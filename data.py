"""네이버 금융에서 시세와 일봉을 가져와 52주 지표를 계산합니다.

- 실시간 시세: polling.finance.naver.com (여러 종목을 한 번에 조회)
- 일봉(약 1년치): fchart.stock.naver.com, 실패하면 api.finance.naver.com/siseJson
- 해외 종목(코드가 6자리 숫자가 아닌 것): 야후 파이낸스(yfinance) 일봉, 15분 안팎 지연
- 시가총액: 상장주식수(하루 한 번 조회) × 현재가로 실시간 계산. 해외는 환율로 원화 환산도 함께
- 코스피·코스닥 요약(지수·상승/보합/하락 종목 수·투자자별 순매수): stock.naver.com 지수 API
- 투자자별 매매동향: 시장 전체는 기관 세부(연기금·투신 등)까지, 종목별은 개인·외국인·기관
  (2026년 9월 네이버 PC 금융 페이지 개편으로 예전 HTML 페이지 대신 JSON API를 씁니다)

개인 참고용입니다. 네이버 응답 형식이 바뀌면 parse_* 함수만 고치면 됩니다.
환경변수 STOCK_MOCK=1 로 실행하면 인터넷 없이 가짜 데이터로 화면을 확인할 수 있어요.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import time as _time
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


_KR_CODE = re.compile(r"\d[0-9A-Z]{5}")


def is_kr(code: str) -> bool:
    """6자리 국내 코드(005930, 새 형식 0009K0 포함)면 국내, 그 외(NVDA, 6857.T 등)는 해외."""
    return bool(code) and bool(_KR_CODE.fullmatch(code))


_SUFFIX_MARKET = {".T": ("JP", "JPY"), ".PA": ("FR", "EUR"), ".AS": ("NL", "EUR"),
                  ".DE": ("DE", "EUR"), ".AX": ("AU", "AUD"), ".HK": ("HK", "HKD"), ".TW": ("TW", "TWD"),
                  ".HE": ("FI", "EUR"), ".L": ("GB", "GBp"), ".SZ": ("CN", "CNY"), ".SS": ("CN", "CNY"),
                  ".SW": ("CH", "CHF")}


def market_of(code: str) -> tuple[str, str]:
    """(시장, 통화)"""
    if is_kr(code):
        return "KR", "KRW"
    for suffix, info in _SUFFIX_MARKET.items():
        if code.upper().endswith(suffix):
            return info
    return "US", "USD"


def quote_url(code: str) -> str:
    if is_kr(code):
        return f"https://m.stock.naver.com/domestic/stock/{code}/total"
    return f"https://finance.yahoo.com/quote/{code}"


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
def fetch_history(code: str, count: int = 520) -> tuple[str | None, pd.DataFrame, str | None]:
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


def normalize_yf(hist: pd.DataFrame) -> pd.DataFrame:
    """yfinance history() 결과 → 공통 일봉 형식(date, open, high, low, close, volume)."""
    if hist is None or hist.empty:
        return empty_frame()
    if isinstance(hist.columns, pd.MultiIndex):
        hist = hist.copy()
        hist.columns = [c[0] if c[0] in ("Open", "High", "Low", "Close", "Volume") else c[-1]
                        for c in hist.columns]
    idx = pd.DatetimeIndex(hist.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    df = pd.DataFrame({
        "date": idx.normalize(),
        "open": pd.to_numeric(hist.get("Open"), errors="coerce").values,
        "high": pd.to_numeric(hist.get("High"), errors="coerce").values,
        "low": pd.to_numeric(hist.get("Low"), errors="coerce").values,
        "close": pd.to_numeric(hist.get("Close"), errors="coerce").values,
        "volume": pd.to_numeric(hist.get("Volume"), errors="coerce").values,
    })
    df = df.dropna(subset=["close", "high", "low"])
    df = df[df["close"] > 0]
    return df.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def fetch_history_overseas(ticker: str, count: int = 520) -> tuple[str | None, pd.DataFrame, str | None]:
    """야후 파이낸스 일봉. (종목명은 확인하지 않으므로 None)"""
    if MOCK:
        return None, mock_history(ticker, count), None
    try:
        import yfinance as yf
    except ImportError:
        return None, empty_frame(), "yfinance가 설치되지 않음(requirements.txt 확인)"
    try:
        hist = yf.Ticker(ticker).history(period="26mo", interval="1d", auto_adjust=False)
    except Exception as exc:  # 야후 쪽 일시 오류·요청 제한
        return None, empty_frame(), f"야후 조회 실패: {exc.__class__.__name__}"
    df = normalize_yf(hist)
    return (None, df, None) if not df.empty else (None, df, "야후 응답에 일봉이 없음(티커 확인)")


# 시장 지표. kind: index(60일선 배지), fx·commodity(20일 변화율). scale: 표시 배율(100엔 기준 등)
INDEXES = [
    {"name": "코스피", "symbol": "KOSPI", "source": "naver", "kind": "index"},
    {"name": "코스닥", "symbol": "KOSDAQ", "source": "naver", "kind": "index"},
    {"name": "나스닥", "symbol": "^IXIC", "source": "yahoo", "kind": "index"},
    {"name": "니케이225", "symbol": "^N225", "source": "yahoo", "kind": "index"},
    {"name": "원/달러", "symbol": "USDKRW=X", "source": "yahoo", "kind": "fx"},
    {"name": "원/엔(100엔)", "symbol": "JPYKRW=X", "source": "yahoo", "kind": "fx", "scale": 100},
    {"name": "WTI 유가", "symbol": "CL=F", "source": "yahoo", "kind": "commodity", "unit": "$"},
    {"name": "브렌트 유가", "symbol": "BZ=F", "source": "yahoo", "kind": "commodity", "unit": "$"},
]


def fetch_index_histories() -> dict[str, tuple[pd.DataFrame, str | None]]:
    """코스피·코스닥은 네이버 일봉(장중 갱신), 나머지는 야후 일봉(15분 안팎 지연)."""

    def one(idx):
        if idx["source"] == "naver":
            _, df, err = fetch_history(idx["symbol"], count=300)
        else:
            _, df, err = fetch_history_overseas(idx["symbol"])
        scale = idx.get("scale", 1)
        if scale != 1 and not df.empty:
            df = df.copy()
            df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]] * scale
        return idx["symbol"], (df, err)

    with ThreadPoolExecutor(max_workers=4) as pool:
        return dict(pool.map(one, INDEXES))


def index_summary(df: pd.DataFrame) -> dict | None:
    """현재 지수, 등락률, 60일선과의 거리."""
    if df is None or len(df) < 2:
        return None
    close = df["close"].astype(float)
    last, prev = float(close.iloc[-1]), float(close.iloc[-2])
    ma60 = float(close.tail(60).mean()) if len(close) >= 60 else None
    chg20 = (last / float(close.iloc[-21]) - 1) * 100 if len(close) > 21 else None
    return {
        "chg20": chg20,
        "last": last,
        "change": (last / prev - 1) * 100,
        "ma60": ma60,
        "above60": (last > ma60) if ma60 else None,
        "dist60": (last / ma60 - 1) * 100 if ma60 else None,
        "date": df["date"].iloc[-1],
    }


def fetch_histories(codes, workers: int = 8) -> dict[str, tuple]:
    """국내·해외 섞인 코드 목록을 받아 각각 알맞은 곳에서 일봉을 가져옵니다."""
    codes = list(codes)

    def one(code):
        return fetch_history(code) if is_kr(code) else fetch_history_overseas(code)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = pool.map(one, codes)
    return dict(zip(codes, results))


def fetch_quotes(codes) -> tuple[dict[str, dict], str | None]:
    """({코드: 시세}, 오류메시지). 일부 실패해도 받은 만큼 돌려줍니다."""
    codes = [c for c in codes if is_kr(c)]
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
def compute_metrics(hist: pd.DataFrame, quote: dict | None, today: date | None = None, bo_mode: str = "line",
                    monthly: pd.DataFrame | None = None) -> dict:
    """현재가·등락률·52주 최고/최저·괴리율·신고가 경과일·정배열 여부."""
    today = today or now_kst().date()
    res = {
        "price": None, "prev": None, "change": None, "high52": None, "low52": None,
        "gap": None, "to_high": None, "pos": None, "days_since_high": None,
        "aligned": None, "source": None,
        "atr_pct": None, "ret_1m": None, "ret_3m": None, "ret_6m": None, "rs_raw": None,
        **{k: None for k in BO_KEYS},
        **{k: None for k in NH_KEYS},
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

    # 현재가·장중 고가를 오늘 일봉에 반영한 사본으로 ATR·수익률·돌파 유지를 계산해요.
    h = hist[["date", "high", "low", "close"]].astype({"high": float, "low": float, "close": float}).reset_index(drop=True)
    if last_is_today:
        h.loc[h.index[-1], "close"] = price
        if q_high:
            h.loc[h.index[-1], "high"] = max(float(h["high"].iloc[-1]), float(q_high))
        if q_low:
            h.loc[h.index[-1], "low"] = min(float(h["low"].iloc[-1]), float(q_low))
    res["atr_pct"] = atr_pct(h, ATR_DAYS)
    rets = {}
    for key, n in (("ret_1m", 21), ("ret_3m", 63), ("ret_6m", 126), ("ret_9m", 189), ("ret_12m", 250)):
        rets[key] = (price / float(h["close"].iloc[-1 - n]) - 1) * 100 if len(h) > n else None
    res.update(ret_1m=rets["ret_1m"], ret_3m=rets["ret_3m"], ret_6m=rets["ret_6m"])
    res["rs_raw"] = rs_raw_score(rets)
    res.update(breakout_hold(h, bo_mode))
    res.update(newhigh_flags(h, monthly))
    return res


# ─────────────────────────── RS · ATR · 신고가 돌파 유지 ───────────────────────────
ATR_DAYS = 20          # 20거래일 ATR
BO_KEYS = ("bo_date", "bo_level", "bo_days", "bo_status", "bo_vs", "bo_break_date", "bo_held", "bo_history")
BO_MODES = {
    "line": "돌파선(직전 52주 최고가)",
    "close": "첫 신고가일 종가",
}


def atr_pct(h: pd.DataFrame, n: int = ATR_DAYS) -> float | None:
    """최근 n거래일 평균 진폭(ATR)을 현재가 대비 %로. 진폭 = max(고가, 전일종가) − min(저가, 전일종가)."""
    if len(h) < n + 1:
        return None
    prev = h["close"].shift(1)
    tr = pd.concat([h["high"], prev], axis=1).max(axis=1) - pd.concat([h["low"], prev], axis=1).min(axis=1)
    atr = tr.tail(n).mean()
    last = h["close"].iloc[-1]
    return float(atr / last * 100) if last else None


def rs_raw_score(rets: dict) -> float | None:
    """종합 RS 원점수. 오닐(IBD) 방식: 최근 3개월 가중 2배 + 6·9·12개월.
    분기 수익률 기준 = 0.4×3M + 0.2×6M + 0.2×9M + 0.2×12M. 1년이 안 된 종목은 있는 기간만으로 가중치를 나눠요."""
    parts = [(0.4, rets.get("ret_3m")), (0.2, rets.get("ret_6m")), (0.2, rets.get("ret_9m")), (0.2, rets.get("ret_12m"))]
    parts = [(w, r) for w, r in parts if r is not None]
    if not parts:
        return None
    wsum = sum(w for w, _ in parts)
    return sum(w * r for w, r in parts) / wsum


def add_rs_ranks(df: pd.DataFrame) -> pd.DataFrame:
    """RS 점수(1~99). 국내는 보드의 국내 종목끼리, 해외는 해외 종목끼리 수익률 순위를 백분위로 바꿔요."""
    df = df.copy()
    kr = df["code"].map(is_kr)
    for col, raw in (("rs", "rs_raw"), ("rs_1m", "ret_1m"), ("rs_3m", "ret_3m"), ("rs_6m", "ret_6m")):
        df[col] = None
        for mask in (kr, ~kr):
            vals = pd.to_numeric(df.loc[mask, raw], errors="coerce")
            if vals.notna().sum() >= 2:
                pct = vals.rank(pct=True, method="average")
                df.loc[mask, col] = (pct * 98 + 1).round().where(vals.notna())
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def breakout_hold(h: pd.DataFrame, mode: str = "line", lookback: int = 250) -> dict:
    """직전 52주 최고가를 기준으로 '유지' 또는 '이탈'이 며칠째인지.

    - 유지: 종가가 직전 52주 최고가(그날 이전 250거래일 최고가)를 넘어선 날(돌파일)을 1일째로,
      그 뒤 종가가 한 번도 그 가격 아래로 내려가지 않은 거래일 수. 기준가 = 돌파한 직전 52주 최고가
    - 이탈: 유지 중이 아니면 기준가 = 지금의 52주 최고가. 그 가격 위에서 마감한 마지막 날 다음 날
      (위에서 마감한 적이 없으면 최고가를 찍은 날)을 1일째로 센 거래일 수
    """
    out = {k: None for k in BO_KEYS}
    if h is None or len(h) < 60:
        return out
    high = h["high"].values
    close = h["close"].values
    dates = h["date"]
    n = len(h)
    minp = lookback if n >= lookback + 20 else 60
    prior = h["high"].shift(1).rolling(lookback, min_periods=minp).max().values

    runs = []          # 유지 구간: 돌파일, 돌파선, 이탈일
    cur = None
    for t in range(n):
        if cur is not None:
            if close[t] < cur["level"]:
                cur["broken"] = t
                runs.append(cur)
                cur = None
            continue
        if prior[t] == prior[t] and close[t] > prior[t]:    # 종가로 직전 52주 최고가 돌파
            cur = {"start": t, "level": float(prior[t]), "broken": None}
    if cur is not None:
        runs.append(cur)

    last = n - 1
    hist_rows = [{
        "돌파일": dates.iloc[r["start"]].date(),
        "돌파한 직전 52주 최고가": r["level"],
        "유지": f"{(last - r['start'] + 1) if r['broken'] is None else (r['broken'] - r['start'])}일",
        "이탈일": "-" if r["broken"] is None else dates.iloc[r["broken"]].date(),
    } for r in runs[-6:]][::-1]

    if cur is not None:                                   # 지금 유지 중
        out.update(
            bo_status="유지", bo_level=cur["level"], bo_date=dates.iloc[cur["start"]].date(),
            bo_days=last - cur["start"] + 1, bo_held=last - cur["start"] + 1,
            bo_vs=(close[-1] / cur["level"] - 1) * 100,
        )
    else:                                                 # 지금의 52주 최고가 아래
        win = h.tail(lookback)
        peak_i = int(win["high"].values.argmax()) + (n - len(win))
        # 같은 최고가가 여러 번이면 가장 최근 날
        ref = float(high[peak_i])
        same = [i for i in range(n - len(win), n) if high[i] >= ref]
        peak_i = same[-1] if same else peak_i
        above = [i for i in range(peak_i, n) if close[i] >= ref]
        first_below = (above[-1] + 1) if above else peak_i
        out.update(
            bo_status="이탈", bo_level=ref, bo_date=dates.iloc[peak_i].date(),
            bo_days=max(1, last - first_below + 1),
            bo_vs=(close[-1] / ref - 1) * 100,
            bo_break_date=dates.iloc[min(first_below, last)].date(),
            bo_held=(runs[-1]["broken"] - runs[-1]["start"]) if runs and runs[-1]["broken"] is not None else None,
        )
    out["bo_history"] = hist_rows
    return out


def normalize_name(name: str | None) -> str:
    return re.sub(r"[\s()㈜·.&-]", "", name or "").lower()


def name_matches(expected: str, naver_name: str | None) -> bool | None:
    if not naver_name:
        return None
    a, b = normalize_name(expected), normalize_name(naver_name)
    return a in b or b in a


# ─────────────────────────── 시가총액 ───────────────────────────
MARKET_SUM_URL = "https://finance.naver.com/sise/sise_market_sum.naver"
ITEM_MAIN_URL = "https://finance.naver.com/item/main.naver"
FX_TICKERS = {"USD": "USDKRW=X", "JPY": "JPYKRW=X", "EUR": "EURKRW=X", "AUD": "AUDKRW=X",
              "GBP": "GBPKRW=X", "HKD": "HKDKRW=X", "TWD": "TWDKRW=X", "CNY": "CNYKRW=X", "CHF": "CHFKRW=X"}


def _strip_tags(html_text: str) -> str:
    return re.sub(r"<[^>]+>", "", html_text).replace("&nbsp;", " ").strip()


def parse_market_sum(text: str) -> tuple[dict[str, int], int]:
    """네이버 시가총액 순위 페이지 → ({코드: 상장주식수}, 마지막 페이지 번호)."""
    last = re.search(r'class="pgRR".*?page=(\d+)', text, re.S)
    last_page = int(last.group(1)) if last else 1
    table = re.search(r'<table[^>]*class="type_2"[^>]*>(.*?)</table>', text, re.S)
    if table:
        text = table.group(1)
    header = [_strip_tags(h) for h in re.findall(r"<th[^>]*>(.*?)</th>", text, re.S)]
    idx = next((i for i, h in enumerate(header) if "상장주식수" in h), None)
    if idx is None:
        return {}, last_page
    out: dict[str, int] = {}
    for row in re.split(r"<tr[\s>]", text):
        m = re.search(r"code=(\d{6})", row)
        if not m:
            continue
        tds = [_strip_tags(t) for t in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(tds) <= idx:
            continue
        shares = to_num(tds[idx])
        if shares:
            out[m.group(1)] = int(shares * 1000)  # 표 단위: 천주
    return out, last_page


def _market_sum_page(sosok: int, page: int) -> tuple[dict[str, int], int]:
    try:
        r = session.get(MARKET_SUM_URL, params={"sosok": sosok, "page": page}, timeout=8)
        r.raise_for_status()
        return parse_market_sum(decode(r.content))
    except requests.RequestException:
        return {}, 1


def _shares_from_item_page(code: str) -> int | None:
    """종목 메인 페이지의 '상장주식수' (순위 페이지에 없을 때만 사용)."""
    try:
        r = session.get(ITEM_MAIN_URL, params={"code": code}, timeout=8)
        r.raise_for_status()
        m = re.search(r"상장주식수[\s\S]{0,300}?<em[^>]*>\s*([\d,]{4,})\s*</em>", decode(r.content))
        return int(m.group(1).replace(",", "")) if m else None
    except requests.RequestException:
        return None


NAVER_MOBILE = "https://m.stock.naver.com/api/stock"
SEC_HEADERS = {
    "User-Agent": os.environ.get("SEC_CONTACT", "ValueChainBoard personal-research contact@example.com"),
    "Accept-Encoding": "gzip, deflate",
}
ADR_RATIO = {"TSM": 5.0, "NGG": 5.0, "LI": 2.0, "NTES": 5.0}  # 미국 ADR 1주 = 원주 N주. 원주 수를 ADR 수로 환산


def parse_korean_amount(text: str | None) -> float | None:
    """'472조 7,478억' / '8,718억' → 원."""
    if not text:
        return None
    jo = re.search(r"([\d,.]+)\s*조", text)
    eok = re.search(r"([\d,.]+)\s*억", text)
    total = (to_num(jo.group(1)) or 0) * 1e12 if jo else 0.0
    total += (to_num(eok.group(1)) or 0) * 1e8 if eok else 0.0
    return total or None


def _naver_mobile_shares(code: str) -> int | None:
    """네이버 모바일 API의 시가총액 ÷ 종가로 상장주식수를 역산 (순위표·종목페이지 실패 시)."""
    try:
        info = session.get(f"{NAVER_MOBILE}/{code}/integration", timeout=8).json()
        mv = next((i.get("value") for i in info.get("totalInfos", []) if i.get("code") == "marketValue"), None)
        cap = parse_korean_amount(mv)
        basic = session.get(f"{NAVER_MOBILE}/{code}/basic", timeout=8).json()
        price = to_num(basic.get("closePrice"))
        return int(round(cap / price)) if cap and price else None
    except (requests.RequestException, ValueError, AttributeError):
        return None


def _detail_api_shares(code: str) -> int | None:
    """stock.naver.com 종목 상세 API의 상장주식수(listedStockCnt). 2026년 9월 개편 뒤 가장 확실한 출처예요."""
    info = _get_json([f"{STOCK_API}/domestic/detail/{code}/detail"], params={"codeType": "KRX"}, timeout=8)
    if not isinstance(info, dict):
        return None
    n = _num(info.get("listedStockCnt"))
    return int(n) if n and n > 0 else None


def fetch_kr_shares(codes) -> tuple[dict[str, int], dict]:
    """국내 상장주식수: ① 네이버 종목 상세 API ② 시가총액 순위표 ③ 종목 페이지 ④ 모바일 API 순서로 채워요.
    (결과, 출처별 개수) 를 돌려줘요."""
    codes = [c for c in codes if is_kr(c)]
    diag = {"total": len(codes), "상세API": 0, "순위표": 0, "종목페이지": 0, "모바일": 0}
    if MOCK:
        return {c: _rng(c).randint(10_000_000, 900_000_000) for c in codes}, {**diag, "순위표": len(codes)}
    result: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for code, n in zip(codes, pool.map(_detail_api_shares, codes)):
            if n:
                result[code] = n
    diag["상세API"] = len(result)
    missing = [c for c in codes if c not in result]
    if len(missing) > 20:  # 순위표는 페이지를 많이 받아야 해서, 많이 비었을 때만
        shares: dict[str, int] = {}
        jobs = []
        for sosok in (0, 1):  # 0: 코스피, 1: 코스닥
            first, last = _market_sum_page(sosok, 1)
            shares.update(first)
            jobs += [(sosok, p) for p in range(2, last + 1)] if first else []
        with ThreadPoolExecutor(max_workers=8) as pool:
            for part, _ in pool.map(lambda job: _market_sum_page(*job), jobs):
                shares.update(part)
        for c in missing:
            if c in shares:
                result[c] = shares[c]
                diag["순위표"] += 1
    for label, fn in (("종목페이지", _shares_from_item_page), ("모바일", _naver_mobile_shares)):
        missing = [c for c in codes if c not in result]
        if not missing:
            break
        with ThreadPoolExecutor(max_workers=6) as pool:
            for code, n in zip(missing, pool.map(fn, missing)):
                if n:
                    result[code] = n
                    diag[label] += 1
    return result, diag


def _sec_ticker_map() -> dict[str, str]:
    r = requests.get("https://www.sec.gov/files/company_tickers.json", headers=SEC_HEADERS, timeout=15)
    r.raise_for_status()
    return {str(v["ticker"]).upper(): f"{int(v['cik_str']):010d}" for v in r.json().values()}


def _sec_concept(cik: str, taxonomy: str, concept: str) -> list[dict]:
    url = f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/{taxonomy}/{concept}.json"
    try:
        r = requests.get(url, headers=SEC_HEADERS, timeout=15)
        if r.status_code != 200:
            return []
        units = r.json().get("units", {})
        return units.get("shares") or []
    except (requests.RequestException, ValueError):
        return []


def _sec_shares(cik: str) -> int | None:
    """SEC 공시(XBRL)의 최신 발행주식수. 표지(dei) 값 → 희석 가중평균 주식수 순으로 시도."""
    recent = (now_kst() - timedelta(days=500)).strftime("%Y-%m-%d")
    vals = [v for v in _sec_concept(cik, "dei", "EntityCommonStockSharesOutstanding") if v.get("end", "") >= recent]
    if vals:
        latest = max(vals, key=lambda v: (v.get("filed", ""), v["end"]))
        # 같은 공시·같은 날짜에 주식 종류(Class A/B/C)별로 따로 적힌 경우 합쳐요
        same = {v["val"] for v in vals if v.get("accn") == latest.get("accn") and v["end"] == latest["end"]}
        return int(sum(same))
    for concept in ("WeightedAverageNumberOfDilutedSharesOutstanding", "CommonStockSharesOutstanding"):
        vals = [v for v in _sec_concept(cik, "us-gaap", concept) if v.get("end", "") >= recent]
        if vals:
            # 같은 날짜면 기간이 짧은(분기) 값을 우선
            latest = max(vals, key=lambda v: (v["end"], v.get("start") or "", v.get("filed", "")))
            return int(latest["val"])
    return None


def _parse_amount(text) -> tuple[float | None, bool]:
    """'4조 3,211억 USD' / '350.2B' / 12345 → (값, 원화 여부)."""
    if text is None:
        return None, False
    if isinstance(text, (int, float)):
        return float(text), False
    t = str(text)
    krw = "원" in t or "KRW" in t
    if "조" in t or "억" in t:
        return parse_korean_amount(t), krw
    m = re.search(r"([\d,.]+)\s*([TBMK])\b", t, re.I)
    if m:
        mult = {"T": 1e12, "B": 1e9, "M": 1e6, "K": 1e3}[m.group(2).upper()]
        return (to_num(m.group(1)) or 0) * mult or None, krw
    return _num(t), krw


def _find_fields(node, found: dict):
    """해외 기본정보 응답 안에서 상장주식수·시가총액·현재가를 찾아요(필드 위치가 바뀌어도 되도록)."""
    if isinstance(node, dict):
        code = str(node.get("code") or node.get("key") or "")
        if code in ("marketValue", "marketCap", "시가총액", "시총") and "value" in node:
            found.setdefault("cap", node.get("value"))
        for k, v in node.items():
            kl = k.lower()
            if kl in ("countoflistedstock", "listedstockcnt", "listedshares", "sharesoutstanding"):
                found.setdefault("shares", v)
            elif kl in ("marketvalue", "marketcap", "marketsum") and not isinstance(v, (dict, list)):
                found.setdefault("cap", v)
            elif kl == "closeprice" and "price" not in found:
                found["price"] = v
            else:
                _find_fields(v, found)
    elif isinstance(node, list):
        for v in node:
            _find_fields(v, found)


def _naver_codes(ticker: str) -> list[str]:
    """야후 티커 → 네이버 해외주식 로이터 코드 후보. 네이버는 미국·일본·중국·홍콩(·베트남)만 다뤄요."""
    t = ticker.upper()
    if t.endswith((".T", ".HK", ".SZ", ".SS")):
        return [t]
    if "." not in t:
        return [f"{t}.O", t, f"{t}.N", f"{t}.K"]
    return []


def _naver_foreign_shares(ticker: str, fx: dict | None = None) -> int | None:
    for rc in _naver_codes(ticker):
        info = _get_json([f"{STOCK_API}/securityService/stock/{rc}/basic"], timeout=6)
        if not isinstance(info, dict) or not info:
            continue
        found: dict = {}
        _find_fields(info, found)
        n = _num(found.get("shares"))
        if n and n > 1000:
            return int(n)
        cap, is_krw = _parse_amount(found.get("cap"))
        price = _num(found.get("price"))
        if cap and price:
            if is_krw:
                rate = (fx or {}).get(market_of(ticker)[1])
                if not rate:
                    continue
                cap = cap / rate
            return int(round(cap / price))
    return None


def _yahoo_shares(ticker: str) -> int | None:
    """야후 발행주식수(클라우드 서버에서는 막히는 경우가 많아 마지막에 시도)."""
    def job():
        import yfinance as yf
        n = yf.Ticker(ticker).fast_info.get("shares")
        return int(n) if n else None
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(job).result(timeout=12)
    except Exception:  # 차단·시간 초과·미지원 모두 '없음'으로
        return None


def fetch_overseas_shares(tickers) -> tuple[dict[str, int], dict]:
    """해외 발행주식수: ① 미국 상장은 SEC 공시 ② 네이버 해외주식(미국·일본·중국·홍콩) ③ 야후 순서로 채워요."""
    tickers = [t for t in tickers if not is_kr(t)]
    us = [t for t in tickers if market_of(t)[0] == "US"]
    diag = {"total": len(tickers), "SEC": 0, "네이버": 0, "야후": 0, "미국 외": len(tickers) - len(us)}
    if MOCK:
        return {t: _rng(t).randint(50_000_000, 5_000_000_000) for t in tickers}, {**diag, "SEC": len(us)}
    result: dict[str, int] = {}
    try:
        cik_map = _sec_ticker_map()
    except (requests.RequestException, ValueError):
        cik_map = {}
    targets = [(t, cik_map[t.upper()]) for t in us if t.upper() in cik_map]

    def one(item):
        t, cik = item
        n = _sec_shares(cik)
        return (t, n / ADR_RATIO.get(t, 1.0) if n else None)

    with ThreadPoolExecutor(max_workers=4) as pool:  # SEC 요청 제한(초당 10회) 안쪽으로
        for t, n in pool.map(one, targets):
            if n:
                result[t] = int(n)
    diag["SEC"] = len(result)

    missing = [t for t in tickers if t not in result]
    fx = fetch_fx() if any(_naver_codes(t) for t in missing) else {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for t, n in zip(missing, pool.map(lambda t: _naver_foreign_shares(t, fx), missing)):
            if n:
                result[t] = n
                diag["네이버"] += 1
    missing = [t for t in tickers if t not in result]
    with ThreadPoolExecutor(max_workers=3) as pool:
        for t, n in zip(missing, pool.map(_yahoo_shares, missing)):
            if n:
                result[t] = n
                diag["야후"] += 1
    return result, diag


def fetch_fx() -> dict[str, float]:
    """통화별 원화 환율. GBp(펜스)는 파운드의 1/100."""
    if MOCK:
        rates = {"USD": 1380.0, "JPY": 9.3, "EUR": 1500.0, "AUD": 900.0, "GBP": 1750.0, "HKD": 177.0, "TWD": 43.0,
                 "CNY": 190.0, "CHF": 1560.0}
    else:
        rates = {}
        try:
            import yfinance as yf
            for cur, t in FX_TICKERS.items():
                try:
                    h = yf.Ticker(t).history(period="5d")
                    if not h.empty:
                        rates[cur] = float(h["Close"].dropna().iloc[-1])
                except Exception:
                    continue
        except ImportError:
            pass
        missing = [c for c in FX_TICKERS if c not in rates]
        if missing:
            try:  # 야후가 막히면 공개 환율 API로 보충
                j = requests.get("https://open.er-api.com/v6/latest/KRW", timeout=8).json()
                for cur in missing:
                    per_krw = (j.get("rates") or {}).get(cur)
                    if per_krw:
                        rates[cur] = 1.0 / float(per_krw)
            except (requests.RequestException, ValueError):
                pass
    rates["KRW"] = 1.0
    if "GBP" in rates:
        rates["GBp"] = rates["GBP"] / 100
    return rates


def format_krw(won: float | None) -> str:
    """원 단위 금액 → '1,569.7조' / '8,718억'."""
    if won is None or pd.isna(won):
        return "-"
    eok = won / 1e8
    return f"{eok / 1e4:,.1f}조" if eok >= 1e4 else f"{eok:,.0f}억"


def format_local_cap(value: float | None, currency: str) -> str:
    if value is None or pd.isna(value):
        return "-"
    if currency == "KRW":
        return format_krw(value)
    if currency == "GBp":
        value, currency = value / 100, "GBP"
    for unit, div in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if value >= div:
            return f"{value / div:,.2f}{unit} {currency}"
    return f"{value:,.0f} {currency}"


# ─────────────────────────── 네이버 새 JSON API ───────────────────────────
STOCK_API = "https://stock.naver.com/api"
M_API = "https://m.stock.naver.com/api"
INDEX_POLLING_URL = "https://polling.finance.naver.com/api/realtime/domestic/index"
AUTOCOMPLETE_M_URL = "https://m.stock.naver.com/front-api/search/autoComplete"
JSON_HEADERS = {"Accept": "application/json", "Referer": "https://stock.naver.com/"}


def _get_json(urls, params=None, timeout: int = 8):
    """주소 여러 개를 차례로 시도해 처음 성공한 JSON을 돌려줘요. 모두 실패하면 None."""
    for url in urls:
        try:
            r = session.get(url, params=params, headers=JSON_HEADERS, timeout=timeout)
            if r.status_code != 200:
                continue
            return r.json()
        except (requests.RequestException, ValueError):
            continue
    return None


def _num(v) -> float | None:
    """'+2,746,972' / '-4,822' / '1,234억' 같은 네이버 문자열 숫자 → float."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"[+-]?[\d,]*\.?\d+", str(v))
    return to_num(m.group(0)) if m else None


