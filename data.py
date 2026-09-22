"""네이버 금융에서 시세와 일봉을 가져와 52주 지표를 계산합니다.

- 실시간 시세: polling.finance.naver.com (여러 종목을 한 번에 조회)
- 일봉(약 1년치): fchart.stock.naver.com, 실패하면 api.finance.naver.com/siseJson

개인 참고용입니다. 네이버 응답 형식이 바뀌면 parse_* 함수만 고치면 됩니다.
환경변수 STOCK_MOCK=1 로 실행하면 인터넷 없이 가짜 데이터로 화면을 확인할 수 있어요.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone

import pandas as pd
import requests

KST = timezone(timedelta(hours=9))
MOCK = os.environ.get("STOCK_MOCK") == "1"

FCHART_URL = "https://fchart.stock.naver.com/sise.nhn"
SISEJSON_URL = "https://api.finance.naver.com/siseJson.naver"
POLLING_URL = "https://polling.finance.naver.com/api/realtime"
AUTOCOMPLETE_URL = "https://ac.stock.naver.com/ac"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/",
}
COLUMNS = ["date", "open", "high", "low", "close", "volume"]

session = requests.Session()
session.headers.update(HEADERS)


# ─────────────────────────── 공통 도우미 ───────────────────────────
def now_kst() -> datetime:
    return datetime.now(KST)


def decode(raw: bytes) -> str:
    for enc in ("utf-8", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def to_num(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=COLUMNS)


def rows_to_frame(rows) -> pd.DataFrame:
    df = pd.DataFrame(list(rows), columns=COLUMNS)
    if df.empty:
        return empty_frame()
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d", errors="coerce")
    for col in COLUMNS[1:]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["date", "close", "high", "low"])
    df = df[df["close"] > 0]
    return df.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i : i + size]


# ─────────────────────────── 응답 해석 ───────────────────────────
def parse_fchart(text: str) -> tuple[str | None, pd.DataFrame]:
    """fchart XML → (종목명, 일봉). item 형식: 날짜|시가|고가|저가|종가|거래량"""
    m = re.search(r'<chartdata[^>]*\bname="([^"]*)"', text)
    name = m.group(1).strip() if m else None
    rows = []
    for item in re.findall(r'<item\s+data="([^"]+)"', text):
        parts = item.split("|")
        if len(parts) >= 6:
            rows.append(parts[:6])
    return name, rows_to_frame(rows)


def parse_sisejson(text: str) -> pd.DataFrame:
    """siseJson 응답 → 일봉. 행 형식: ["20240102", 시가, 고가, 저가, 종가, 거래량, ...]"""
    num = r"\s*,\s*([\d.]+)"
    pattern = r"\[\s*[\"'](\d{8})[\"']" + num * 5
    return rows_to_frame(re.findall(pattern, text))


def parse_polling(text: str) -> dict[str, dict]:
    """polling 실시간 응답 → {코드: {price, prev, high, low, status}}"""
    data = json.loads(text)
    out: dict[str, dict] = {}
    areas = (data.get("result") or {}).get("areas") or []
    for area in areas:
        for item in area.get("datas") or []:
            code = str(item.get("cd", "")).strip().zfill(6)
            price = to_num(item.get("nv"))
            if not code.strip("0") or not price:
                continue
            out[code] = {
                "price": price,
                "prev": to_num(item.get("pcv")) or to_num(item.get("sv")),
                "high": to_num(item.get("hv")),
                "low": to_num(item.get("lv")),
                "status": item.get("ms"),
            }
    return out


# ─────────────────────────── 네트워크 조회 ───────────────────────────
def fetch_history(code: str, count: int = 300) -> tuple[str | None, pd.DataFrame, str | None]:
    """(네이버 종목명, 일봉, 오류메시지)"""
    if MOCK:
        return None, mock_history(code, count), None
    error = None
    try:
        r = session.get(
            FCHART_URL,
            params={"symbol": code, "timeframe": "day", "count": count, "requestType": 0},
            timeout=8,
        )
        r.raise_for_status()
        name, df = parse_fchart(decode(r.content))
        if not df.empty:
            return name, df, None
        error = "fchart 응답에 일봉이 없음"
    except requests.RequestException as exc:
        error = f"fchart 실패: {exc.__class__.__name__}"

    try:
        end = now_kst().date()
        start = end - timedelta(days=int(count * 1.6))
        r = session.get(
            SISEJSON_URL,
            params={
                "symbol": code,
                "requestType": 1,
                "startTime": start.strftime("%Y%m%d"),
                "endTime": end.strftime("%Y%m%d"),
                "timeframe": "day",
            },
            timeout=8,
        )
        r.raise_for_status()
        df = parse_sisejson(decode(r.content))
        if not df.empty:
            return None, df, None
        error = (error or "") + " / siseJson 응답에 일봉이 없음"
    except requests.RequestException as exc:
        error = (error or "") + f" / siseJson 실패: {exc.__class__.__name__}"
    return None, empty_frame(), error


def fetch_histories(codes, workers: int = 8) -> dict[str, tuple]:
    codes = list(codes)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = pool.map(fetch_history, codes)
    return dict(zip(codes, results))


def fetch_quotes(codes) -> tuple[dict[str, dict], str | None]:
    """({코드: 시세}, 오류메시지). 일부 실패해도 받은 만큼 돌려줍니다."""
    codes = list(codes)
    if MOCK:
        return {c: mock_quote(c) for c in codes}, None
    out: dict[str, dict] = {}
    errors = []
    for part in chunks(codes, 40):
        try:
            r = session.get(POLLING_URL, params={"query": "SERVICE_ITEM:" + ",".join(part)}, timeout=8)
            r.raise_for_status()
            out.update(parse_polling(decode(r.content)))
        except (requests.RequestException, ValueError) as exc:
            errors.append(exc.__class__.__name__)
    return out, ("실시간 시세 조회 실패: " + ", ".join(sorted(set(errors)))) if errors else None


# ─────────────────────────── 지표 계산 ───────────────────────────
def compute_metrics(hist: pd.DataFrame, quote: dict | None, today: date | None = None) -> dict:
    """현재가·등락률·52주 최고/최저·괴리율·신고가 경과일·정배열 여부."""
    today = today or now_kst().date()
    res = {
        "price": None, "prev": None, "change": None, "high52": None, "low52": None,
        "gap": None, "to_high": None, "pos": None, "days_since_high": None,
        "aligned": None, "source": None,
    }
    if hist is None or hist.empty:
        return res

    last_is_today = hist["date"].iloc[-1].date() == today
    closes = hist["close"].astype(float).reset_index(drop=True)

    if quote and quote.get("price"):
        price = float(quote["price"])
        prev = quote.get("prev")
        if not prev:
            prev = closes.iloc[-2] if (last_is_today and len(closes) > 1) else closes.iloc[-1]
        source = "실시간"
    else:
        price = float(closes.iloc[-1])
        prev = closes.iloc[-2] if len(closes) > 1 else None
        source = "일봉"

    window = hist[hist["date"].dt.date >= today - timedelta(days=364)]
    if window.empty:
        window = hist.tail(250)
    hi_idx = window["high"].idxmax()
    high52 = float(window.loc[hi_idx, "high"])
    days_since = int((window["date"] > window.loc[hi_idx, "date"]).sum())
    low52 = float(window["low"].min())

    q_high = quote.get("high") if quote else None
    q_low = quote.get("low") if quote else None
    if q_high and q_high > high52:
        high52, days_since = float(q_high), 0
    if price > high52:
        high52, days_since = price, 0
    if q_low and q_low < low52:
        low52 = float(q_low)
    low52 = min(low52, price)

    if last_is_today:
        closes.iloc[-1] = price
    aligned = None
    if len(closes) >= 120:
        ma20, ma60, ma120 = (closes.tail(n).mean() for n in (20, 60, 120))
        aligned = bool(price > ma20 > ma60 > ma120)

    res.update(
        price=price,
        prev=prev,
        change=(price / prev - 1) * 100 if prev else None,
        high52=high52,
        low52=low52,
        gap=(price / high52 - 1) * 100,
        to_high=(high52 / price - 1) * 100,
        pos=((price - low52) / (high52 - low52) * 100) if high52 > low52 else 100.0,
        days_since_high=days_since,
        aligned=aligned,
        source=source,
    )
    return res


def normalize_name(name: str | None) -> str:
    return re.sub(r"[\s()㈜·.&-]", "", name or "").lower()


def name_matches(expected: str, naver_name: str | None) -> bool | None:
    if not naver_name:
        return None
    a, b = normalize_name(expected), normalize_name(naver_name)
    return a in b or b in a


def build_table(stocks: list[dict], histories: dict, quotes: dict) -> pd.DataFrame:
    rows = []
    for s in stocks:
        naver_name, hist, error = histories.get(s["code"], (None, empty_frame(), "조회 안 됨"))
        metrics = compute_metrics(hist, quotes.get(s["code"]))
        rows.append({
            **s,
            **metrics,
            "naver_name": naver_name,
            "name_ok": name_matches(s["name"], naver_name),
            "error": error if metrics["price"] is None else None,
        })
    return pd.DataFrame(rows)


def leading_groups(df: pd.DataFrame, recent_days: int, min_count: int = 2):
    """최근 recent_days 거래일 안에 52주 신고가를 쓴 종목이 min_count개 이상인 분류."""
    hot = df[df["days_since_high"].notna() & (df["days_since_high"] <= recent_days)]
    out = []
    for group, sub in hot.groupby("group", sort=False):
        if len(sub) >= min_count:
            out.append((group, sub.sort_values("gap", ascending=False)))
    out.sort(key=lambda x: (-len(x[1]), x[0]))
    return out


def market_status(quotes: dict) -> str:
    statuses = [q.get("status") for q in quotes.values() if q.get("status")]
    if statuses:
        top = max(set(statuses), key=statuses.count)
        return {"OPEN": "장중", "CLOSE": "장 마감"}.get(top, top)
    now = now_kst()
    if now.weekday() < 5 and time(9, 0) <= now.time() <= time(15, 30):
        return "장중(추정)"
    return "장 마감(추정)"


# ─────────────────────────── 가짜 데이터(테스트용) ───────────────────────────
def _rng(code: str) -> random.Random:
    return random.Random(int(hashlib.md5(code.encode()).hexdigest()[:8], 16))


def mock_history(code: str, count: int = 300) -> pd.DataFrame:
    rng = _rng(code)
    dates = pd.bdate_range(end=now_kst().date(), periods=count)
    price = rng.uniform(3_000, 200_000)
    drift = rng.uniform(-0.002, 0.004)
    rows = []
    for d in dates:
        o = price
        c = max(100.0, price * (1 + rng.gauss(drift, 0.025)))
        hi = max(o, c) * (1 + abs(rng.gauss(0, 0.01)))
        lo = min(o, c) * (1 - abs(rng.gauss(0, 0.01)))
        rows.append((d.strftime("%Y%m%d"), round(o), round(hi), round(lo), round(c), rng.randint(10_000, 5_000_000)))
        price = c
    return rows_to_frame(rows)


def mock_quote(code: str) -> dict:
    hist = mock_history(code)
    last = hist.iloc[-1]
    return {"price": float(last["close"]), "prev": float(hist.iloc[-2]["close"]),
            "high": float(last["high"]), "low": float(last["low"]), "status": "OPEN"}
