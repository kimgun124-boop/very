"""네이버 금융에서 시세와 일봉을 가져와 52주 지표를 계산합니다.

- 실시간 시세: polling.finance.naver.com (여러 종목을 한 번에 조회)
- 일봉(약 1년치): fchart.stock.naver.com, 실패하면 api.finance.naver.com/siseJson
- 해외 종목(코드가 6자리 숫자가 아닌 것): 야후 파이낸스(yfinance) 일봉, 15분 안팎 지연
- 시가총액: 상장주식수(하루 한 번 조회) × 현재가로 실시간 계산. 해외는 환율로 원화 환산도 함께

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


def is_kr(code: str) -> bool:
    """6자리 숫자면 국내 종목, 그 외(NVDA, 6857.T 등)는 해외 종목."""
    return len(code) == 6 and code.isdigit()


_SUFFIX_MARKET = {".T": ("JP", "JPY"), ".PA": ("FR", "EUR"), ".AS": ("NL", "EUR"),
                  ".DE": ("DE", "EUR"), ".AX": ("AU", "AUD"), ".HK": ("HK", "HKD"), ".TW": ("TW", "TWD"),
                  ".HE": ("FI", "EUR"), ".L": ("GB", "GBp")}


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


def fetch_history_overseas(ticker: str, count: int = 300) -> tuple[str | None, pd.DataFrame, str | None]:
    """야후 파이낸스 일봉. (종목명은 확인하지 않으므로 None)"""
    if MOCK:
        return None, mock_history(ticker, count), None
    try:
        import yfinance as yf
    except ImportError:
        return None, empty_frame(), "yfinance가 설치되지 않음(requirements.txt 확인)"
    try:
        hist = yf.Ticker(ticker).history(period="14mo", interval="1d", auto_adjust=False)
    except Exception as exc:  # 야후 쪽 일시 오류·요청 제한
        return None, empty_frame(), f"야후 조회 실패: {exc.__class__.__name__}"
    df = normalize_yf(hist)
    return (None, df, None) if not df.empty else (None, df, "야후 응답에 일봉이 없음(티커 확인)")


INDEXES = [
    {"name": "코스피", "symbol": "KOSPI", "source": "naver"},
    {"name": "코스닥", "symbol": "KOSDAQ", "source": "naver"},
    {"name": "나스닥", "symbol": "^IXIC", "source": "yahoo"},
]


def fetch_index_histories() -> dict[str, tuple[pd.DataFrame, str | None]]:
    """코스피·코스닥은 네이버 일봉(장중 갱신), 나스닥은 야후 일봉(지연)."""
    out = {}
    for idx in INDEXES:
        if idx["source"] == "naver":
            _, df, err = fetch_history(idx["symbol"], count=300)
        else:
            _, df, err = fetch_history_overseas(idx["symbol"])
        out[idx["symbol"]] = (df, err)
    return out


def index_summary(df: pd.DataFrame) -> dict | None:
    """현재 지수, 등락률, 60일선과의 거리."""
    if df is None or len(df) < 2:
        return None
    close = df["close"].astype(float)
    last, prev = float(close.iloc[-1]), float(close.iloc[-2])
    ma60 = float(close.tail(60).mean()) if len(close) >= 60 else None
    return {
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


# ─────────────────────────── 시가총액 ───────────────────────────
MARKET_SUM_URL = "https://finance.naver.com/sise/sise_market_sum.naver"
ITEM_MAIN_URL = "https://finance.naver.com/item/main.naver"
FX_TICKERS = {"USD": "USDKRW=X", "JPY": "JPYKRW=X", "EUR": "EURKRW=X", "AUD": "AUDKRW=X",
              "GBP": "GBPKRW=X", "HKD": "HKDKRW=X", "TWD": "TWDKRW=X"}


def _strip_tags(html_text: str) -> str:
    return re.sub(r"<[^>]+>", "", html_text).replace("&nbsp;", " ").strip()


def parse_market_sum(text: str) -> tuple[dict[str, int], int]:
    """네이버 시가총액 순위 페이지 → ({코드: 상장주식수}, 마지막 페이지 번호)."""
    header = [_strip_tags(h) for h in re.findall(r"<th[^>]*>(.*?)</th>", text, re.S)]
    try:
        idx = header.index("상장주식수")
    except ValueError:
        return {}, 1
    out: dict[str, int] = {}
    for row in re.split(r"<tr[\s>]", text):
        m = re.search(r"code=(\d{6})", row)
        if not m:
            continue
        tds = [_strip_tags(t) for t in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(tds) < len(header):
            continue
        shares = to_num(tds[idx])
        if shares:
            out[m.group(1)] = int(shares * 1000)  # 표 단위: 천주
    last = re.search(r'class="pgRR".*?page=(\d+)', text, re.S)
    return out, int(last.group(1)) if last else 1


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
        m = re.search(r"상장주식수</th>\s*<td[^>]*>\s*<em>([\d,]+)</em>", decode(r.content))
        return int(m.group(1).replace(",", "")) if m else None
    except requests.RequestException:
        return None


def fetch_kr_shares(codes) -> dict[str, int]:
    """국내 상장주식수. 코스피·코스닥 시가총액 순위 페이지를 한 번에 훑고, 빠진 종목만 개별 조회."""
    codes = [c for c in codes if is_kr(c)]
    if MOCK:
        return {c: _rng(c).randint(10_000_000, 900_000_000) for c in codes}
    shares: dict[str, int] = {}
    jobs = []
    for sosok in (0, 1):  # 0: 코스피, 1: 코스닥
        first, last = _market_sum_page(sosok, 1)
        shares.update(first)
        jobs += [(sosok, p) for p in range(2, last + 1)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        for part, _ in pool.map(lambda job: _market_sum_page(*job), jobs):
            shares.update(part)
    missing = [c for c in codes if c not in shares]
    if missing:
        with ThreadPoolExecutor(max_workers=6) as pool:
            for code, n in zip(missing, pool.map(_shares_from_item_page, missing)):
                if n:
                    shares[code] = n
    return {c: shares[c] for c in codes if c in shares}


def fetch_overseas_shares(tickers) -> dict[str, int]:
    """해외 종목 발행주식수(야후)."""
    tickers = [t for t in tickers if not is_kr(t)]
    if MOCK:
        return {t: _rng(t).randint(50_000_000, 5_000_000_000) for t in tickers}
    try:
        import yfinance as yf
    except ImportError:
        return {}

    def one(t):
        try:
            fi = yf.Ticker(t).fast_info
            n = getattr(fi, "shares", None)
            if not n:
                n = fi["shares"]
            return int(n) if n else None
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        return {t: n for t, n in zip(tickers, pool.map(one, tickers)) if n}


def fetch_fx() -> dict[str, float]:
    """통화별 원화 환율. GBp(펜스)는 파운드의 1/100."""
    if MOCK:
        rates = {"USD": 1380.0, "JPY": 9.3, "EUR": 1500.0, "AUD": 900.0, "GBP": 1750.0, "HKD": 177.0, "TWD": 43.0}
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


def build_table(stocks: list[dict], histories: dict, quotes: dict,
                shares: dict | None = None, fx: dict | None = None) -> pd.DataFrame:
    rows = []
    for s in stocks:
        naver_name, hist, error = histories.get(s["code"], (None, empty_frame(), "조회 안 됨"))
        metrics = compute_metrics(hist, quotes.get(s["code"]))
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