# ─────────────────────────── 코스피·코스닥 요약 ───────────────────────────
MARKETS = {"코스피": "KOSPI", "코스닥": "KOSDAQ"}


def parse_index_overview(basic: dict | None, integ: dict | None, polling: dict | None = None) -> dict:
    """지수 basic·integration(·polling) 응답 → 화면용 요약."""
    basic = basic or {}
    integ = integ or {}
    pol = ((polling or {}).get("datas") or [{}])[0] if polling else {}
    last = _num(basic.get("closePrice")) or _num(pol.get("closePrice"))
    diff = _num(basic.get("compareToPreviousClosePrice"))
    if diff is None:
        diff = _num(pol.get("compareToPreviousClosePrice"))
    rate = _num(basic.get("fluctuationsRatio"))
    if rate is None:
        rate = _num(pol.get("fluctuationsRatio"))
    direction = ((basic.get("compareToPreviousPrice") or pol.get("compareToPreviousPrice") or {}).get("name") or "")
    if diff is not None and direction == "FALLING" and diff > 0:  # 부호 없이 오는 경우
        diff, rate = -diff, -abs(rate or 0)
    ud = integ.get("upDownStockInfo") or {}
    breadth = {k: _num(ud.get(f)) for k, f in (("상한", "upperCount"), ("상승", "riseCount"), ("보합", "steadyCount"),
                                                ("하락", "fallCount"), ("하한", "lowerCount"))}
    if all(v is None for v in breadth.values()):
        breadth = None
    dt = integ.get("dealTrendInfo") or {}
    deal = {"개인": _num(dt.get("personalValue")), "외국인": _num(dt.get("foreignValue")),
            "기관": _num(dt.get("institutionalValue")), "bizdate": dt.get("bizdate")}
    if all(deal[k] is None for k in INVESTORS):
        deal = None
    elif max(abs(deal[k] or 0) for k in INVESTORS) > 5e6:  # 백만원 단위로 오면 억원으로
        deal.update({k: (deal[k] / 100 if deal[k] is not None else None) for k in INVESTORS})
    pt = integ.get("programTrendInfo") or {}
    program = _num(pt.get("indexTotalReal"))
    status = basic.get("marketStatus") or pol.get("marketStatus")
    return {"last": last, "diff": diff, "rate": rate, "time": basic.get("localTradedAt") or pol.get("localTradedAt"),
            "breadth": breadth, "deal": deal, "program": program, "status": status}


def fetch_market_overview() -> dict[str, dict]:
    """{'코스피': {...}, '코스닥': {...}} — 지수, 전일대비, 등락률, 상승·보합·하락 종목 수, 투자자별 순매수(억원)."""
    if MOCK:
        out = {}
        for name, code in MARKETS.items():
            rng = _rng(code + now_kst().strftime("%Y%m%d%H"))
            last = 7080.92 if code == "KOSPI" else 844.48
            diff = round(last * rng.uniform(-0.015, 0.015), 2)
            total = 930 if code == "KOSPI" else 1770
            rise = rng.randint(int(total * 0.25), int(total * 0.6))
            steady = rng.randint(40, 90)
            f, i = rng.randint(-8000, 8000), rng.randint(-6000, 6000)
            out[name] = {"last": last, "diff": diff, "rate": diff / (last - diff) * 100, "time": now_kst().isoformat(),
                         "breadth": {"상한": rng.randint(0, 5), "상승": rise, "보합": steady,
                                     "하락": total - rise - steady, "하한": rng.randint(0, 2)},
                         "deal": {"개인": -(f + i) + rng.randint(-500, 500), "외국인": f, "기관": i,
                                  "bizdate": now_kst().strftime("%Y%m%d")},
                         "program": rng.randint(-3000, 3000), "status": "OPEN"}
        return out

    def one(item):
        name, code = item
        basic = _get_json([f"{STOCK_API}/securityFe/api/index/{code}/basic", f"{M_API}/index/{code}/basic"])
        integ = _get_json([f"{STOCK_API}/securityFe/api/index/{code}/integration", f"{M_API}/index/{code}/integration"])
        polling = None
        if not basic or basic.get("closePrice") is None:
            polling = _get_json([f"{INDEX_POLLING_URL}/{code}"])
        return name, parse_index_overview(basic, integ, polling)

    with ThreadPoolExecutor(max_workers=2) as pool:
        return dict(pool.map(one, MARKETS.items()))


def market_mood(overview: dict) -> tuple[str, float] | None:
    """코스피+코스닥 상승 종목 비율로 매긴 '오늘의 시장' 분위기. (문구, 0~1)"""
    up = down = 0.0
    for sm in overview.values():
        b = (sm or {}).get("breadth") or {}
        up += (b.get("상승") or 0) + (b.get("상한") or 0)
        down += (b.get("하락") or 0) + (b.get("하한") or 0)
    if up + down == 0:
        return None
    ratio = up / (up + down)
    for cut, label in ((0.3, "안 좋아요"), (0.43, "조금 안 좋아요"), (0.57, "보통이에요"), (0.7, "좋아요")):
        if ratio < cut:
            return label, ratio
    return "아주 좋아요", ratio


# ─────────────────────────── 투자자별 매매동향(시장 전체) ───────────────────────────
INVESTOR_URL = "https://finance.naver.com/sise/investorDealTrendDay.naver"   # 예전 페이지(대비용)
INVESTOR_MARKETS = {"코스피": "01", "코스닥": "02"}
INVESTORS = ["개인", "외국인", "기관"]
INST_DETAIL = ["금융투자", "보험", "투신(사모)", "은행", "기타금융", "연기금", "기타법인"]
_GUBUN = {"1000": "금융투자", "2000": "보험", "3000": "투신(사모)", "3100": "투신(사모)", "4000": "은행",
          "5000": "기타금융", "6000": "연기금", "7000": "기타법인", "7100": "기타법인",
          "8000": "개인", "9000": "외국인", "9001": "외국인"}


def parse_market_trend(payload) -> pd.DataFrame:
    """stock.naver.com 시장 투자자별 매매동향 → date, 개인, 외국인, 기관, 금융투자 … 기타법인 (원 단위 그대로)."""
    content = (payload or {}).get("content") if isinstance(payload, dict) else payload
    rows = []
    for c in content or []:
        bd = str(c.get("bizdate") or "")
        if not re.fullmatch(r"\d{8}", bd):
            continue
        row = {"date": pd.Timestamp(int(bd[:4]), int(bd[4:6]), int(bd[6:]))}
        for k in ["개인", "외국인"] + INST_DETAIL:
            row[k] = 0.0
        seen = False
        for a in c.get("netAmounts") or []:
            key = _GUBUN.get(str(a.get("investorGubun")))
            v = _num(a.get("diffValue"))
            if key and v is not None:
                row[key] += v
                seen = True
        if seen:
            row["기관"] = sum(row[k] for k in INST_DETAIL if k != "기타법인")
            rows.append(row)
    cols = ["date"] + INVESTORS + INST_DETAIL
    return pd.DataFrame(rows, columns=cols).sort_values("date").drop_duplicates("date").reset_index(drop=True)


def _scale_to_eok(df: pd.DataFrame, ref: dict | None) -> pd.DataFrame:
    """API 금액 단위를 억원으로 맞춰요. 지수 요약의 당일 순매수(억원)가 있으면 그걸로 단위를 맞추고,
    없으면 크기로 짐작해요(원 → ÷1억, 백만원 → ÷100)."""
    if df.empty:
        return df
    cols = INVESTORS + INST_DETAIL
    div = None
    if ref and ref.get("bizdate"):
        hit = df[df["date"] == pd.to_datetime(str(ref["bizdate"]), format="%Y%m%d", errors="coerce")]
        if not hit.empty:
            ratios = [abs(hit.iloc[0][k] / ref[k]) for k in INVESTORS if ref.get(k) and hit.iloc[0][k]]
            if ratios:
                r = sorted(ratios)[len(ratios) // 2]
                div = min((1, 100, 1e8), key=lambda d: abs((r / d) - 1) if r else 9e9)
    if div is None:
        med = max(df[k].abs().median() for k in INVESTORS)
        div = 1e8 if med > 1e9 else (100 if med > 5e4 else 1)
    out = df.copy()
    out[cols] = out[cols] / div
    return out


def parse_investor_trend(text: str) -> list[dict]:
    """(예전) 네이버 투자자별 매매동향 HTML(일별, 억원) → [{date, 개인, 외국인, 기관}, ...]"""
    header = [_strip_tags(h).replace(" ", "") for h in re.findall(r"<th[^>]*>(.*?)</th>", text, re.S)]

    def col(name, default):
        for i, h in enumerate(header):
            if h.startswith(name):
                return i
        return default

    idx = {"개인": col("개인", 1), "외국인": col("외국인", 2), "기관": col("기관", 3)}
    rows = []
    for tr in re.split(r"<tr[\s>]", text):
        tds = [_strip_tags(t) for t in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if not tds:
            continue
        m = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{2})", tds[0].strip())
        if not m:
            continue
        row = {"date": pd.Timestamp(2000 + int(m.group(1)), int(m.group(2)), int(m.group(3)))}
        for name, i in idx.items():
            row[name] = to_num(tds[i]) if i < len(tds) else None
        if all(row[n] is not None for n in INVESTORS):
            rows.append(row)
    return rows


def fetch_investor_flows(overview: dict | None = None) -> dict[str, pd.DataFrame]:
    """코스피·코스닥 일별 투자자별 순매수(억원): 개인·외국인·기관 + 기관 세부(금융투자·투신·연기금 등)."""
    cols = ["date"] + INVESTORS + INST_DETAIL
    out = {}
    for market, code in MARKETS.items():
        if MOCK:
            rng = _rng(market)
            rows = []
            for dt in pd.bdate_range(end=now_kst().date(), periods=20):
                det = {k: float(rng.randint(-2500, 2500)) for k in INST_DETAIL}
                inst = sum(v for k, v in det.items() if k != "기타법인")
                f = float(rng.randint(-8000, 8000))
                rows.append({"date": dt, "개인": -(f + inst + det["기타법인"]), "외국인": f, "기관": inst, **det})
            out[market] = pd.DataFrame(rows, columns=cols)
            continue
        payload = _get_json([f"{STOCK_API}/domestic/market/trend/daily"],
                            params={"tradeType": "KRX", "marketType": code, "bizdate": now_kst().strftime("%Y%m%d"),
                                    "startIdx": 0, "pageSize": 30})
        df = parse_market_trend(payload)
        if not df.empty:
            out[market] = _scale_to_eok(df, ((overview or {}).get(market) or {}).get("deal"))
            continue
        rows = []   # 새 API가 안 되면 예전 HTML 페이지로 한 번 더
        for page in (1, 2):
            try:
                r = session.get(INVESTOR_URL, params={"bizdate": now_kst().strftime("%Y%m%d"),
                                                      "sosok": INVESTOR_MARKETS[market], "page": page}, timeout=8)
                r.raise_for_status()
                rows += parse_investor_trend(decode(r.content))
            except requests.RequestException:
                break
        df = pd.DataFrame(rows, columns=["date"] + INVESTORS)
        for k in INST_DETAIL:
            df[k] = float("nan")
        out[market] = df[cols].drop_duplicates("date").sort_values("date").reset_index(drop=True)
    return out


# ─────────────────────────── 투자자별 매매동향(종목별) ───────────────────────────
TREND_COLS = ["date", "close", "개인", "외국인", "기관", "외국인보유율", "개인금액", "외국인금액", "기관금액"]
_trend_cache: dict[str, tuple[float, pd.DataFrame]] = {}
TREND_TTL = 600


def parse_stock_trend(payload) -> pd.DataFrame:
    """종목 trend 응답(배열) → date, close, 개인·외국인·기관 순매수 수량(주), 외국인보유율, 금액 추정(억원)."""
    items = payload if isinstance(payload, list) else ((payload or {}).get("content") or (payload or {}).get("result") or [])
    rows = []
    for it in items or []:
        bd = re.sub(r"\D", "", str(it.get("bizdate") or it.get("localTradedAt") or ""))[:8]
        if len(bd) != 8:
            continue
        close = _num(it.get("closePrice"))
        row = {"date": pd.Timestamp(int(bd[:4]), int(bd[4:6]), int(bd[6:])), "close": close,
               "개인": _num(it.get("individualPureBuyQuant")), "외국인": _num(it.get("foreignerPureBuyQuant")),
               "기관": _num(it.get("organPureBuyQuant")), "외국인보유율": _num(it.get("frgnHoldRatio") or it.get("foreignerHoldRatio"))}
        if row["개인"] is None and row["외국인"] is not None and row["기관"] is not None:
            row["개인"] = None  # 개인이 없는 응답도 있어요(기타법인 때문에 역산하지 않음)
        for k in INVESTORS:
            row[f"{k}금액"] = row[k] * close / 1e8 if (row[k] is not None and close) else None
        rows.append(row)
    df = pd.DataFrame(rows, columns=TREND_COLS)
    return df.sort_values("date").drop_duplicates("date").reset_index(drop=True)


def mock_trend(code: str, days: int = 20) -> pd.DataFrame:
    rng = _rng("trend" + code)
    hist = mock_history(code).tail(days)
    rows = []
    hold = rng.uniform(2, 50)
    for d, c in zip(hist["date"], hist["close"]):
        f, i = rng.randint(-300_000, 300_000), rng.randint(-200_000, 200_000)
        hold = max(0.1, hold + rng.uniform(-0.3, 0.3))
        rows.append({"date": d, "close": c, "개인": -(f + i), "외국인": f, "기관": i, "외국인보유율": hold,
                     "개인금액": -(f + i) * c / 1e8, "외국인금액": f * c / 1e8, "기관금액": i * c / 1e8})
    return pd.DataFrame(rows, columns=TREND_COLS)


def fetch_stock_trend(code: str, days: int = 20) -> pd.DataFrame:
    """종목 하나의 최근 일별 개인·외국인·기관 순매수. 10분 동안 기억해요."""
    import time as _time
    hit = _trend_cache.get(code)
    if hit and _time.time() - hit[0] < TREND_TTL:
        return hit[1]
    if MOCK:
        df = mock_trend(code, days)
    else:
        payload = _get_json([f"{STOCK_API}/domestic/detail/{code}/trend", f"{M_API}/stock/{code}/trend"],
                            params={"tradeType": "KRX", "startIdx": 0, "pageSize": days}, timeout=6)
        df = parse_stock_trend(payload)
    _trend_cache[code] = (_time.time(), df)
    return df


def fetch_stock_trends(codes, workers: int = 8) -> dict[str, pd.DataFrame]:
    codes = [c for c in codes if is_kr(c)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(zip(codes, pool.map(fetch_stock_trend, codes)))


def trend_summary(df: pd.DataFrame | None) -> dict:
    """표에 넣을 값: 최근 거래일 순매수 금액(억원), 5일 누적, 외국인·기관 연속 순매수 일수."""
    out = {"flow_date": None, **{f"flow_{k}": None for k in INVESTORS}, **{f"flow5_{k}": None for k in INVESTORS},
           "streak_외국인": None, "streak_기관": None}
    if df is None or df.empty:
        return out
    last = df.iloc[-1]
    out["flow_date"] = last["date"]
    for k in INVESTORS:
        out[f"flow_{k}"] = last[f"{k}금액"]
        s5 = df.tail(5)[f"{k}금액"].dropna()
        out[f"flow5_{k}"] = float(s5.sum()) if not s5.empty else None
    for k in ("외국인", "기관"):
        n = 0
        for v in reversed(df[k].tolist()):
            if v is None or pd.isna(v) or v == 0:
                break
            if n == 0:
                sign = v > 0
            elif (v > 0) != sign:
                break
            n += 1
        out[f"streak_{k}"] = (n if sign else -n) if n else 0
    return out


# ─────────────────────────── 이름으로 코드 찾기 ───────────────────────────
def search_stock(name: str) -> list[tuple[str, str]]:
    """네이버 검색 자동완성 → [(코드, 이름), ...] (국내 종목만)."""
    found: list[tuple[str, str]] = []

    def walk(node):
        if isinstance(node, dict):
            code, nm = node.get("code"), node.get("name")
            if isinstance(code, str) and isinstance(nm, str) and is_kr(code) and (code, nm) not in found:
                found.append((code, nm))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(_get_json([AUTOCOMPLETE_M_URL], params={"query": name, "target": "stock"}, timeout=6))
    if not found:
        walk(_get_json([AUTOCOMPLETE_URL], params={"q": name, "target": "stock"}, timeout=6))
    return found


def resolve_codes(names) -> tuple[dict[str, str], dict[str, list[tuple[str, str]]]]:
    """이름이 똑같은(공백·괄호 무시) 국내 종목만 코드로 인정해요. (찾은 것, 못 찾은 것의 후보)"""
    names = list(names)
    if MOCK:
        return {n: f"9{int(hashlib.md5(n.encode()).hexdigest()[:5], 16) % 100000:05d}" for n in names}, {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(search_stock, names))
    ok, miss = {}, {}
    for n, cands in zip(names, results):
        exact = [c for c, nm in cands if normalize_name(nm) == normalize_name(n)]
        if exact:
            ok[n] = exact[0]
        else:
            miss[n] = cands[:3]
    return ok, miss


def build_table(stocks: list[dict], histories: dict, quotes: dict,
                shares: dict | None = None, fx: dict | None = None, bo_mode: str = "line",
                monthlies: dict | None = None) -> pd.DataFrame:
    rows = []
    for s in stocks:
        naver_name, hist, error = histories.get(s["code"], (None, empty_frame(), "조회 안 됨"))
        metrics = compute_metrics(hist, quotes.get(s["code"]), bo_mode=bo_mode,
                                  monthly=(monthlies or {}).get(s["code"]))
        market, currency = market_of(s["code"])
        n_shares = (shares or {}).get(s["code"])
        cap_local = metrics["price"] * n_shares if (metrics["price"] and n_shares) else None
        rate = (fx or {}).get(currency)
        rows.append({
            **s,
            **metrics,
            "market": market,
            "currency": currency,
            "url": quote_url(s["code"]),
            "shares": n_shares,
            "cap_local": cap_local,
            "cap_krw": cap_local * rate if (cap_local and rate) else None,
            "naver_name": naver_name,
            "name_ok": name_matches(s["name"], naver_name),
            "error": error if metrics["price"] is None else None,
        })
    return add_rs_ranks(pd.DataFrame(rows))


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


def mock_history(code: str, count: int = 520) -> pd.DataFrame:
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


# ─────────────────────────── 실적(영업이익·EPS) 정배열 ───────────────────────────
FIN_TTL = 12 * 3600          # 실적은 자주 안 바뀌어서 종목마다 12시간 기억
_fin_cache: dict[str, tuple[float, dict | None]] = {}
FIN_ROWS = {"영업이익": "op", "EPS": "eps", "매출액": "sales"}


def _fin_label(title: str) -> str | None:
    t = re.sub(r"\s+", "", title or "")
    if t.startswith("영업이익") and "률" not in t and "증가" not in t:
        return "op"
    if t.startswith("EPS"):
        return "eps"
    if t.startswith("매출액") and "증가" not in t:
        return "sales"
    return None


def _period_label(title: str, is_est: bool) -> str:
    m = re.search(r"(\d{4})[.\-/]?(\d{1,2})?", title or "")
    if not m:
        return title
    return m.group(1) + ("(E)" if is_est else "")


def parse_fin_mobile(obj) -> pd.DataFrame:
    """네이버 모바일 finance/annual JSON → period·is_est·op·eps·sales 표."""
    info = (obj or {}).get("financeInfo") or obj or {}
    titles = info.get("trTitleList") or []
    rows = info.get("rowList") or []
    if not titles or not rows:
        return pd.DataFrame()
    recs = []
    for t in titles:
        key = t.get("key")
        is_est = str(t.get("isConsensus", "N")).upper() == "Y" or "(E)" in str(t.get("title", ""))
        rec = {"key": key, "period": _period_label(str(t.get("title", key)), is_est), "is_est": is_est}
        for row in rows:
            lab = _fin_label(row.get("title", ""))
            if not lab or lab in rec:
                continue
            cell = (row.get("columns") or {}).get(key) or {}
            rec[lab] = _num(cell.get("value") if isinstance(cell, dict) else cell)
        recs.append(rec)
    return pd.DataFrame(recs).sort_values("key").reset_index(drop=True)


def parse_fin_html(text: str) -> pd.DataFrame:
    """네이버 PC 종목 메인의 '기업실적분석' 표에서 연간 열만 뽑아요(앞 4열이 연간)."""
    m = re.search(r'class="section cop_analysis".*?</table>', text or "", re.S)
    if not m:
        return pd.DataFrame()
    block = m.group(0)
    thead = re.search(r"<thead>(.*?)</thead>", block, re.S)
    tbody = re.search(r"<tbody>(.*?)</tbody>", block, re.S)
    if not thead or not tbody:
        return pd.DataFrame()
    heads = [_strip_tags(h).strip() for h in re.findall(r"<th[^>]*>(.*?)</th>", thead.group(1), re.S)]
    periods = [h for h in heads if re.match(r"\d{4}\.\d{2}", h)]
    annual = periods[:4]
    if not annual:
        return pd.DataFrame()
    recs = [{"key": re.sub(r"\D", "", p)[:6], "period": _period_label(p, "(E)" in p), "is_est": "(E)" in p}
            for p in annual]
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", tbody.group(1), re.S):
        th = re.search(r"<th[^>]*>(.*?)</th>", tr, re.S)
        if not th:
            continue
        lab = _fin_label(_strip_tags(th.group(1)))
        if not lab or lab in recs[0]:
            continue
        tds = [_strip_tags(td).strip() for td in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        for i, rec in enumerate(recs):
            rec[lab] = _num(tds[i]) if i < len(tds) else None
    return pd.DataFrame(recs)


def fetch_financials(code: str) -> pd.DataFrame | None:
    """연간 매출액·영업이익(억원)·EPS(원), 실적과 컨센서스(E). 모바일 API → PC 페이지 순서로 시도."""
    if not is_kr(code):
        return None
    now = _time.time()
    hit = _fin_cache.get(code)
    if hit and now - hit[0] < FIN_TTL:
        return hit[1]
    if MOCK:
        df = mock_financials(code)
    else:
        df = pd.DataFrame()
        obj = _get_json([f"https://m.stock.naver.com/api/stock/{code}/finance/annual"])
        if obj:
            try:
                df = parse_fin_mobile(obj)
            except Exception:  # 응답 형식이 바뀐 경우
                df = pd.DataFrame()
        if df.empty or df.get("op") is None or df["op"].isna().all():
            try:
                r = session.get("https://finance.naver.com/item/main.naver", params={"code": code}, timeout=8)
                df = parse_fin_html(decode(r.content))
            except requests.RequestException:
                df = pd.DataFrame()
    df = df if (df is not None and not df.empty) else None
    _fin_cache[code] = (now, df)
    return df


def fetch_financials_many(codes, workers: int = 8) -> dict[str, pd.DataFrame | None]:
    codes = [c for c in codes if is_kr(c)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(zip(codes, pool.map(fetch_financials, codes)))


def earnings_trend(df: pd.DataFrame | None, actual_years: int = 3) -> dict:
    """영업이익·EPS가 최근 실적 N년 + 컨센서스(E) 전 구간에서 해마다 증가(정배열)하는지.

    - 실적 연도는 최근 actual_years년, 전망(E)은 있는 만큼 전부
    - 전망(E)이 하나도 없으면 판정 불가(None) — 앞으로도 우상향인지 확인할 수 없어서
    - 마지막 실적 연도 값이 0 이하(적자)면 정배열로 보지 않아요
    """
    out = {"earn_ok": None, "op_ok": None, "eps_ok": None, "earn_span": None, "n_est": 0}
    if df is None or df.empty or "op" not in df or "eps" not in df:
        return out
    act = df[~df["is_est"]].dropna(subset=["op", "eps"], how="all").tail(actual_years)
    est = df[df["is_est"]]
    out["n_est"] = int(est[["op", "eps"]].notna().any(axis=1).sum())
    seq = pd.concat([act, est])

    def rising(col):
        vals = seq[col].dropna().tolist()
        n_act = act[col].notna().sum()
        n_est = est[col].notna().sum()
        if n_act < actual_years or n_est == 0:
            return None
        if act[col].dropna().iloc[-1] <= 0:
            return False
        return all(b > a for a, b in zip(vals, vals[1:]))

    out["op_ok"], out["eps_ok"] = rising("op"), rising("eps")
    if out["op_ok"] is None or out["eps_ok"] is None:
        out["earn_ok"] = None
    else:
        out["earn_ok"] = bool(out["op_ok"] and out["eps_ok"])
    periods = seq["period"].tolist()
    out["earn_span"] = f"{periods[0]}~{periods[-1]}" if periods else None
    return out


def mock_financials(code: str) -> pd.DataFrame:
    rng = _rng(code + "fin")
    op, eps, base = rng.uniform(100, 5000), rng.uniform(300, 8000), 2023
    recs = []
    for i in range(6):
        g = rng.uniform(-0.15, 0.4)
        op, eps = op * (1 + g), eps * (1 + g + rng.uniform(-0.05, 0.05))
        recs.append({"key": f"{base + i}12", "period": f"{base + i}" + ("(E)" if i >= 3 else ""),
                     "is_est": i >= 3, "op": round(op), "eps": round(eps), "sales": round(op * 12)})
    return pd.DataFrame(recs)


# ─────────────────────────── 일·주·월봉 신고가(52주 · 역대) ───────────────────────────
NH_KEYS = ("nh52_d", "nh52_w", "nh52_m", "ath_d", "ath_w", "ath_m", "ath_price", "ath_ok")


def fetch_monthly(code: str, count: int = 600) -> pd.DataFrame:
    """상장 이후 전체 월봉(역대 최고가 계산용). 국내는 네이버, 해외는 야후."""
    if MOCK:
        d = mock_history(code, 520)
        return d.groupby(d["date"].dt.to_period("M")).agg(date=("date", "first"), high=("high", "max")).reset_index(drop=True)
    try:
        if is_kr(code):
            r = session.get(FCHART_URL, params={"symbol": code, "timeframe": "month", "count": count, "requestType": 0},
                            timeout=8)
            r.raise_for_status()
            return parse_fchart(decode(r.content))[1]
        import yfinance as yf
        return normalize_yf(yf.Ticker(code).history(period="max", interval="1mo", auto_adjust=False))
    except Exception:
        return empty_frame()


def fetch_monthlies(codes, workers: int = 8) -> dict[str, pd.DataFrame]:
    codes = list(codes)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(zip(codes, pool.map(fetch_monthly, codes)))


def newhigh_flags(h: pd.DataFrame, monthly: pd.DataFrame | None = None) -> dict:
    """지금 봉(오늘 일봉·이번 주 주봉·이번 달 월봉)의 고가가 52주 신고가인지, 역대 신고가인지.

    - 52주: 그 봉이 시작되기 전 52주(364일) 최고가를 그 봉 고가가 넘었는지
    - 역대: 그 봉이 시작되기 전 상장 이후 전체 최고가를 넘었는지(월봉으로 과거 전체를 봐요)
    """
    out = {k: None for k in NH_KEYS}
    if h is None or h.empty:
        return out
    d = h.copy()
    d["date"] = pd.to_datetime(d["date"])
    today = d["date"].iloc[-1].normalize()
    starts = {
        "d": today,
        "w": today - pd.Timedelta(days=today.weekday()),
        "m": today.replace(day=1),
    }
    mon_ok = monthly is not None and not monthly.empty
    if mon_ok:
        mm = monthly.copy()
        mm["date"] = pd.to_datetime(mm["date"])
    for k, st_ in starts.items():
        bar = d[d["date"] >= st_]
        before = d[d["date"] < st_]
        if bar.empty:
            continue
        bar_high = float(bar["high"].max())
        w52 = before[before["date"] >= st_ - pd.Timedelta(days=364)]
        if len(before) >= 200 and not w52.empty:
            out[f"nh52_{k}"] = bool(bar_high > float(w52["high"].max()))
        # 역대: 일봉에 있는 과거 + 월봉에 있는 그 이전 전체
        cands = [float(before["high"].max())] if not before.empty else []
        if mon_ok:
            old = mm[mm["date"] < starts["m"]]
            if not old.empty:
                cands.append(float(old["high"].max()))
        if cands and (mon_ok or len(before) < 200):
            out[f"ath_{k}"] = bool(bar_high > max(cands))
    hist_max = float(d["high"].max())
    if mon_ok:
        hist_max = max(hist_max, float(mm["high"].max()))
    out["ath_price"] = hist_max
    out["ath_ok"] = mon_ok
    return out
