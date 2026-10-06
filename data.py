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

import numpy as np
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
# app.py가 이 값으로 서버에 남아 있는 예전 data.py를 알아채고 새로 읽어요. data.py를 고칠 때마다 올려요.
DATA_VERSION = "2026-10-06-ath"
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


_EMPTY_FRAME = None


def empty_frame() -> pd.DataFrame:
    """(속도) 빈 표는 하나만 만들어 같이 써요(만들 때마다 느려서). 고치지 말고 읽기만 해요."""
    global _EMPTY_FRAME
    if _EMPTY_FRAME is None:
        _EMPTY_FRAME = pd.DataFrame(columns=COLUMNS)
    return _EMPTY_FRAME


def rows_to_frame(rows) -> pd.DataFrame:
    df = pd.DataFrame(list(rows), columns=COLUMNS)
    if df.empty:
        return empty_frame()
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d", errors="coerce")
    for col in COLUMNS[1:]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["date", "close", "high", "low"])
    df = df[df["close"] > 0]
    df = fix_bars(df.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True))
    return slim(df)


def slim(df: pd.DataFrame) -> pd.DataFrame:
    """(메모리) 가격·거래량을 float32로 — 계산할 땐 float로 다시 바꿔 써서 결과는 같아요."""
    if df is None or df.empty:
        return df
    for col in ("open", "high", "low", "close", "volume"):
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("float32")
    return df


def fix_bars(df: pd.DataFrame) -> pd.DataFrame:
    """거래정지·신규상장 날처럼 시가·고가·저가가 0으로 오는 봉을 종가로 채워요(0으로 나누기 오류 방지)."""
    if df is None or df.empty or "close" not in df:
        return df
    c = pd.to_numeric(df["close"], errors="coerce")
    bad = False
    for col in ("open", "high", "low"):
        if col in df:
            v = pd.to_numeric(df[col], errors="coerce")
            m = ~(v > 0)
            if m.any():
                bad = True
                df[col] = v.where(~m, c)
    if bad:
        df["high"] = np.fmax(pd.to_numeric(df["high"], errors="coerce"), c)
        df["low"] = np.fmin(pd.to_numeric(df["low"], errors="coerce"), c)
    return df


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
                "open": to_num(item.get("ov")),
                "volume": to_num(item.get("aq")),
                "value": to_num(item.get("aa")),      # 누적 거래대금(단위가 응답마다 달라 쓸 때 점검)
                "status": item.get("ms"),
            }
    return out


# ─────────────────────────── 네트워크 조회 ───────────────────────────
HIST_COUNT = 340     # (메모리) 52주(250일) + 돌파 추적 90일이면 충분. 차트 탭은 따로 길게 받아요.


def fetch_history(code: str, count: int = HIST_COUNT) -> tuple[str | None, pd.DataFrame, str | None]:
    """(네이버 종목명, 일봉, 오류메시지)"""
    if MOCK:
        return None, slim(mock_history(code, count)), None
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


def fetch_history_overseas(ticker: str, count: int = HIST_COUNT) -> tuple[str | None, pd.DataFrame, str | None]:
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
            _, df, err = fetch_history(idx["symbol"], count=320)
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


def _yf_batch(tickers, period: str, interval: str) -> dict[str, pd.DataFrame]:
    """야후 여러 종목을 한 번에 받아요(yfinance가 안에서 병렬로 받음). 못 받은 티커는 결과에서 빠져요."""
    tickers = list(tickers)
    if not tickers or MOCK:
        return {}
    try:
        import yfinance as yf
    except ImportError:
        return {}
    out: dict[str, pd.DataFrame] = {}
    for part in chunks(tickers, 60):
        try:
            raw = yf.download(part, period=period, interval=interval, group_by="ticker", auto_adjust=False,
                              threads=True, progress=False)
        except Exception:  # 야후 일시 오류·요청 제한 → 아래에서 하나씩 다시
            continue
        if raw is None or raw.empty:
            continue
        for t in part:
            if isinstance(raw.columns, pd.MultiIndex):
                if t not in raw.columns.get_level_values(0):
                    continue
                sub = raw[t]
            elif len(part) == 1:
                sub = raw
            else:
                continue
            df = normalize_yf(sub)
            if not df.empty:
                out[t] = df
    return out


def fetch_histories(codes, workers: int = 12) -> dict[str, tuple]:
    """국내·해외 섞인 코드 목록을 받아 각각 알맞은 곳에서 일봉을 가져옵니다.

    (속도) 국내는 12개씩 동시에, 해외는 야후에서 한 번에 받고 빠진 종목만 하나씩 다시 받아요.
    """
    codes = list(codes)
    kr = [c for c in codes if is_kr(c)]
    os_ = [c for c in codes if not is_kr(c)]
    out: dict[str, tuple] = {}
    if kr:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            out.update(zip(kr, pool.map(fetch_history, kr)))
        # 한꺼번에 많이 받으면 네이버가 잠깐 막아서 빈 종목이 생겨요 → 빠진 종목만 천천히 두 번 더 받아요
        for wait, w in ((2.0, 4), (5.0, 2)):
            miss = [c for c in kr if out.get(c) is None or out[c][1] is None or out[c][1].empty]
            if not miss or MOCK:
                break
            _time.sleep(wait)
            with ThreadPoolExecutor(max_workers=w) as pool:
                for c, res in zip(miss, pool.map(fetch_history, miss)):
                    if res[1] is not None and not res[1].empty:
                        out[c] = res
    if os_:
        got = _yf_batch(os_, "26mo", "1d")
        out.update({t: (None, df, None) for t, df in got.items()})
        rest = [t for t in os_ if t not in got]
        if rest:
            with ThreadPoolExecutor(max_workers=4) as pool:
                out.update(zip(rest, pool.map(fetch_history_overseas, rest)))
    return {c: out[c] for c in codes}


def fetch_quotes(codes) -> tuple[dict[str, dict], str | None]:
    """({코드: 시세}, 오류메시지). 일부 실패해도 받은 만큼 돌려줍니다."""
    codes = [c for c in codes if is_kr(c)]
    if MOCK:
        return {c: mock_quote(c) for c in codes}, None
    out: dict[str, dict] = {}
    errors = []

    def one(part):
        try:
            r = session.get(POLLING_URL, params={"query": "SERVICE_ITEM:" + ",".join(part)}, timeout=8)
            r.raise_for_status()
            return parse_polling(decode(r.content)), None
        except (requests.RequestException, ValueError) as exc:
            return {}, exc.__class__.__name__

    # (속도) 500여 종목 = 40개씩 13번 요청. 예전엔 차례로 보내서 몇 초씩 걸렸는데 이제 동시에 보내요.
    with ThreadPoolExecutor(max_workers=8) as pool:
        for got, err in pool.map(one, list(chunks(codes, 40))):
            out.update(got)
            if err:
                errors.append(err)
    return out, ("실시간 시세 조회 실패: " + ", ".join(sorted(set(errors)))) if errors else None


# ─────────────────────────── 지표 계산 ───────────────────────────
def _arrays(hist: pd.DataFrame):
    """일봉 DataFrame → (날짜 datetime64[D], 고가, 저가, 종가) numpy 배열. 계산은 전부 이 배열로 해서 빨라요."""
    d = hist["date"].values.astype("datetime64[D]")
    c = hist["close"].to_numpy(dtype=float)
    h, l = hist["high"].to_numpy(dtype=float), hist["low"].to_numpy(dtype=float)
    if not ((h > 0).all() and (l > 0).all()):        # 서버에 예전에 받아 둔 일봉에도 0이 섞여 있을 수 있어요
        h = np.where(h > 0, h, c)
        l = np.where(l > 0, l, c)
    return d, h, l, c


def compute_metrics(hist: pd.DataFrame, quote: dict | None, today: date | None = None, bo_mode: str = "line",
                    monthly: pd.DataFrame | None = None, official: dict | None = None) -> dict:
    """현재가·등락률·52주 최고/최저·괴리율·신고가 경과일·정배열 여부.

    (속도) 600종목을 30초마다 다시 계산하므로 pandas 대신 numpy 배열로 계산해요. 결과는 예전과 같아요.
    """
    today = today or now_kst().date()
    res = {
        "price": None, "prev": None, "change": None, "high52": None, "low52": None,
        "gap": None, "to_high": None, "pos": None, "days_since_high": None,
        "aligned": None, "above60": None, "source": None,
        "nh_cnt20": None, "nh_cnt60": None, "nh_streak": None, "day_open": None, "day_high": None, "day_low": None,
        "high52_calc": None, "low52_calc": None, "h52_diff": None, "h52_fixed": False, "h52_src": None,
        "atr_pct": None, "ret_1m": None, "ret_3m": None, "ret_6m": None, "rs_raw": None,
        **{k: None for k in BO_KEYS},
        **{k: None for k in NH_KEYS},
        **{k: None for k in EXTRA_KEYS},
    }
    if hist is None or hist.empty:
        return res

    dts, highs, lows, closes0 = _arrays(hist)
    vols = hist["volume"].to_numpy(dtype=float) if "volume" in hist else np.full(len(dts), np.nan)
    today64 = np.datetime64(today, "D")
    last_is_today = dts[-1] == today64

    if quote and quote.get("price"):
        price = float(quote["price"])
        prev = quote.get("prev")
        if not prev:
            prev = closes0[-2] if (last_is_today and len(closes0) > 1) else closes0[-1]
        source = "실시간"
    else:
        price = float(closes0[-1])
        prev = closes0[-2] if len(closes0) > 1 else None
        source = "일봉"
    if prev is not None:
        prev = float(prev)

    wmask = dts >= today64 - np.timedelta64(364, "D")
    if not wmask.any():
        wmask = np.zeros(len(dts), dtype=bool)
        wmask[-250:] = True
    w_d, w_hi, w_lo = dts[wmask], highs[wmask], lows[wmask]
    hi_i = int(np.nanargmax(w_hi))
    high52 = float(w_hi[hi_i])
    days_since = int((w_d > w_d[hi_i]).sum())
    low52 = float(np.nanmin(w_lo))

    q_high = quote.get("high") if quote else None
    q_low = quote.get("low") if quote else None
    if q_high and q_high > high52:
        high52, days_since = float(q_high), 0
    if price > high52:
        high52, days_since = price, 0
    if q_low and q_low < low52:
        low52 = float(q_low)
    low52 = min(low52, price)

    # 네이버 공식 52주 최고·최저와 대조. 차이가 H52_TOL%를 넘으면 공식값으로 고치고 표시해 둬요.
    high52_calc, low52_calc = high52, low52
    h52_diff, h52_fixed, h52_src = None, False, "일봉 계산"
    off_hi = (official or {}).get("high52")
    off_lo = (official or {}).get("low52")
    if off_hi:
        live_hi = max([float(x) for x in (q_high, price) if x] or [0.0])
        final_hi = max(float(off_hi), live_hi)      # 공식값이 늦게 바뀌는 장중 신고가는 실시간 값이 우선
        h52_diff = (high52_calc / final_hi - 1) * 100
        if abs(h52_diff) > H52_TOL:
            h52_fixed, h52_src = True, "네이버 공식(교정)"
            high52 = final_hi
            if live_hi >= final_hi:
                days_since = 0
            else:
                near = np.where(np.abs(w_hi / final_hi - 1) * 100 <= H52_TOL)[0]
                days_since = int((w_d > w_d[near[-1]]).sum()) if len(near) else None
        else:
            h52_src = "네이버 공식과 일치"
    if off_lo:
        live_lo = min([float(x) for x in (q_low, price) if x] or [float(off_lo)])
        final_lo = min(float(off_lo), live_lo)
        if abs(low52_calc / final_lo - 1) * 100 > H52_TOL:
            low52 = final_lo
            h52_fixed = True
            if h52_src != "네이버 공식(교정)":
                h52_src = "네이버 공식(최저가 교정)"

    # 현재가·장중 고가를 오늘 일봉에 반영한 사본으로 정배열·ATR·수익률·돌파 유지를 계산해요.
    cc, hh, ll = closes0.copy(), highs.copy(), lows.copy()
    if last_is_today:
        cc[-1] = price
        if q_high:
            hh[-1] = max(hh[-1], float(q_high))
        if q_low:
            ll[-1] = min(ll[-1], float(q_low))
    aligned = None
    above60 = bool(price > float(cc[-60:].mean())) if len(cc) >= 60 else None   # 60일선 위 종목 비율(시장 카드)용
    if len(cc) >= 120:
        ma20, ma60, ma120 = (float(cc[-n:].mean()) for n in (20, 60, 120))
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
        above60=above60,
        source=source,
        high52_calc=high52_calc,
        low52_calc=low52_calc,
        h52_diff=h52_diff,
        h52_fixed=h52_fixed,
        h52_src=h52_src,
    )

    res["atr_pct"] = _atr_np(hh, ll, cc, ATR_DAYS)
    # 신고가 갱신 흐름: 최근 20·60거래일 중 장중 고가가 직전 52주(250일) 최고가를 넘은 날 수, 최근부터 끊기지 않은 갱신 일수
    if len(hh) >= 120:
        prior = _rolling_prior_max(hh, 250, min(250, len(hh) - 20))
        hit = np.nan_to_num(hh > prior, nan=0).astype(bool) & ~np.isnan(prior)
        res["nh_cnt20"], res["nh_cnt60"] = int(hit[-20:].sum()), int(hit[-60:].sum())
        k = 0
        for v in hit[::-1]:
            if not v:
                break
            k += 1
        res["nh_streak"] = k
    # 오늘(마지막 거래일) 봉: 미니 캔들용
    try:
        o = (quote or {}).get("open") if last_is_today else None
        o = o or (float(hist["open"].iloc[-1]) if "open" in hist and last_is_today else None)
        res.update(day_open=float(o) if o else None, day_high=float(hh[-1]), day_low=float(ll[-1]))
    except (TypeError, ValueError):
        pass
    rets = {}
    for key, n in (("ret_1m", 21), ("ret_3m", 63), ("ret_6m", 126), ("ret_9m", 189), ("ret_12m", 250)):
        rets[key] = (price / float(cc[-1 - n]) - 1) * 100 if len(cc) > n else None
    res.update(ret_1m=rets["ret_1m"], ret_3m=rets["ret_3m"], ret_6m=rets["ret_6m"])
    res["rs_raw"] = rs_raw_score(rets)
    res.update(_breakout_np(dts, hh, cc, bo_mode))
    res.update(_newhigh_np(dts, hh, _monthly_arrays(monthly)))
    vv = vols.copy()
    q_vol = quote.get("volume") if quote else None
    if last_is_today and q_vol and not (vv[-1] >= q_vol):
        vv[-1] = float(q_vol)                          # 일봉(30분마다)보다 실시간 누적 거래량이 더 최신
    # (속도) ADX는 일봉이 바뀔 때만 다시 계산해요(장중 몇 분 사이엔 거의 안 변해요)
    # (메모리) 일봉 표 자체를 붙잡지 않고 '길이·마지막 날짜·마지막 종가'로만 같은 일봉인지 확인해요.
    # 예전엔 표를 붙잡아 둬서 새로 받을 때마다 옛 표가 메모리에 쌓였어요(리소스 초과 원인).
    key = id(hist)
    sig = (len(hh), str(dts[-1]) if len(dts) else "", float(cc[-2]) if len(cc) > 1 else 0.0)
    hit = _ADX_CACHE.get(key)
    if hit is not None and hit[0] == sig:
        adx = hit[1]
    else:
        adx = _adx(hh, ll, cc)
        if len(_ADX_CACHE) > 4000:
            _ADX_CACHE.clear()
        _ADX_CACHE[key] = (sig, adx)
    res.update(_extras(dts, hh, ll, cc, vv, price, res.get("bo_date") if res.get("bo_status") == "유지" else None,
                       last_is_today, adx=adx))
    return res


_ADX_CACHE: dict = {}


# ─────────────────────────── 매수 후보 판정용 추가 지표 ───────────────────────────
EXTRA_KEYS = ("ma20", "dist_ma20", "adx", "bo_vol_ratio", "bo_dcr", "htf", "ma5", "ma50", "ma5_vs_50", "tv20",
              "vol_today", "dcr_today", "tv5", "tvp20")


def _wilder(x: np.ndarray, n: int) -> np.ndarray:
    """와일더 평활(첫 값 = 처음 n개 평균, 이후 (앞값×(n-1)+새값)/n). 판다스 ewm으로 한 번에 계산해요."""
    seed = np.concatenate([[x[:n].mean()], x[n:]])
    return pd.Series(seed).ewm(alpha=1 / n, adjust=False).mean().to_numpy()


def _adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 14) -> float | None:
    """ADX(14), 와일더 방식. 20 미만 = 추세 약함/횡보, 20~40 = 추세, 40 이상 = 강한 추세.
    (속도) 예전 파이썬 반복문과 같은 값을 넘파이·판다스로 한 번에 계산해요."""
    m = len(close)
    if m < 2 * n + 1:
        return None
    h, l, c = (np.asarray(x, dtype=float) for x in (high, low, close))
    up, dn = h[1:] - h[:-1], l[:-1] - l[1:]
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = np.maximum(h[1:], c[:-1]) - np.minimum(l[1:], c[:-1])
    if not (np.isfinite(tr).all() and np.isfinite(pdm).all() and np.isfinite(mdm).all()):
        tr, pdm, mdm = np.nan_to_num(tr), np.nan_to_num(pdm), np.nan_to_num(mdm)
    s_tr, s_p, s_m = _wilder(tr, n), _wilder(pdm, n), _wilder(mdm, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi, mdi = 100 * s_p / s_tr, 100 * s_m / s_tr
        dx = np.where((s_tr > 0) & ((pdi + mdi) > 0), 100 * np.abs(pdi - mdi) / (pdi + mdi), 0.0)
    if len(dx) < n:
        return None
    return float(_wilder(dx, n)[-1])


def _extras(dts, high, low, close, vol, price: float, bo_date, last_is_today: bool = False, adx="calc") -> dict:
    """20일선 이격, ADX, 돌파일 거래량 배수(직전 50일 평균 대비)·종가 위치(DCR), HTF 여부,
    끌고 가기 점검용 5일선·50일선, 주도주 판정용 20일 평균 거래대금(원)."""
    out = {k: None for k in EXTRA_KEYS}
    n = len(close)
    if n >= 50:
        ma5, ma50 = float(close[-5:].mean()), float(close[-50:].mean())
        out.update(ma5=ma5, ma50=ma50, ma5_vs_50=(ma5 / ma50 - 1) * 100 if ma50 else None)
    # 거래대금 = 종가 × 거래량. 오늘 장중 봉은 덜 쌓였으니 빼고 직전 20거래일 평균
    end = n - 1 if last_is_today else n
    tv = close[max(0, end - 20):end] * vol[max(0, end - 20):end]
    tv = tv[np.isfinite(tv)]
    out["tv20"] = float(tv.mean()) if len(tv) >= 10 else None
    # 돈의 방향: 최근 5거래일 평균 거래대금 vs 그 전 20거래일 평균(오늘 장중 봉은 빼요)
    tv_all = close[:end] * vol[:end]
    if len(tv_all) >= 25:
        a5, p20 = tv_all[-5:], tv_all[-25:-5]
        a5, p20 = a5[np.isfinite(a5)], p20[np.isfinite(p20)]
        out["tv5"] = float(a5.mean()) if len(a5) >= 3 else None
        out["tvp20"] = float(p20.mean()) if len(p20) >= 10 else None
    # 오늘(마지막) 봉: 거래량 배수(직전 50일 평균 대비)·종가 위치(DCR)
    if n >= 21:
        base = vol[max(0, n - 51):n - 1]
        base = base[np.isfinite(base)]
        if len(base) >= 20 and base.mean() > 0 and np.isfinite(vol[-1]):
            out["vol_today"] = float(vol[-1] / base.mean())
        rng = high[-1] - low[-1]
        out["dcr_today"] = float((close[-1] - low[-1]) / rng * 100) if rng > 0 else 100.0
    if n >= 20:
        ma20 = float(close[-20:].mean())
        out["ma20"] = ma20
        out["dist_ma20"] = (price / ma20 - 1) * 100 if ma20 else None
    out["adx"] = _adx(high, low, close) if adx == "calc" else adx
    if bo_date is not None:
        idx = np.nonzero(dts == np.datetime64(bo_date, "D"))[0]
        if len(idx):
            t = int(idx[0])
            base = vol[max(0, t - 50):t]
            base = base[~np.isnan(base)]
            if len(base) >= 20 and base.mean() > 0 and vol[t] == vol[t]:
                out["bo_vol_ratio"] = float(vol[t] / base.mean())
            rng = high[t] - low[t]
            out["bo_dcr"] = float((close[t] - low[t]) / rng * 100) if rng > 0 else 100.0
    # HTF: 최근 60거래일 안에, 그 전 40거래일 최저가 대비 종가가 100% 넘게 오른 적이 있으면
    if n >= 60:
        lows_min = pd.Series(low).rolling(40, min_periods=20).min().shift(1).to_numpy()
        ratio = close[-60:] / lows_min[-60:]
        out["htf"] = bool(np.nanmax(ratio) >= 2.0) if np.isfinite(ratio).any() else False
    return out


# ─────────────────────────── RS · ATR · 신고가 돌파 유지 ───────────────────────────
ATR_DAYS = 20          # 20거래일 ATR
BO_KEYS = ("bo_date", "bo_level", "bo_days", "bo_status", "bo_vs", "bo_break_date", "bo_held", "bo_history", "bo_nth")
BO_MODES = {
    "line": "돌파선(직전 52주 최고가)",
    "close": "첫 신고가일 종가",
}


def _atr_np(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = ATR_DAYS) -> float | None:
    if len(close) < n + 1:
        return None
    prev = np.empty_like(close)
    prev[0] = np.nan
    prev[1:] = close[:-1]
    tr = np.fmax(high, prev) - np.fmin(low, prev)      # fmax/fmin은 빈 값(NaN)을 건너뛰어요
    atr = np.nanmean(tr[-n:])
    last = close[-1]
    return float(atr / last * 100) if last else None


def atr_pct(h: pd.DataFrame, n: int = ATR_DAYS) -> float | None:
    """최근 n거래일 평균 진폭(ATR)을 현재가 대비 %로. 진폭 = max(고가, 전일종가) − min(저가, 전일종가)."""
    return _atr_np(h["high"].to_numpy(dtype=float), h["low"].to_numpy(dtype=float),
                   h["close"].to_numpy(dtype=float), n)


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


def _rolling_prior_max(high: np.ndarray, lookback: int, minp: int) -> np.ndarray:
    """prior[t] = t 이전 lookback거래일 고가의 최고값(값이 minp개 미만이면 NaN). pandas rolling과 같은 결과."""
    return pd.Series(high).shift(1).rolling(lookback, min_periods=minp).max().to_numpy()


def _breakout_np(dts: np.ndarray, high: np.ndarray, close: np.ndarray, mode: str = "line",
                 lookback: int = 250) -> dict:
    out = {k: None for k in BO_KEYS}
    n = len(close)
    if n < 60:
        return out
    minp = lookback if n >= lookback + 20 else 60
    prior = _rolling_prior_max(high, lookback, minp).tolist()
    cl = close.tolist()

    runs = []          # 유지 구간: 돌파일, 돌파선, 이탈일
    cur = None
    for t in range(n):
        if cur is not None:
            if cl[t] < cur["level"]:
                cur["broken"] = t
                runs.append(cur)
                cur = None
            continue
        p = prior[t]
        if p == p and cl[t] > p:                       # 종가로 직전 52주 최고가 돌파
            cur = {"start": t, "level": float(p), "broken": None}
    if cur is not None:
        runs.append(cur)

    def day(i):
        return dts[i].astype(object)                   # datetime64[D] → datetime.date

    last = n - 1
    hist_rows = [{
        "돌파일": day(r["start"]),
        "돌파한 직전 52주 최고가": r["level"],
        "유지": f"{(last - r['start'] + 1) if r['broken'] is None else (r['broken'] - r['start'])}일",
        "이탈일": "-" if r["broken"] is None else day(r["broken"]),
    } for r in runs[-6:]][::-1]

    if cur is not None:                                   # 지금 유지 중
        # 몇 번째 돌파인지: 지금 돌파 전 250거래일(52주) 안에 다른 돌파(유지 구간)가 있었으면 2번째 이상
        out["bo_nth"] = 1 + sum(1 for r in runs[:-1] if r["start"] >= cur["start"] - lookback)
        out.update(
            bo_status="유지", bo_level=cur["level"], bo_date=day(cur["start"]),
            bo_days=last - cur["start"] + 1, bo_held=last - cur["start"] + 1,
            bo_vs=(cl[-1] / cur["level"] - 1) * 100 if cur["level"] else None,
        )
    else:                                                 # 지금의 52주 최고가 아래
        w0 = max(0, n - lookback)
        win = high[w0:]
        ref = float(win[int(np.nanargmax(win))])
        same = np.nonzero(win >= ref)[0]                  # 같은 최고가가 여러 번이면 가장 최근 날
        peak_i = int(same[-1]) + w0 if len(same) else int(np.nanargmax(win)) + w0
        above = np.nonzero(close[peak_i:] >= ref)[0]
        first_below = (int(above[-1]) + peak_i + 1) if len(above) else peak_i
        out.update(
            bo_status="이탈", bo_level=ref, bo_date=day(peak_i),
            bo_days=max(1, last - first_below + 1),
            bo_vs=(cl[-1] / ref - 1) * 100 if ref else None,
            bo_break_date=day(min(first_below, last)),
            bo_held=(runs[-1]["broken"] - runs[-1]["start"]) if runs and runs[-1]["broken"] is not None else None,
        )
    out["bo_history"] = hist_rows
    return out


def breakout_hold(h: pd.DataFrame, mode: str = "line", lookback: int = 250) -> dict:
    """직전 52주 최고가를 기준으로 '유지' 또는 '이탈'이 며칠째인지.

    - 유지: 종가가 직전 52주 최고가(그날 이전 250거래일 최고가)를 넘어선 날(돌파일)을 1일째로,
      그 뒤 종가가 한 번도 그 가격 아래로 내려가지 않은 거래일 수. 기준가 = 돌파한 직전 52주 최고가
    - 이탈: 유지 중이 아니면 기준가 = 지금의 52주 최고가. 그 가격 위에서 마감한 마지막 날 다음 날
      (위에서 마감한 적이 없으면 최고가를 찍은 날)을 1일째로 센 거래일 수
    """
    if h is None or len(h) < 60:
        return {k: None for k in BO_KEYS}
    return _breakout_np(h["date"].values.astype("datetime64[D]"), h["high"].to_numpy(dtype=float),
                        h["close"].to_numpy(dtype=float), mode, lookback)


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


# ─────────────────────────── 네이버 공식 52주 최고·최저 (대조·교정용) ───────────────────────────
H52_TOL = 0.3   # %. 일봉으로 계산한 값과 네이버 공식값이 이보다 더 차이 나면 '다르다'로 보고 공식값으로 고쳐요


def _pick_52w_items(items, found: dict):
    """[{code, key, value}, ...] 형식(integration의 totalInfos)에서 52주 최고·최저를 찾아요."""
    for it in items or []:
        if not isinstance(it, dict):
            continue
        lab = f'{it.get("code") or ""} {it.get("key") or ""} {it.get("title") or ""}'
        if "52" not in lab:
            continue
        v = _num(it.get("value"))
        if not v or v <= 0:
            continue
        if re.search(r"high|최고", lab, re.I):
            found.setdefault("high52", v)
        elif re.search(r"low|최저", lab, re.I):
            found.setdefault("low52", v)


def _find_52w_keys(node, found: dict):
    """필드 이름에 52와 high/low가 들어간 값을 찾아요(응답 모양이 바뀌어도 되도록)."""
    if isinstance(node, dict):
        if "value" in node and ("code" in node or "key" in node):
            _pick_52w_items([node], found)
        for k, v in node.items():
            kl = str(k).lower()
            if isinstance(v, (dict, list)):
                _find_52w_keys(v, found)
            elif "52" in kl:
                n = _num(v)
                if n and n > 0:
                    if "high" in kl or "max" in kl:
                        found.setdefault("high52", n)
                    elif "low" in kl or "min" in kl:
                        found.setdefault("low52", n)
    elif isinstance(node, list):
        for v in node:
            _find_52w_keys(v, found)


def _official_52w_one(code: str) -> dict:
    """한 종목의 네이버 공식 52주 최고·최저. ① 모바일 integration ② 종목 메인 페이지 ③ 종목 상세 API 순서."""
    found: dict = {}
    info = _get_json([f"{NAVER_MOBILE}/{code}/integration"], timeout=6)
    if isinstance(info, dict):
        _pick_52w_items(info.get("totalInfos"), found)
        if "high52" not in found:
            _find_52w_keys(info, found)
    if "high52" not in found:
        try:
            r = session.get(ITEM_MAIN_URL, params={"code": code}, timeout=6)
            r.raise_for_status()
            m = re.search(r"52주최고[\s\S]{0,300}?<em[^>]*>\s*([\d,]+)\s*</em>[\s\S]{0,200}?<em[^>]*>\s*([\d,]+)\s*</em>",
                          decode(r.content))
            if m:
                found["high52"], found["low52"] = to_num(m.group(1)), to_num(m.group(2))
        except requests.RequestException:
            pass
    if "high52" not in found:
        info = _get_json([f"{STOCK_API}/domestic/detail/{code}/detail"], params={"codeType": "KRX"}, timeout=6)
        if isinstance(info, dict):
            _find_52w_keys(info, found)
    return {k: float(v) for k, v in found.items() if v}


def fetch_official_52w(codes) -> dict[str, dict]:
    """국내 종목의 네이버 공식 52주 최고·최저 {코드: {high52, low52}}. 못 받은 종목은 빠져요(일봉 계산값을 그대로 써요)."""
    codes = [c for c in codes if is_kr(c)]
    if MOCK:
        return {}
    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=10) as pool:
        for code, got in zip(codes, pool.map(_official_52w_one, codes)):
            if got.get("high52"):
                out[code] = got
    return out


def fetch_kr_shares(codes) -> tuple[dict[str, int], dict]:
    """국내 상장주식수: ① 네이버 종목 상세 API ② 시가총액 순위표 ③ 종목 페이지 ④ 모바일 API 순서로 채워요.
    (결과, 출처별 개수) 를 돌려줘요."""
    codes = [c for c in codes if is_kr(c)]
    diag = {"total": len(codes), "상세API": 0, "순위표": 0, "종목페이지": 0, "모바일": 0}
    if MOCK:
        return {c: _rng(c).randint(10_000_000, 900_000_000) for c in codes}, {**diag, "순위표": len(codes)}
    result: dict[str, int] = {}
    many = len(codes) > 300           # (속도) 종목이 많으면 순위표(약 60쪽)로 한 번에 받고, 빠진 것만 종목별로
    detail_codes = [] if many else codes
    with ThreadPoolExecutor(max_workers=8) as pool:
        for code, n in zip(detail_codes, pool.map(_detail_api_shares, detail_codes)):
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
    if many:
        missing = [c for c in codes if c not in result]
        with ThreadPoolExecutor(max_workers=8) as pool:
            for code, n in zip(missing, pool.map(_detail_api_shares, missing)):
                if n:
                    result[code] = n
                    diag["상세API"] += 1
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
        got = _yf_batch(list(FX_TICKERS.values()), "5d", "1d")
        for cur, t in FX_TICKERS.items():
            if t in got and not got[t].empty:
                rates[cur] = float(got[t]["close"].iloc[-1])
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
    raw_vol = _num(basic.get("accumulatedTradingVolume")) or _num(pol.get("accumulatedTradingVolume"))
    raw_val = _num(basic.get("accumulatedTradingValue")) or _num(pol.get("accumulatedTradingValue"))
    return {"last": last, "diff": diff, "rate": rate, "time": basic.get("localTradedAt") or pol.get("localTradedAt"),
            "breadth": breadth, "deal": deal, "program": program, "status": status,
            "raw_vol": raw_vol, "raw_val": raw_val}


# ─────────────────────────── 코스피·코스닥 시장 거래대금 · 거래량 (평소 대비) ───────────────────────────
INDEX_DAY_URL = "https://finance.naver.com/sise/sise_index_day.naver"


def parse_index_day(text: str) -> list[dict]:
    """일별 지수 표 → [{date, close, vol(천주), val(백만원)}]."""
    rows = []
    for tr in re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", text):
        tds = re.findall(r"<td[^>]*>([\s\S]*?)</td>", tr)
        if len(tds) < 6:
            continue
        cells = [re.sub(r"<[^>]+>", "", t).strip() for t in tds]
        m = re.fullmatch(r"(\d{4})\.(\d{2})\.(\d{2})", cells[0])
        if not m:
            continue
        rows.append({"date": pd.Timestamp(f"{m.group(1)}-{m.group(2)}-{m.group(3)}"), "close": to_num(cells[1]),
                     "vol": to_num(cells[4]), "val": to_num(cells[5])})
    return rows


def fetch_index_turnover(code: str, pages: int = 11) -> pd.DataFrame:
    """지수의 일별 거래량(주)·거래대금(원) 약 60거래일. 오늘 행은 장중이면 지금까지 누적이에요."""
    if MOCK:
        rng = _rng(code + "turn")
        dates = pd.bdate_range(end=now_kst().date(), periods=66)
        base_v, base_a = (560e6, 14e12) if code == "KOSPI" else (1050e6, 9.5e12)
        rows = [{"date": d, "close": 0.0, "vol": base_v * rng.uniform(0.6, 1.5), "val": base_a * rng.uniform(0.6, 1.5)}
                for d in dates]
        return pd.DataFrame(rows)

    def one(page):
        try:
            r = session.get(INDEX_DAY_URL, params={"code": code, "page": page}, timeout=8)
            r.raise_for_status()
            return parse_index_day(decode(r.content))
        except requests.RequestException:
            return []

    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = [x for part in pool.map(one, range(1, pages + 1)) for x in part]
    if not rows:
        return pd.DataFrame(columns=["date", "close", "vol", "val"])
    df = pd.DataFrame(rows).drop_duplicates("date").sort_values("date").reset_index(drop=True)
    df["vol"] = df["vol"] * 1e3      # 천주 → 주
    df["val"] = df["val"] * 1e6      # 백만원 → 원
    return df


def _fit_unit(raw: float | None, ref: float | None, mults=(1.0, 1e3, 1e6)) -> float | None:
    """실시간 값의 단위를 모를 때 평소 값과 비슷한 자릿수가 되는 배수를 골라요."""
    if not raw or not ref:
        return None
    frac = session_frac()
    for m in mults:
        x = raw * m / (ref * frac)
        if 0.15 <= x <= 6:
            return raw * m
    return None


def market_turnover(hist: pd.DataFrame, live: dict | None, today: date | None = None) -> dict | None:
    """오늘(지금까지) 거래대금·거래량과 5·20·60일 평균, 같은 시각 평소 대비, 마감 예상."""
    if hist is None or hist.empty:
        return None
    today = today or now_kst().date()
    h = hist.copy()
    is_today = h["date"].dt.date == today
    past = h[~is_today].tail(60)
    if len(past) < 5:
        return None
    frac = session_frac()
    out = {"frac": frac, "open": frac < 1 and now_kst().weekday() < 5, "date": today}
    for key in ("val", "vol"):
        s_ = past[key]
        a5, a20, a60 = s_.tail(5).mean(), s_.tail(20).mean(), s_.mean()
        now_v = None
        raw = (live or {}).get("raw_val" if key == "val" else "raw_vol")
        if raw:
            now_v = _fit_unit(raw, a20)
        if now_v is None and is_today.any():
            now_v = float(h.loc[is_today, key].iloc[-1])
        if now_v is None and not out["open"]:        # 장 마감·주말: 마지막 거래일
            now_v = float(h[key].iloc[-1])
        proj = now_v / frac if now_v else None
        out[key] = {"now": now_v, "a5": a5, "a20": a20, "a60": a60,
                    "same_time": a20 * frac, "x_now": (now_v / (a20 * frac)) if now_v else None,
                    "proj": proj, "vs5": (proj / a5 - 1) * 100 if proj else None,
                    "vs20": (proj / a20 - 1) * 100 if proj else None, "vs60": (proj / a60 - 1) * 100 if proj else None,
                    "last20": past[["date", key]].tail(20).rename(columns={key: "v"}).to_dict("records")}
    return out


def fetch_market_turnover_hist() -> dict[str, pd.DataFrame]:
    with ThreadPoolExecutor(max_workers=2) as pool:
        return dict(zip(MARKETS, pool.map(fetch_index_turnover, MARKETS.values())))


# ─────────────────────────── 코스피·코스닥 전체 종목 목록 ───────────────────────────
KIND_LIST_URL = "https://kind.krx.co.kr/corpgeneral/corpList.do"
MARKET_SUM_URL = "https://finance.naver.com/sise/sise_market_sum.naver"


def _kind_market(mt: str) -> list[dict]:
    try:
        r = session.get(KIND_LIST_URL, params={"method": "download", "searchType": "13", "marketType": mt}, timeout=20)
        r.raise_for_status()
        text = r.content.decode("cp949", errors="ignore")
    except requests.RequestException:
        return []
    rows = []
    for tr in re.findall(r"<tr>([\s\S]*?)</tr>", text):
        tds = [re.sub(r"<[^>]+>", "", t).strip() for t in re.findall(r"<td[^>]*>([\s\S]*?)</td>", tr)]
        if len(tds) >= 4 and re.fullmatch(r"[0-9A-Z]{1,6}", tds[2] or ""):
            rows.append({"name": tds[0], "code": tds[2].zfill(6), "industry": tds[3], "product": tds[4] if len(tds) > 4 else ""})
    return rows


def _naver_market(sosok: int, max_pages: int = 60) -> list[dict]:
    """KIND가 막혔을 때: 네이버 시가총액 순위 페이지에서 이름·코드."""
    rows, seen = [], set()
    for page in range(1, max_pages + 1):
        try:
            r = session.get(MARKET_SUM_URL, params={"sosok": sosok, "page": page}, timeout=8)
            text = decode(r.content)
        except requests.RequestException:
            break
        got = re.findall(r'href="/item/main\.naver\?code=(\w{6})" class="tltle">([^<]+)</a>', text)
        new = [(c, n) for c, n in got if c not in seen]
        if not new:
            break
        for c, n in new:
            seen.add(c)
            rows.append({"name": n.strip(), "code": c, "industry": "", "product": ""})
    return rows


UNIVERSE_DIAG: dict = {}       # 어디서 몇 개 받았는지(못 받았을 때 화면에 이유를 보여줘요)


def _naver_mobile_market(market: str, page_size: int = 100, max_pages: int = 40) -> list[dict]:
    """네이버 모바일 시가총액 순위 API(가장 잘 되는 곳). market = KOSPI / KOSDAQ."""
    rows, seen = [], set()
    for page in range(1, max_pages + 1):
        try:
            r = session.get(f"https://m.stock.naver.com/api/stocks/marketValue/{market}",
                            params={"page": page, "pageSize": page_size}, headers=JSON_HEADERS, timeout=10)
            r.raise_for_status()
            j = r.json()
        except (requests.RequestException, ValueError):
            break
        got = []

        def pick(d):
            code = d.get("itemCode") or d.get("reutersCode") or d.get("code")
            name = d.get("stockName") or d.get("itemName") or d.get("name")
            if code and name and re.fullmatch(r"\w{6}", str(code)):
                kind = str(d.get("stockEndType") or "stock").lower()
                got.append({"code": str(code), "name": str(name).strip(), "kind": kind,
                            "industry": str(d.get("industryCodeName") or d.get("sosokName") or ""), "product": ""})

        def walk(n):
            if isinstance(n, dict):
                pick(n)
                for v in n.values():
                    walk(v)
            elif isinstance(n, list):
                for v in n:
                    walk(v)
        walk(j)
        new = [x for x in got if x["code"] not in seen]
        if not new:
            break
        for x in new:
            seen.add(x["code"])
            rows.append(x)
        if len(got) < page_size:
            break
    return [x for x in rows if x["kind"] in ("stock", "")]


def fetch_upjong_map() -> dict[str, str]:
    """네이버 업종 분류 {코드: 업종명}. 전체 종목에 세부 분류를 붙일 때 써요(업종 80여 개 페이지)."""
    try:
        r = session.get("https://finance.naver.com/sise/sise_group.naver", params={"type": "upjong"}, timeout=10)
        text = decode(r.content)
    except requests.RequestException:
        return {}
    groups = re.findall(r'href="/sise/sise_group_detail\.naver\?type=upjong&(?:amp;)?no=(\d+)"[^>]*>([^<]+)</a>', text)

    def one(g):
        no, name = g
        try:
            r = session.get("https://finance.naver.com/sise/sise_group_detail.naver",
                            params={"type": "upjong", "no": no}, timeout=10)
            codes = set(re.findall(r'/item/main\.naver\?code=(\w{6})', decode(r.content)))
            return name.strip(), codes
        except requests.RequestException:
            return name.strip(), set()

    out: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for name, codes in pool.map(one, groups):
            for c in codes:
                out.setdefault(c, name)
    return out


# 공식 업종 이름(네이버 업종 · 한국거래소 업종) → 앱의 산업 칸. 위에서부터 먼저 맞는 규칙을 써요.
# 반도체·조선·화장품·건설·바이오·금융처럼 원래 있던 산업과 뜻이 똑같을 때만 원래 칸에 넣고,
# 2차전지·원전·OLED처럼 '업종'이 아니라 '테마'인 것은 업종으로 추측하지 않아요(테마는 네이버 테마 태그로 따로 붙여요).
INDUSTRY_RULES = [
    (r"반도체", "반도체"),
    (r"디스플레이", "디스플레이"),
    (r"조선|선박", "조선"),
    (r"우주항공|국방|항공기|무기", "방산·우주항공"),
    (r"화장품", "화장품"),
    (r"건설|건축|토목", "건설"),
    (r"제약|의약|생물공학|바이오|건강관리|생명과학|의료", "바이오·헬스케어"),
    (r"은행|증권|보험|카드|창업투자|금융|신탁|복합기업|지주", "금융·지주"),
    (r"전기유틸리티|가스유틸리티|복합유틸리티|유틸리티|석유|가스|에너지|전기업|발전", "에너지·유틸리티"),
    (r"전기장비|전기제품|전기 장비|전동기|발전기|전선|절연|축전지|일차전지", "전기·전자장비"),
    (r"통신장비|통신 및 방송 장비|핸드셋|컴퓨터|주변기기|전자장비|전자제품|전자부품|사무용전자|전자기기|계측|하드웨어", "IT하드웨어"),
    (r"카탈로그소매|전자상거래|통신판매|온라인 소매", "유통·무역"),
    (r"소프트웨어|IT서비스|정보서비스|자료처리|호스팅|포털|인터넷|양방향미디어", "소프트웨어·인터넷"),
    (r"게임|방송|엔터테인먼트|광고|출판|영화|미디어|음악|오디오물", "미디어·엔터·게임"),
    (r"무선통신|다각화된통신|전기 통신|통신서비스|통신업", "통신"),
    (r"화학|고무|플라스틱|비료|합성", "화학"),
    (r"철강|비철금속|금속|1차 철강|주조", "철강·금속"),
    (r"종이|목재|포장|펄프", "종이·포장"),
    (r"자동차|타이어|트레일러", "자동차"),
    (r"가정용품|섬유|의류|신발|호화품|레저용|가구|문구|가방|귀금속|완구|생활용품", "소비재"),
    (r"기계|장비", "기계"),
    (r"항공사|해운|운송|물류|철도|도로|창고", "운송·물류"),
    (r"식품|음료|담배|식료품|도축|곡물", "음식료"),
    (r"가정용품|섬유|의류|신발|호화품|레저용|가구|문구|가방|귀금속|완구|생활용품", "소비재"),
    (r"호텔|레스토랑|레저|교육|다각화된소비자|여행|숙박|음식점|오락", "소비자 서비스"),
    (r"백화점|판매업체|소매|도매|무역|상품 중개|유통|전문소매", "유통·무역"),
    (r"상업서비스|사업지원|인력|경비|전문 서비스|연구개발|엔지니어링", "상업서비스"),
    (r"부동산|리츠", "부동산"),
    (r"자본재", "산업재"),
    (r"내구소비재", "소비재"),
    (r"^소재$|소재 ", "화학"),
]


def classify_industry(industry: str) -> str:
    """공식 업종 이름을 앱의 산업 칸으로 옮겨요. 맞는 규칙이 없으면 '기타 업종'."""
    name = industry or ""
    for pat, sector in INDUSTRY_RULES:
        if re.search(pat, name):
            return sector
    return "기타 업종"


def fetch_theme_map(max_pages: int = 10) -> dict[str, list[str]]:
    """네이버 테마 {테마 이름: [종목코드]}. 2차전지·원전·방산처럼 업종으로 안 나뉘는 흐름을 태그로 붙일 때 써요."""
    if MOCK:
        return {"2차전지(소재/부품)": ["900001", "900004", "042700"], "원자력발전": ["900002", "900005"],
                "방위산업/전쟁 및 테러": ["900003", "012450"]}
    themes = []
    for page in range(1, max_pages + 1):
        try:
            r = session.get("https://finance.naver.com/sise/theme.naver", params={"page": page}, timeout=10)
            got = re.findall(r'href="/sise/sise_group_detail\.naver\?type=theme&(?:amp;)?no=(\d+)"[^>]*>([^<]+)</a>', decode(r.content))
        except requests.RequestException:
            break
        new = [g for g in got if g not in themes]
        if not new:
            break
        themes += new

    def one(g):
        no, name = g
        try:
            r = session.get("https://finance.naver.com/sise/sise_group_detail.naver", params={"type": "theme", "no": no},
                            timeout=10)
            return _html_unescape(name.strip()), sorted(set(re.findall(r'/item/main\.naver\?code=(\w{6})', decode(r.content))))
        except requests.RequestException:
            return _html_unescape(name.strip()), []

    out = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for name, codes in pool.map(one, themes):
            if codes:
                out[name] = codes
    return out


def _html_unescape(t: str) -> str:
    import html as _h
    return _h.unescape(t)


WICS_MID = {"G1010": "에너지", "G1510": "소재", "G2010": "자본재", "G2020": "상업서비스와공급품", "G2030": "운송",
            "G2510": "자동차와부품", "G2520": "내구소비재와의류", "G2530": "호텔,레스토랑,레저 등", "G2550": "소매(유통)",
            "G2560": "교육서비스", "G3010": "식품과기본식료품소매", "G3020": "식품,음료,담배", "G3030": "가정용품과개인용품",
            "G3510": "건강관리장비와서비스", "G3520": "제약과생물공학", "G4010": "은행", "G4020": "증권",
            "G4030": "다각화된금융", "G4040": "보험", "G4050": "부동산", "G4510": "소프트웨어와서비스",
            "G4520": "기술하드웨어와장비", "G4530": "반도체와반도체장비", "G4535": "전자와 전기제품", "G4540": "디스플레이",
            "G5010": "전기통신서비스", "G5020": "미디어와엔터테인먼트", "G5510": "유틸리티"}


def _wics_map() -> dict[str, str]:
    """WICS(에프앤가이드 산업분류) 중분류 {코드: 업종}. 네이버 업종을 못 받을 때 쓰는 두 번째 공식 분류."""
    out: dict[str, str] = {}
    day = now_kst().date()
    for back in range(0, 8):                         # 가장 가까운 영업일 자료
        dt = (day - timedelta(days=back)).strftime("%Y%m%d")

        def one(item):
            cd, name = item
            try:
                r = session.get("https://www.wiseindex.com/Index/GetIndexComponets",
                                params={"ceil_yn": 0, "dt": dt, "sec_cd": cd}, timeout=10)
                j = r.json()
            except (requests.RequestException, ValueError):
                return name, []
            rows = j.get("list") if isinstance(j, dict) else None
            return name, [str(x.get("CMP_CD")) for x in rows or [] if x.get("CMP_CD")]

        with ThreadPoolExecutor(max_workers=6) as pool:
            for name, codes in pool.map(one, WICS_MID.items()):
                for c in codes:
                    out.setdefault(c.zfill(6), name)
        if len(out) > 300:
            break
    return out


def _naver_mobile_industry_map() -> dict[str, str]:
    """네이버 모바일 업종 API {코드: 업종}(응답 모양이 바뀌어도 되게 넓게 찾아요)."""
    try:
        j = session.get("https://m.stock.naver.com/api/stocks/industry", headers=JSON_HEADERS, timeout=10).json()
    except (requests.RequestException, ValueError):
        return {}
    groups = []

    def walk(n):
        if isinstance(n, dict):
            no = n.get("no") or n.get("industryCode") or n.get("groupCode")
            name = n.get("name") or n.get("industryName") or n.get("groupName")
            if no and name and not n.get("itemCode"):
                groups.append((str(no), str(name)))
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(j)

    def one(g):
        no, name = g
        codes = set()
        for page in range(1, 6):
            try:
                jj = session.get(f"https://m.stock.naver.com/api/stocks/industry/{no}",
                                 params={"page": page, "pageSize": 100}, headers=JSON_HEADERS, timeout=10).json()
            except (requests.RequestException, ValueError):
                break
            found = set(re.findall(r'"itemCode"\s*:\s*"(\w{6})"', json.dumps(jj)))
            if not found - codes:
                break
            codes |= found
        return name, codes

    out: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for name, codes in pool.map(one, groups[:120]):
            for c in codes:
                out.setdefault(c, name)
    return out


def fetch_industry_map(codes=None) -> tuple[dict[str, str], dict]:
    """전 종목 업종 {코드: 공식 업종 이름}과 출처별 개수.
    ① 네이버 업종(PC) ② 네이버 모바일 업종 ③ WICS 순서로, 앞에서 빠진 종목만 다음 출처로 채워요."""
    if MOCK:
        names = ["반도체와반도체장비", "제약", "소프트웨어", "증권", "화학", "자동차부품", "조선", "디스플레이장비및부품",
                 "식품", "전기제품", "게임엔터테인먼트", "기계", "우주항공과국방", "해운사", "호텔,레스토랑,레저"]
        got = {c: names[int(c[-3:]) % len(names)] for c in (codes or [])}
        return got, {"네이버 업종": len(got)}
    want = set(codes or [])
    out: dict[str, str] = {}
    diag: dict[str, int] = {}
    for label, fn in (("네이버 업종", fetch_upjong_map), ("네이버 모바일 업종", _naver_mobile_industry_map),
                      ("WICS", _wics_map)):
        try:
            got = fn()
        except Exception:
            got = {}
        n0 = len(out)
        for c, name in got.items():
            if c not in out and name:
                out[c] = name.strip()
        diag[label] = len(out) - n0
        if want and len(want - set(out)) <= max(20, len(want) * 0.02):
            break
    miss = [c for c in want if c not in out][:1500]
    if miss:                                          # 남은 종목은 종목 페이지의 '업종' 링크에서 하나씩
        with ThreadPoolExecutor(max_workers=8) as pool:
            got = dict(zip(miss, pool.map(_item_page_industry, miss)))
        n0 = len(out)
        out.update({c: n for c, n in got.items() if n})
        diag["종목 페이지"] = len(out) - n0
    return out, diag


def _item_page_industry(code: str) -> str:
    try:
        r = session.get(ITEM_MAIN_URL, params={"code": code}, timeout=8)
        m = re.search(r'type=upjong&(?:amp;)?no=\d+"[^>]*>([^<]+)</a>', decode(r.content))
        return _html_unescape(m.group(1).strip()) if m else ""
    except requests.RequestException:
        return ""


def fetch_krx_universe() -> list[dict]:
    """코스피·코스닥 상장 종목 전체 [{code, name, market, industry, product}]. 스팩·ETF·ETN·우선주는 빼요.
    네이버 모바일 → 한국거래소 KIND → 네이버 PC 순서로 시도해요(해외 서버에서 막히는 곳이 있어서)."""
    if MOCK:
        rng = _rng("universe")
        return [{"code": f"9{i:05d}", "name": f"테스트{'피' if i % 3 else '닥'}{i}", "market": "코스피" if i % 3 else "코스닥",
                 "industry": "" if i % 2 == 0 else rng.choice(["반도체와반도체장비", "제약", "소프트웨어", "증권", "화학", "자동차부품", "조선",
                                         "디스플레이장비및부품", "식품", "전기제품", "게임엔터테인먼트", "기계"]),
                 "product": "테스트 제품"} for i in range(1, int(os.environ.get("MOCK_UNIVERSE", "300")) + 1)]
    out = []
    UNIVERSE_DIAG.clear()
    for label, mob, mt, sosok in (("코스피", "KOSPI", "stockMkt", 0), ("코스닥", "KOSDAQ", "kosdaqMkt", 1)):
        rows, src = [], ""
        for src, fn in (("네이버 모바일", lambda: _naver_mobile_market(mob)), ("한국거래소 KIND", lambda: _kind_market(mt)),
                        ("네이버 PC", lambda: _naver_market(sosok))):
            try:
                rows = fn()
            except Exception:
                rows = []
            if len(rows) >= 50:
                break
        UNIVERSE_DIAG[label] = f"{src} {len(rows)}개" if rows else "못 받음"
        for x in rows:
            if "스팩" in x["name"] or re.search(r"(ETF|ETN|리츠)$", x["name"]) or not x["code"].endswith("0"):
                continue                      # 우선주(코드 끝이 0이 아님)·스팩·ETF 빼기
            out.append({k: v for k, v in x.items() if k != "kind"} | {"market": label})
    if out and sum(1 for x in out if x.get("industry")) < len(out) * 0.5:     # 업종이 비었으면 네이버 업종으로 채워요
        up = fetch_upjong_map()
        UNIVERSE_DIAG["업종"] = f"네이버 업종 {len(set(up.values()))}개" if up else "업종 못 받음"
        for x in out:
            if not x.get("industry"):
                x["industry"] = up.get(x["code"], "")
    uniq = {}
    for x in out:
        uniq.setdefault(x["code"], x)
    return list(uniq.values())


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
                         "program": rng.randint(-3000, 3000), "status": "OPEN",
                         "raw_vol": (560_000 if code == "KOSPI" else 1_050_000) * session_frac() * rng.uniform(0.7, 1.5),
                         "raw_val": (14_000_000 if code == "KOSPI" else 9_500_000) * session_frac() * rng.uniform(0.7, 1.6)}
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


_bg_pool = ThreadPoolExecutor(max_workers=6)     # 화면을 멈추지 않고 뒤에서 새로 받는 일꾼
_trend_refreshing: set[str] = set()


def _refresh_trend(code: str, days: int):
    old = _trend_cache.get(code)
    try:
        df = _fetch_stock_trend_now(code, days)
        if df.empty and old is not None and not old[1].empty:   # 새로 받기 실패 → 예전 값 두고 1분 뒤 다시
            _trend_cache[code] = (_time.time() - TREND_TTL + 60, old[1])
    finally:
        _trend_refreshing.discard(code)


def fetch_stock_trend(code: str, days: int = 20) -> pd.DataFrame:
    """종목 하나의 최근 일별 개인·외국인·기관 순매수. 10분 동안 기억해요.

    (속도) 10분이 지난 종목은 기다리지 않고 예전 값을 먼저 보여준 뒤, 뒤에서 새로 받아요.
    """
    hit = _trend_cache.get(code)
    if hit:
        if _time.time() - hit[0] >= TREND_TTL and code not in _trend_refreshing:
            _trend_refreshing.add(code)
            _bg_pool.submit(_refresh_trend, code, days)
        return hit[1]
    return _fetch_stock_trend_now(code, days)


def _fetch_stock_trend_now(code: str, days: int = 20) -> pd.DataFrame:
    if MOCK:
        df = mock_trend(code, days)
    else:
        payload = _get_json([f"{STOCK_API}/domestic/detail/{code}/trend", f"{M_API}/stock/{code}/trend"],
                            params={"tradeType": "KRX", "startIdx": 0, "pageSize": days}, timeout=6)
        df = parse_stock_trend(payload)
    # 못 받은 종목(빈 표)은 10분이 아니라 1분 뒤에 다시 받아요
    _trend_cache[code] = (_time.time() - (TREND_TTL - 60 if df is None or df.empty else 0), df)
    return df


def fetch_stock_trends(codes, workers: int = 12) -> dict[str, pd.DataFrame]:
    codes = [c for c in codes if is_kr(c)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        out = dict(zip(codes, pool.map(fetch_stock_trend, codes)))
    miss = [c for c, d in out.items() if d is None or d.empty]
    if miss and not MOCK and len(miss) < len(codes):        # 일부만 빠졌으면(네이버가 잠깐 막음) 천천히 한 번 더
        _time.sleep(1.5)
        with ThreadPoolExecutor(max_workers=4) as pool:
            out.update(zip(miss, pool.map(_fetch_stock_trend_now, miss)))
    return out


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


_metrics_memo: dict[str, tuple] = {}


def _metrics_cached(code: str, hist, quote, monthly, bo_mode: str, official: dict | None = None) -> dict:
    """일봉·월봉이 같은 객체이고 시세(현재가·전일·고가·저가)도 그대로면 지난 계산 결과를 다시 써요.
    장 마감 뒤나 거래가 뜸한 종목은 30초마다 다시 계산하지 않아도 돼요."""
    qkey = (quote.get("price"), quote.get("prev"), quote.get("high"), quote.get("low")) if quote else None
    today = now_kst().date()
    okey = (official.get("high52"), official.get("low52")) if official else None
    m = _metrics_memo.get(code)
    if (m and m[0] is hist and m[1] is monthly and m[2] == qkey and m[3] == today and m[4] == bo_mode
            and m[5] == okey):
        return m[6]
    res = compute_metrics(hist, quote, today=today, bo_mode=bo_mode, monthly=monthly, official=official)
    _metrics_memo[code] = (hist, monthly, qkey, today, bo_mode, okey, res)
    return res


# ─────────────────────────── 실시간 거래량 · 섹터 거래대금 쏠림 ───────────────────────────
SESSION_MIN = 390   # 09:00~15:30


def session_frac(now: datetime | None = None) -> float:
    """정규장 중 지금까지 지난 비율(0.05~1). 장 전·장 뒤·주말은 1(하루치 전체)."""
    now = now or now_kst()
    if now.weekday() >= 5:
        return 1.0
    t = now.hour * 60 + now.minute - 9 * 60
    if t <= 0 or t >= SESSION_MIN:
        return 1.0
    return max(0.05, t / SESSION_MIN)


def live_volume(hist: pd.DataFrame | None, quote: dict | None, metrics: dict) -> dict:
    """오늘(또는 최근 거래일) 거래량·거래대금과 평소(직전 20일 평균, 지금 시각까지로 환산) 대비 배수."""
    out = {"vol_live": None, "tv_live": None, "tv_x": None, "vol_src": None}
    price = metrics.get("price")
    vol, val, src = None, None, None
    if quote and quote.get("volume"):
        vol, val, src = float(quote["volume"]), quote.get("value"), "실시간"
    elif hist is not None and not hist.empty and "volume" in hist:
        v = hist["volume"].iloc[-1]
        if v == v:
            vol, src = float(v), "최근 거래일"
            price = float(hist["close"].iloc[-1])
    if not vol or not price:
        return out
    est = price * vol
    tv = est
    if val:   # 네이버 누적 거래대금: 원 단위면 그대로, 백만원 단위면 환산. 어긋나면 현재가×거래량
        for mult in (1.0, 1e6):
            if 0.5 <= val * mult / est <= 2.0:
                tv = val * mult
                break
    tv20 = metrics.get("tv20")
    frac = session_frac() if src == "실시간" else 1.0
    out.update(vol_live=vol, tv_live=tv, vol_src=src,
               tv_x=(tv / (tv20 * frac)) if tv20 else None)
    return out


def sector_money(df: pd.DataFrame, by: str = "group", top_names: int = 3) -> pd.DataFrame:
    """섹터(세부 분류 또는 산업)별 지금 거래대금 합계, 비중, 평소 대비 배수, 등락, 거래대금 상위 종목."""
    d = df[pd.to_numeric(df.get("tv_live"), errors="coerce").notna()].copy() if "tv_live" in df else df.iloc[0:0]
    if d.empty:
        return pd.DataFrame(columns=["sector", "tv", "share", "x", "chg", "n", "up", "down", "top"])
    d["tv_live"] = pd.to_numeric(d["tv_live"], errors="coerce")
    d["tv_base"] = pd.to_numeric(d["tv_live"] / d["tv_x"], errors="coerce")
    total = d["tv_live"].sum()
    rows = []
    for key, g in d.groupby(by):
        tv = g["tv_live"].sum()
        has = g["tv_base"].notna() & (g["tv_base"] > 0)
        base = g.loc[has, "tv_base"].sum() if has.any() else None
        tv_b = g.loc[has, "tv_live"].sum() if has.any() else None
        chg = pd.to_numeric(g["change"], errors="coerce")
        w = (chg * g["tv_live"]).sum() / g.loc[chg.notna(), "tv_live"].sum() if chg.notna().any() else None
        top = g.sort_values("tv_live", ascending=False).head(top_names)
        rows.append({
            "sector": key, "tv": tv, "share": tv / total * 100 if total else None,
            "x": (tv_b / base) if base else None, "chg": w,
            "n": len(g), "up": int((chg > 0).sum()), "down": int((chg < 0).sum()),
            "top": " · ".join(f"{r['name']}({r['change']:+.1f}%)" if r["change"] == r["change"] else r["name"]
                              for _, r in top.iterrows()),
        })
    return pd.DataFrame(rows).sort_values("tv", ascending=False).reset_index(drop=True)


def is_halted(hist: pd.DataFrame | None, quote: dict | None, days: int = 5) -> bool:
    """거래정지(또는 사실상 거래가 없는) 종목: 최근 5거래일 거래량이 모두 0이고 오늘도 거래가 없어요.
    이런 종목은 가격이 멈춰 있어서 52주 최고 = 현재가, 신고가까지 0%처럼 보이지만 실제로는 살 수 없어요."""
    if hist is None or len(hist) < days or "volume" not in hist:
        return False
    v = hist["volume"].to_numpy()[-days:]
    try:
        if (np.nan_to_num(v.astype(float)) > 0).any():
            return False
    except (TypeError, ValueError):
        return False
    return not (quote and (quote.get("volume") or 0) > 0)


def build_table(stocks: list[dict], histories: dict, quotes: dict,
                shares: dict | None = None, fx: dict | None = None, bo_mode: str = "line",
                monthlies: dict | None = None, official52: dict | None = None) -> pd.DataFrame:
    rows = []
    for s in stocks:
        naver_name, hist, error = histories.get(s["code"], (None, empty_frame(), "조회 안 됨"))
        metrics = _metrics_cached(s["code"], hist, quotes.get(s["code"]), (monthlies or {}).get(s["code"]), bo_mode,
                                  (official52 or {}).get(s["code"]))
        market, currency = market_of(s["code"])
        n_shares = (shares or {}).get(s["code"])
        cap_local = metrics["price"] * n_shares if (metrics["price"] and n_shares) else None
        rate = (fx or {}).get(currency)
        live = live_volume(hist, quotes.get(s["code"]), metrics)
        rows.append({
            **s,
            **metrics,
            **live,
            "halted": is_halted(hist, quotes.get(s["code"])),
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
    return add_leader_ranks(add_rs_ranks(pd.DataFrame(rows)))


LEADER_RS_MIN = 70


def add_leader_ranks(df: pd.DataFrame) -> pd.DataFrame:
    """섹터(세부 분류) 안 주도주 순위. 원칙: 그 섹터에서 가장 강하고(RS) 시총이 크고 거래량이 많은(거래대금) 종목.

    국내 종목끼리 분류마다 RS·시가총액·20일 평균 거래대금을 각각 백분위로 바꿔 똑같이 1/3씩 더해요.
    값이 없는 항목은 그 분류에서 가장 낮은 것으로 쳐요(모르는 걸 좋게 보지 않기). 1위 = 대장주.
    단, '가장 강한 종목'이 먼저라서 RS 70 미만은 시총·거래대금이 아무리 커도 RS 70 이상 종목들 뒤로 가요.
    분류에 RS 70 이상이 하나도 없으면 대장주가 없어요(lead_ok=False).
    """
    df = df.copy()
    for col in ("lead_rank", "lead_n", "lead_score", "lead_rs_rank", "lead_cap_rank", "lead_tv_rank"):
        df[col] = np.nan
    df["lead_ok"] = False
    if df.empty:
        return df
    kr = df["code"].map(is_kr) & df["price"].notna()
    sub = df.loc[kr, ["group", "rs", "cap_krw", "tv20"]].copy()
    if sub.empty:
        return df
    # (속도) 분류별 순위를 groupby로 한 번에 계산해요(예전엔 분류마다 반복)
    g = sub.groupby("group")
    score = pd.Series(0.0, index=sub.index)
    for key, col in (("lead_rs_rank", "rs"), ("lead_cap_rank", "cap_krw"), ("lead_tv_rank", "tv20")):
        vals = pd.to_numeric(sub[col], errors="coerce")
        sub[col] = vals
        df.loc[sub.index, key] = vals.groupby(sub["group"]).rank(ascending=False, method="min", na_option="bottom")
        score += vals.groupby(sub["group"]).rank(pct=True, method="average").fillna(0.0) / 3
    rs = sub["rs"].fillna(-1)
    strong = rs >= LEADER_RS_MIN
    tmp = pd.DataFrame({"group": sub["group"], "weak": ~strong, "neg_score": -score.round(9), "neg_rs": -rs},
                       index=sub.index).sort_values(["group", "weak", "neg_score", "neg_rs"], kind="mergesort")
    df.loc[tmp.index, "lead_rank"] = tmp.groupby("group").cumcount().to_numpy() + 1
    df.loc[sub.index, "lead_score"] = score
    df.loc[sub.index, "lead_ok"] = strong.to_numpy()
    df.loc[sub.index, "lead_n"] = g["group"].transform("size").to_numpy()
    return df


def sector_leaders(df: pd.DataFrame, groups) -> pd.DataFrame:
    """분류마다 주도주 1위 행."""
    d = df[df["group"].isin(list(groups)) & (df["lead_rank"] == 1) & (df["lead_ok"] == True)]  # noqa: E712
    return d.sort_values("lead_score", ascending=False)


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
    if rng.random() < 0.12:        # 거래량 폭발 탭 확인용: 몇 달 조용하다가 최근 며칠 터진 모양
        k = rng.randint(1, 4)
        base = rng.randint(50_000, 300_000)
        for i in range(len(rows) - 110, len(rows)):
            r = list(rows[i])
            r[5] = int(base * rng.uniform(0.6, 1.4)) if i < len(rows) - k else int(base * rng.uniform(3, 9))
            rows[i] = tuple(r)
    return rows_to_frame(rows)


_mock_hist: dict[str, pd.DataFrame] = {}


def mock_quote(code: str) -> dict:
    hist = _mock_hist.get(code)
    if hist is None:
        hist = _mock_hist[code] = mock_history(code)
    last = hist.iloc[-1]
    return {"price": float(last["close"]), "prev": float(hist.iloc[-2]["close"]),
            "high": float(last["high"]), "low": float(last["low"]), "status": "OPEN",
            "volume": float(last["volume"]) * _rng(code + "v").uniform(0.3, 2.5)}


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


# ─────────────────────────── 앱 안 캔들 차트(일·주·월봉) ───────────────────────────
CHART_TF = {"일봉": ("day", 800, "5y", "1d"), "주봉": ("week", 520, "10y", "1wk"), "월봉": ("month", 400, "max", "1mo")}


def _resample(d: pd.DataFrame, rule: str) -> pd.DataFrame:
    g = d.set_index("date").resample(rule)
    out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                        "close": g["close"].last(), "volume": g["volume"].sum()}).dropna(subset=["close"])
    first_day = d.set_index("date")["close"].resample(rule).apply(lambda x: x.index.min() if len(x) else pd.NaT)
    out.index = first_day.loc[out.index].values
    return out.rename_axis("date").reset_index()


def fetch_chart(code: str, tf: str = "일봉") -> pd.DataFrame:
    """캔들 차트용 봉(날짜·시가·고가·저가·종가·거래량). 국내는 네이버, 해외는 야후. 못 받으면 빈 표."""
    kind, count, period, interval = CHART_TF.get(tf, CHART_TF["일봉"])
    if MOCK:
        d = mock_history(code, 520)
        return d if tf == "일봉" else _resample(d, "W-FRI" if tf == "주봉" else "MS")
    try:
        if is_kr(code) or code in ("KOSPI", "KOSDAQ", "KPI200"):       # 국내 종목 · 코스피·코스닥 지수
            r = session.get(FCHART_URL, params={"symbol": code, "timeframe": kind, "count": count, "requestType": 0},
                            timeout=8)
            r.raise_for_status()
            return parse_fchart(decode(r.content))[1]
        got = _yf_batch([code], period, interval)
        if code in got and not got[code].empty:
            return got[code]
        import yfinance as yf
        return normalize_yf(yf.Ticker(code).history(period=period, interval=interval, auto_adjust=False))
    except Exception:
        return empty_frame()


def chart_with_live(bars: pd.DataFrame, quote: dict | None, tf: str, today: date | None = None) -> pd.DataFrame:
    """실시간 시세로 마지막 봉을 고치거나(오늘·이번 주·이번 달), 장중인데 봉이 없으면 새로 붙여요."""
    if bars is None or bars.empty or not quote or not quote.get("price"):
        return bars
    today = today or now_kst().date()
    price = float(quote["price"])
    hi = max(float(quote.get("high") or price), price)
    lo = min(float(quote.get("low") or price), price)
    vol = quote.get("volume")
    b = bars.copy()
    for c in ("open", "high", "low", "close", "volume"):
        b[c] = pd.to_numeric(b[c], errors="coerce").astype(float)
    last = pd.Timestamp(b["date"].iloc[-1]).date()
    same = {"일봉": last == today,
            "주봉": last.isocalendar()[:2] == today.isocalendar()[:2],
            "월봉": (last.year, last.month) == (today.year, today.month)}.get(tf, False)
    if same:
        i = b.index[-1]
        b.loc[i, "close"] = price
        b.loc[i, "high"] = max(b.loc[i, "high"], hi)
        b.loc[i, "low"] = min(b.loc[i, "low"], lo)
        if tf == "일봉" and vol and not (b.loc[i, "volume"] >= vol):
            b.loc[i, "volume"] = float(vol)
    elif quote.get("status") == "OPEN" and _is_session_now(today):
        b = pd.concat([b, pd.DataFrame([{"date": pd.Timestamp(today), "open": price, "high": hi, "low": lo,
                                         "close": price, "volume": float(vol) if vol else np.nan}])],
                      ignore_index=True)
    return b


def fetch_monthlies(codes, workers: int = 12) -> dict[str, pd.DataFrame]:
    codes = list(codes)
    kr = [c for c in codes if is_kr(c)]
    os_ = [c for c in codes if not is_kr(c)]
    out: dict[str, pd.DataFrame] = {}
    if kr:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            out.update(zip(kr, pool.map(fetch_monthly, kr)))
    if os_:
        out.update(_yf_batch(os_, "max", "1mo"))
        rest = [t for t in os_ if t not in out]
        if rest:
            with ThreadPoolExecutor(max_workers=4) as pool:
                out.update(zip(rest, pool.map(fetch_monthly, rest)))
    return {c: out[c] for c in codes}


def _monthly_arrays(monthly: pd.DataFrame | None):
    if monthly is None or monthly.empty:
        return None
    return monthly["date"].values.astype("datetime64[D]"), monthly["high"].to_numpy(dtype=float)


def _newhigh_np(dts: np.ndarray, high: np.ndarray, mon) -> dict:
    out = {k: None for k in NH_KEYS}
    if len(dts) == 0:
        return out
    today = dts[-1]
    t = today.astype(object)                               # datetime.date
    starts = {
        "d": today,
        "w": today - np.timedelta64(t.weekday(), "D"),
        "m": np.datetime64(t.replace(day=1), "D"),
    }
    mon_ok = mon is not None
    old_max = None
    if mon_ok:
        old = mon[1][mon[0] < starts["m"]]
        old_max = float(np.nanmax(old)) if len(old) else None
    for k, st_ in starts.items():
        bar = dts >= st_
        if not bar.any():
            continue
        before = ~bar
        n_before = int(before.sum())
        bar_high = float(np.nanmax(high[bar]))
        w52 = before & (dts >= st_ - np.timedelta64(364, "D"))
        if n_before >= 200 and w52.any():
            out[f"nh52_{k}"] = bool(bar_high > float(np.nanmax(high[w52])))
        # 역대: 일봉에 있는 과거 + 월봉에 있는 그 이전 전체
        cands = [float(np.nanmax(high[before]))] if n_before else []
        if old_max is not None:
            cands.append(old_max)
        if cands and (mon_ok or n_before < 200):
            out[f"ath_{k}"] = bool(bar_high > max(cands))
    hist_max = float(np.nanmax(high))
    if mon_ok:
        hist_max = max(hist_max, float(np.nanmax(mon[1])))
    out["ath_price"] = hist_max
    out["ath_ok"] = mon_ok
    return out


def newhigh_flags(h: pd.DataFrame, monthly: pd.DataFrame | None = None) -> dict:
    """지금 봉(오늘 일봉·이번 주 주봉·이번 달 월봉)의 고가가 52주 신고가인지, 역대 신고가인지.

    - 52주: 그 봉이 시작되기 전 52주(364일) 최고가를 그 봉 고가가 넘었는지
    - 역대: 그 봉이 시작되기 전 상장 이후 전체 최고가를 넘었는지(월봉으로 과거 전체를 봐요)
    """
    if h is None or h.empty:
        return {k: None for k in NH_KEYS}
    return _newhigh_np(pd.to_datetime(h["date"]).values.astype("datetime64[D]"),
                       h["high"].to_numpy(dtype=float), _monthly_arrays(monthly))


# ─────────────────────────── 시장신호(지수 신호등 · 지수 RS) ───────────────────────────
SIGNAL_TEXT = {"G": "상승 추세", "Y": "경계", "R": "하락 추세"}


def ma_signal(df: pd.DataFrame | None, n: int = 20, slope_days: int = 5) -> dict | None:
    """지수와 n일선으로 매긴 신호등.

    - 초록(G): 지수가 n일선 위 + n일선이 올라가는 중(5거래일 전보다 높음)
    - 빨강(R): 지수가 n일선 아래 + n일선이 내려가는 중
    - 노랑(Y): 둘 중 하나만 맞을 때(선 위인데 선이 꺾였거나, 선 아래인데 선은 아직 올라가는 중)
    """
    if df is None or len(df) < n + slope_days:
        return None
    close = df["close"].astype(float)
    ma = close.rolling(n).mean()
    last, ma_now, ma_prev = float(close.iloc[-1]), float(ma.iloc[-1]), float(ma.iloc[-1 - slope_days])
    above, rising = last >= ma_now, ma_now >= ma_prev
    code = "G" if (above and rising) else ("R" if (not above and not rising) else "Y")
    return {"code": code, "text": SIGNAL_TEXT[code], "ma": ma_now, "dist": (last / ma_now - 1) * 100,
            "above": above, "rising": rising}


def index_rs(index_df: pd.DataFrame | None, board: pd.DataFrame) -> dict:
    """지수의 RS: 지수 수익률을 보드 국내 종목들 수익률 분포에 놓고 1~99로. 종목 RS와 같은 잣대."""
    out = {"rs": None, "rs_1m": None, "rs_3m": None, "rs_6m": None}
    if index_df is None or len(index_df) < 30 or board is None or board.empty:
        return out
    close = index_df["close"].astype(float).reset_index(drop=True)
    last = float(close.iloc[-1])
    rets = {}
    for key, n in (("ret_1m", 21), ("ret_3m", 63), ("ret_6m", 126), ("ret_9m", 189), ("ret_12m", 250)):
        rets[key] = (last / float(close.iloc[-1 - n]) - 1) * 100 if len(close) > n else None
    mine = {"rs": rs_raw_score(rets), "rs_1m": rets["ret_1m"], "rs_3m": rets["ret_3m"], "rs_6m": rets["ret_6m"]}
    kr = board[board["code"].map(is_kr)]
    for col, raw in (("rs", "rs_raw"), ("rs_1m", "ret_1m"), ("rs_3m", "ret_3m"), ("rs_6m", "ret_6m")):
        v = mine[col]
        vals = pd.to_numeric(kr.get(raw), errors="coerce").dropna() if raw in kr else pd.Series(dtype=float)
        if v is None or len(vals) < 2:
            continue
        pct = ((vals < v).sum() + 0.5 * (vals == v).sum()) / len(vals)
        out[col] = round(pct * 98 + 1)
    return out


# ─────────────────────────── 매수 후보(효석 매매 원칙) ───────────────────────────
# 원칙(내가 정한 규칙) — 바꾸지 않는 조건
#   시장: 코스피·코스닥이 60일선 이하면 쉰다 / 주도섹터(분류 안 52주 신고가 2종목 이상)가 있을 때만 돌파매매
#   종목: 1stage = 정배열 + 52주 신고가 돌파, RS 70 이상, 단기 과열(이평선 이격 과다) 피하기, ADX 20 이상(추세),
#         3stage 돌파봉 = 큰 거래량 + 강한 종가(DCR), 너무 작은 종목 제외, 매출·영업이익·순이익 균형 성장
#   리스크: 1R 손절 8%(ATR이 8% 이상이면 ATR까지), 1회 위험 계좌의 1.5%, 최대 8종목, 3R에서 절반 익절
# 숫자로 정해 주지 않은 기준(돌파 후 며칠, 20일선 이격 %, 거래량 배수, DCR %, 최소 시총)은 기본값이고 앱에서 바꿀 수 있어요.
BUY_DEFAULTS = {
    "rs_min": 70,          # 원칙
    "adx_min": 20,         # 원칙
    "fresh_days": 5,       # 기본값: 돌파(유지 1일) 후 5거래일 이내만
    "max_ext": 15.0,       # 기본값: 20일선 대비 +15% 넘게 뜬 종목은 과열로 제외
    "vol_mult": 1.5,       # 기본값: 돌파일 거래량 ≥ 직전 50일 평균 × 1.5
    "dcr_min": 70.0,       # 기본값: 돌파일 종가가 그날 고저 범위의 위쪽 30% 안
    "min_cap": 3000.0,     # 기본값: 시총 3,000억원 이상(억원)
    "top_n": 1,            # 원칙: 섹터에서 가장 강한 주도주(1위)만. 2~3으로 넓힐 수 있어요
    "first_only": False,   # 켜면 52주 안 첫 돌파만(원칙: 첫 돌파가 가장 좋음 → 기본은 첫 돌파를 맨 위로 정렬)
    "stop_pct": 8.0,       # 원칙
    "risk_pct": 1.5,       # 원칙
    "max_pos": 8,          # 원칙
}

BUY_RULES = [   # (키, 표시 이름)
    ("market", "지수 60일선 위(코스피·코스닥)"),
    ("leader", "주도섹터 소속"),
    ("top", "섹터 주도주(RS·시총·거래대금 종합 순위)"),
    ("breakout", "52주 신고가 돌파 유지(최근)"),
    ("aligned", "정배열(강한 종목은 면제)"),
    ("rs", "RS(70↑ · 코스피·코스닥 RS보다 위)"),
    ("ext", "과열 아님(20일선 이격)"),
    ("adx", "ADX 추세"),
    ("volume", "돌파일 거래량"),
    ("dcr", "돌파일 종가 강도(DCR)"),
    ("cap", "시가총액"),
    ("earn", "실적 정배열(영업이익·EPS)"),
]


def buy_checks(r, market_ok: bool | None, market_text: str, leaders: set, p: dict, earn=None,
               idx_rs: dict | None = None) -> dict:
    """한 종목의 조건별 (통과 True/False/모름 None, 설명)."""
    def num(v):
        return None if v is None or (isinstance(v, float) and v != v) else v

    c = {}
    c["market"] = (market_ok, market_text)
    c["leader"] = (r["group"] in leaders, "주도섹터" if r["group"] in leaders else "주도섹터 아님")
    lr, ln = num(r.get("lead_rank")), num(r.get("lead_n"))
    if lr is None:
        c["top"] = (None, "순위 모름")
    else:
        parts = [f"{lab} {int(num(r.get(k)))}위" for lab, k in (("RS", "lead_rs_rank"), ("시총", "lead_cap_rank"),
                                                              ("거래대금", "lead_tv_rank")) if num(r.get(k)) is not None]
        ok = bool(r.get("lead_ok"))
        head = ("👑 대장주" if lr == 1 else f"섹터 {int(lr)}위") if ok else f"섹터 {int(lr)}위(RS 70 미만)"
        c["top"] = (ok and lr <= p.get("top_n", 1), f"{head}/{int(ln)}종목 ({', '.join(parts)})")
    days = num(r.get("bo_days"))
    if r.get("h52_fixed") is True and num(r.get("h52_diff")) is not None:
        c["breakout"] = (None, f"판정 보류 — 일봉 최고가가 네이버 52주 최고가와 {r.get('h52_diff'):+.1f}% 달라요(HTS 확인)")
    elif r.get("bo_status") == "유지" and days is not None:
        nth = num(r.get("bo_nth"))
        nth_txt = (" · 52주 안 첫 돌파" if nth == 1 else f" · 52주 안 {int(nth)}번째 돌파") if nth else ""
        ok = days <= p["fresh_days"] and (nth == 1 or not p.get("first_only"))
        c["breakout"] = (ok, f"돌파 유지 {int(days)}일째{nth_txt}")
    else:
        c["breakout"] = (False, f"이탈 {int(days)}일" if r.get("bo_status") == "이탈" and days else "돌파 아님")
    # RS: 70 이상 + 코스피·코스닥 지수 RS보다 모두 높아야(시장보다 강한 종목). 지수 RS를 모르면 통과 아님
    rs = num(r.get("rs"))
    idx_rs = idx_rs or {}
    known = {k: v for k, v in idx_rs.items() if v is not None}
    if rs is None:
        c["rs"] = (None, "RS 없음")
    elif len(known) < len(idx_rs) or not idx_rs:
        c["rs"] = (None, f"RS {rs:.0f} · 지수 RS 모름")
    else:
        beat = all(rs > v for v in known.values())
        idx_txt = " · ".join(f"{k} {v:.0f}" for k, v in known.items())
        c["rs"] = (rs >= p["rs_min"] and beat, f"RS {rs:.0f} (지수 {idx_txt})")
    # 정배열: 강한 종목(RS 조건 통과) + 52주 신고가 돌파면 정배열이 아니어도 괜찮아요
    if r.get("aligned") is True:
        c["aligned"] = (True, "정배열")
    elif c["rs"][0] is True and r.get("bo_status") == "유지":
        c["aligned"] = (True, "정배열 아님 — 강한 종목 + 신고가 돌파라 허용")
    else:
        c["aligned"] = (False, "정배열 아님(RS 조건·돌파 중 하나가 안 돼서 면제 안 됨)")
    ext = num(r.get("dist_ma20"))
    c["ext"] = (None if ext is None else ext <= p["max_ext"], f"20일선 {ext:+.1f}%" if ext is not None else "-")
    adx = num(r.get("adx"))
    c["adx"] = (None if adx is None else adx >= p["adx_min"], f"ADX {adx:.0f}" if adx is not None else "-")
    vr = num(r.get("bo_vol_ratio"))
    c["volume"] = (None if vr is None else vr >= p["vol_mult"], f"거래량 {vr:.1f}배" if vr is not None else "돌파일 없음")
    dcr = num(r.get("bo_dcr"))
    c["dcr"] = (None if dcr is None else dcr >= p["dcr_min"], f"DCR {dcr:.0f}%" if dcr is not None else "돌파일 없음")
    cap = num(r.get("cap_krw"))
    c["cap"] = (None if cap is None else cap / 1e8 >= p["min_cap"], format_krw(cap) + "원" if cap is not None else "시총 모름")
    if earn is None:
        c["earn"] = (None, "실적 미확인")
    else:
        ok = earn.get("earn_ok")
        c["earn"] = (ok, {True: "정배열", False: "정배열 아님", None: "판정 불가(전망 없음)"}[ok])
    return c


def position_plan(price: float, atr: float | None, equity: float | None, p: dict) -> dict:
    """1R 손절(8%, ATR이 8% 이상이면 ATR), 계좌 1.5% 위험 기준 수량, 3R 목표가."""
    stop_pct = p["stop_pct"]
    if atr is not None and atr == atr and atr >= p["stop_pct"]:
        stop_pct = float(atr)
    out = {"stop_pct": stop_pct, "stop_price": price * (1 - stop_pct / 100),
           "target_price": price * (1 + 3 * stop_pct / 100), "shares": None, "amount": None, "weight": None}
    if equity and price:
        risk_won = equity * p["risk_pct"] / 100
        shares = int(risk_won // (price * stop_pct / 100))
        shares = min(shares, int(equity // price))           # 계좌보다 크게는 못 사요
        out.update(shares=shares, amount=shares * price, weight=shares * price / equity * 100)
    return out


def buy_screen(df: pd.DataFrame, market_ok: bool | None, market_text: str, leaders: set, p: dict,
               fins: dict | None = None, intraday: bool = False, idx_rs: dict | None = None) -> pd.DataFrame:
    """국내 종목만. tier: 매수 가능 / 종가 확인 / 1개 미충족. 나머지는 빼요."""
    rows = []
    for _, r in df[df["code"].map(is_kr) & df["price"].notna()].iterrows():
        earn = earnings_trend(fins.get(r["code"]), p.get("earn_years", 3)) if fins is not None else None
        c = buy_checks(r, market_ok, market_text, leaders, p, earn, idx_rs)
        # fins=None(1차 거르기)일 때는 실적을 아직 안 봤으니 빼고 세요. 실적은 남은 후보만 받아서 2차로 확인해요.
        fails = [k for k, _ in BUY_RULES if (k != "earn" or fins is not None) and c[k][0] is not True]
        stock_fails = [k for k in fails if k != "market"]
        if len(stock_fails) > 1:
            continue
        if not stock_fails:
            if market_ok is not True:
                tier = "시장 대기"
            elif intraday and r.get("bo_days") == 1:
                tier = "종가 확인"
            else:
                tier = "매수 가능"
        else:
            tier = "1개 미충족"
        rows.append({"code": r["code"], "name": r["name"], "group": r["group"], "tier": tier,
                     "fails": stock_fails, "checks": c, "row": r})
    order = {"매수 가능": 0, "종가 확인": 1, "시장 대기": 2, "1개 미충족": 3}
    # 같은 등급 안에서는 52주 안 첫 돌파 → 돌파가 최근일수록 → RS 높은 순
    def nth(x):
        v = x["row"].get("bo_nth")
        return 99 if v is None or v != v else int(v)
    def lr(x):
        v = x["row"].get("lead_rank")
        return 99 if v is None or v != v else int(v)
    # 같은 등급 안에서는 대장주(섹터 순위) → 52주 안 첫 돌파 → 최근 돌파 → RS 높은 순
    rows.sort(key=lambda x: (order[x["tier"]], lr(x), nth(x), x["row"].get("bo_days") or 99,
                             -(x["row"].get("rs") or 0)))
    return pd.DataFrame(rows, columns=["code", "name", "group", "tier", "fails", "checks", "row"])



# ─────────────────────────── 섹터 안 급상승 엔진 · 차기 주도섹터 ───────────────────────────
NON_SECTOR_GROUPS = {"스팩", "기타(동전주·정리매매 등)"}   # 업종이 아니라서 섹터 비교에서 빼요
_flow_memo: dict = {}


def _flow_table(trends: dict | None) -> pd.DataFrame:
    """종목별 외국인·기관 순매수(억원) 5일·20일 합과 연속일수. 같은 수급 묶음이면 한 번만 계산."""
    key = id(trends)
    hit = _flow_memo.get("v")
    if hit is not None and hit[0] == key and hit[1] is trends:
        return hit[2]
    out = _flow_table_now(trends)
    _flow_memo["v"] = (key, trends, out)
    return out


def _flow_table_now(trends: dict | None) -> pd.DataFrame:
    rows = []
    for code, t in (trends or {}).items():
        if t is None or t.empty:
            continue
        sm = trend_summary(t)
        r = {"code": code, "streak_f": sm["streak_외국인"], "streak_i": sm["streak_기관"]}
        for k, lab in (("외국인", "f"), ("기관", "i")):
            col = t[f"{k}금액"]
            r[f"{lab}5"] = float(col.tail(5).dropna().sum()) if col.tail(5).notna().any() else np.nan
            r[f"{lab}20"] = float(col.tail(20).dropna().sum()) if col.tail(20).notna().any() else np.nan
        rows.append(r)
    return pd.DataFrame(rows, columns=["code", "streak_f", "streak_i", "f5", "f20", "i5", "i20"])


MOM_WEIGHTS = {"flow": 0.30, "rs": 0.25, "candle": 0.20, "break": 0.25}


def op_growth(fin: pd.DataFrame | None, year: int | None = None) -> dict:
    """올해(E 포함) 영업이익이 작년 실적보다 늘었는지. 원칙: 영업이익이 늘지 않는 종목은 강해도 배제.

    - 증가: 올해 > 작년이고 올해가 흑자(적자에서 흑자로 바뀐 것도 증가로 봐요)
    - 올해 값이 없거나 작년 값이 없으면 판정 불가(None) → 배제 쪽으로 처리
    """
    year = year or now_kst().year
    yo = _year_op(fin)
    a, b = yo.get(year - 1), yo.get(year)
    if a is None or b is None:
        return {"op_up": None, "op_g": None, "op_txt": "영업이익 확인 불가"}
    up = bool(b > a and b > 0)
    if a > 0:
        g = (b / a - 1) * 100
        txt = f"영업익 {g:+.0f}%"
    else:
        g = None
        txt = "흑자전환" if b > 0 else "적자"
    return {"op_up": up, "op_g": g, "op_txt": txt}


def momentum_engine(df: pd.DataFrame, trends: dict | None, fresh_days: int = 5,
                    fins: dict | None = None, require_op: bool = False) -> pd.DataFrame:
    """섹터 안에서 가장 빠르게 치고 올라오는 종목 점수(0~100).

    국내 종목 전체를 기준으로 네 가지를 각각 백분위로 바꿔 가중 평균해요.
    - 수급(30%): 외국인+기관 5일 순매수 ÷ 시가총액
    - RS(25%): 1개월 RS 수준 + RS 가속(1개월 RS − 종합 RS, 최근이 더 강할수록 +)
    - 캔들(20%): 오늘 거래량 배수(50일 평균 대비) + 오늘 종가 위치(DCR)
    - 저항 돌파(25%): 52주 신고가 돌파 fresh_days일 이내면 만점, 아니면 52주 고가에 가까울수록 높게
    수급 데이터가 없는 종목은 나머지 셋으로만 계산해요(mom_noflow=True).
    섹터 순위(mom_rank)는 같은 분류 국내 종목끼리 점수 순서예요.
    """
    d = df[df["code"].map(is_kr) & df["price"].notna() & ~df["group"].isin(NON_SECTOR_GROUPS)].copy()
    if d.empty:
        return d
    ft = _flow_table(trends)
    d = d.merge(ft, on="code", how="left")
    cap = pd.to_numeric(d["cap_krw"], errors="coerce")
    d["fi5"] = d[["f5", "i5"]].sum(axis=1, min_count=1)
    d["fi20"] = d[["f20", "i20"]].sum(axis=1, min_count=1)
    d["fi5_pct"] = d["fi5"] * 1e8 / cap * 100

    def pct(col):
        v = pd.to_numeric(col, errors="coerce")
        return v.rank(pct=True, method="average") * 100

    d["rs_accel"] = pd.to_numeric(d["rs_1m"], errors="coerce") - pd.to_numeric(d["rs"], errors="coerce")
    d["s_flow"] = pct(d["fi5_pct"])
    d["s_rs"] = (pct(d["rs_1m"]) + pct(d["rs_accel"])) / 2
    d["s_candle"] = (pct(d["vol_today"]) + pct(d["dcr_today"])) / 2
    fresh = (d["bo_status"] == "유지") & (pd.to_numeric(d["bo_days"], errors="coerce") <= fresh_days)
    d["s_break"] = (pct(d["gap"]) * 0.9).where(~fresh, 100.0)
    parts = {"flow": "s_flow", "rs": "s_rs", "candle": "s_candle", "break": "s_break"}
    num = sum(d[c].fillna(0) * MOM_WEIGHTS[k] for k, c in parts.items())
    den = sum(d[c].notna() * MOM_WEIGHTS[k] for k, c in parts.items())
    d["mom"] = (num / den.where(den > 0)).round(1)
    d["mom_noflow"] = d["s_flow"].isna()
    yr = now_kst().year
    og = [op_growth((fins or {}).get(c), yr) for c in d["code"]]
    d["op_up"] = [x["op_up"] for x in og]
    d["op_g"] = [x["op_g"] for x in og]
    d["op_txt"] = [x["op_txt"] for x in og]
    if require_op:   # 점수는 전체 기준 그대로, 순위는 영업이익이 늘어나는 종목끼리
        d = d[d["op_up"] == True].copy()  # noqa: E712
        if d.empty:
            return d
    d["mom_rank"] = d.groupby("group")["mom"].rank(ascending=False, method="first")
    d["mom_n"] = d.groupby("group")["code"].transform("count")
    return d


def _year_op(fin: pd.DataFrame | None) -> dict:
    if fin is None or fin.empty or "op" not in fin:
        return {}
    out = {}
    for k, v in zip(fin["key"].astype(str), fin["op"]):
        if len(k) >= 4 and k[:4].isdigit() and v is not None and v == v:
            out[int(k[:4])] = float(v)
    return out


_yo_memo: dict = {}


def _year_ops(fins: dict | None) -> dict:
    """{종목: {연도: 영업이익}}. 같은 실적 묶음이면 한 번만 계산."""
    hit = _yo_memo.get("v")
    if hit is not None and hit[0] is fins:
        return hit[1]
    out = {c: _year_op(f) for c, f in (fins or {}).items()}
    _yo_memo["v"] = (fins, out)
    return out


NEXT_DEFAULTS = {"near_pct": 10.0, "near_rs": 70, "min_near": 3, "min_cover": 0.5}


def next_leader_sectors(df: pd.DataFrame, fins: dict | None, trends: dict | None, leaders: set,
                        p: dict | None = None, year: int | None = None, include_all: bool = False) -> pd.DataFrame:
    """차기 주도섹터 후보. 세 조건(효석 원칙):
    1) 업종 이익증가율 상승: 분류 국내 종목 영업이익 합으로 올해(E) 증가율 > 작년 증가율, 그리고 올해 증가율 > 0
       (세 해 값이 모두 있는 종목만 더해요. 분류 종목의 절반 이상·3종목 이상이어야 판정)
    2) 수급: 외국인+기관 순매수 합이 20일·5일 모두 플러스
    3) 52주 신고가를 갈 수 있는 종목 다수: 52주 고가 대비 near_pct% 이내 + RS near_rs 이상 종목이 min_near개 이상
    """
    p = {**NEXT_DEFAULTS, **(p or {})}
    year = year or now_kst().year
    d = df[df["code"].map(is_kr) & df["price"].notna() & ~df["group"].isin(NON_SECTOR_GROUPS)]
    d = d.merge(_flow_table(trends), on="code", how="left")
    yo_all = _year_ops(fins)
    rows = []
    for g, sub in d.groupby("group"):
        n = len(sub)
        if n < 3:
            continue
        # 1) 이익증가율
        ys = [year - 2, year - 1, year, year + 1]
        sums = {y: 0.0 for y in ys}
        cover = 0
        cover_next = 0
        nxt = 0.0
        for c in sub["code"]:
            yo = yo_all.get(c, {})
            if all(y in yo for y in ys[:3]):
                cover += 1
                for y in ys[:3]:
                    sums[y] += yo[y]
                if ys[3] in yo:
                    cover_next += 1
        def growth(a, b):
            return (b / a - 1) * 100 if a and a > 0 else None
        g_prev = growth(sums[ys[0]], sums[ys[1]]) if cover else None
        g_now = growth(sums[ys[1]], sums[ys[2]]) if cover else None
        # 내년(E) 증가율: 내년 값까지 있는 종목만 따로
        s2 = s3 = 0.0
        for c in sub["code"]:
            yo = yo_all.get(c, {})
            if ys[2] in yo and ys[3] in yo and all(y in yo for y in ys[:3]):
                s2 += yo[ys[2]]
                s3 += yo[ys[3]]
        g_next = growth(s2, s3) if cover_next else None
        enough = cover >= 3 and cover / n >= p["min_cover"]
        if not enough or g_prev is None or g_now is None:
            earn_ok = None
        else:
            earn_ok = bool(g_now > g_prev and g_now > 0)
        # 2) 수급
        f5, i5 = sub["f5"].sum(min_count=1), sub["i5"].sum(min_count=1)
        f20, i20 = sub["f20"].sum(min_count=1), sub["i20"].sum(min_count=1)
        have_flow = sub["f20"].notna().sum()
        if have_flow < max(2, n // 2):
            flow_ok = None
        else:
            fi5 = (0 if pd.isna(f5) else f5) + (0 if pd.isna(i5) else i5)
            fi20 = (0 if pd.isna(f20) else f20) + (0 if pd.isna(i20) else i20)
            flow_ok = bool(fi20 > 0 and fi5 > 0)
        # 3) 52주 신고가 갈 수 있는 종목
        gap = pd.to_numeric(sub["gap"], errors="coerce")
        rs = pd.to_numeric(sub["rs"], errors="coerce")
        near = sub[(gap >= -p["near_pct"]) & (rs >= p["near_rs"])]
        near_ok = len(near) >= p["min_near"]
        oks = [earn_ok, flow_ok, near_ok]
        n_ok = sum(1 for o in oks if o is True)
        if n_ok == 3:
            tier = "주도 유지" if g in leaders else "차기 주도 후보"
        elif n_ok == 2:
            tier = "관찰"
        elif include_all:
            tier = "해당 없음"
        else:
            continue
        cap = pd.to_numeric(sub["cap_krw"], errors="coerce").sum(min_count=1)
        fi20 = (0 if pd.isna(f20) else f20) + (0 if pd.isna(i20) else i20)
        rows.append({
            "group": g, "tier": tier, "leading": g in leaders, "n": n, "n_ok": n_ok,
            "accel": (g_now - g_prev) if (earn_ok is not None) else None,
            "flow_cap": (fi20 * 1e8 / cap * 100) if (flow_ok is not None and cap and cap > 0) else None,
            "near_ratio": len(near) / n,
            "earn_ok": earn_ok, "g_prev": g_prev, "g_now": g_now, "g_next": g_next, "cover": cover,
            "flow_ok": flow_ok, "f5": f5, "i5": i5, "f20": f20, "i20": i20,
            "both20": bool((f20 or 0) > 0 and (i20 or 0) > 0) if flow_ok is not None else None,
            "near_ok": near_ok, "n_near": len(near),
            "near_names": ", ".join(near.sort_values("gap", ascending=False)["name"].head(6)),
            "years": ys,
        })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    # 가능성 점수(0~100): 섹터끼리 백분위 — 이익 가속 40% · 수급(시총 대비 외+기 20일) 30% · 신고가 근처 강한 종목 비율 30%
    def pr(col):
        return pd.to_numeric(out[col], errors="coerce").rank(pct=True) * 100
    w = {"accel": 0.4, "flow_cap": 0.3, "near_ratio": 0.3}
    num = sum(pr(c).fillna(0) * v for c, v in w.items())
    den = sum(pr(c).notna() * v for c, v in w.items())
    out["score"] = (num / den.where(den > 0)).round(0)
    order = {"차기 주도 후보": 0, "주도 유지": 1, "관찰": 2, "해당 없음": 3}
    out["_o"] = out["tier"].map(order)
    out = out.sort_values(["_o", "score", "n_near"], ascending=[True, False, False]).drop(columns="_o")
    return out.reset_index(drop=True)



# ─────────────────────────── 로테이션 시나리오(두 바스켓) ───────────────────────────
SCENARIO_PRESETS = {
    "반도체 후공정 vs 전공정": {
        "a_name": "후공정", "b_name": "전공정",
        "a_groups": ["OSAT", "후공정 테스트 부품", "후공정 테스트 장비", "후공정 패키징·검사 장비", "패키징 소재"],
        "b_groups": ["전공정 장비", "전공정 부품", "전공정 소재", "전공정 계측"],
    },
}
SCN_WINDOWS = {"오늘": 1.0, "이번 주": 2.0, "5거래일": 2.0, "20거래일": 4.0}   # 창별 기본 기준(%)


def _closes_frame(df: pd.DataFrame, histories: dict, codes) -> pd.DataFrame:
    """종목별 종가를 날짜로 맞춘 표. 오늘 봉은 현재가로 바꿔요."""
    today = pd.Timestamp(now_kst().date())
    cols = {}
    idx = df.set_index("code")
    for c in codes:
        h = histories.get(c, (None, None, None))[1]
        if h is None or h.empty:
            continue
        sr = pd.Series(h["close"].to_numpy(dtype=float), index=pd.to_datetime(h["date"]).dt.normalize())
        sr = sr[~sr.index.duplicated(keep="last")]
        p = idx["price"].get(c)
        live = idx["source"].get(c) == "실시간"
        if p is not None and p == p and live:
            if sr.index[-1] == today:
                sr.iloc[-1] = float(p)            # 일봉에 오늘이 있으면 현재가로
            elif today.weekday() < 5:
                sr.loc[today] = float(p)          # 평일 장중인데 일봉에 오늘이 아직 없으면 붙여요(주말엔 안 붙임)
        cols[c] = sr
    if not cols:
        return pd.DataFrame()
    return pd.DataFrame(cols).sort_index().ffill()


def basket_stats(df: pd.DataFrame, histories: dict, groups, window: str, trends: dict | None = None) -> dict:
    """바스켓(분류 묶음) 수익률: 시총가중(판정 기준)·동일가중·상승 종목 비율, 외국인·기관 수급, 최근 5거래일 일별."""
    sub = df[df["group"].isin(list(groups)) & df["code"].map(is_kr) & df["price"].notna()]
    out = {"n": len(sub), "ret_cap": None, "ret_eq": None, "breadth": None, "f5": None, "i5": None,
           "f20": None, "i20": None, "daily": pd.Series(dtype=float), "top": [], "codes": list(sub["code"])}
    if sub.empty:
        return out
    cl = _closes_frame(df, histories, sub["code"])
    if cl.empty or len(cl) < 3:
        return out
    last = cl.index[-1]
    if window == "오늘":
        base_i = len(cl) - 2
    elif window == "이번 주":
        monday = last - pd.Timedelta(days=last.weekday())
        before = cl.index[cl.index < monday]
        base_i = cl.index.get_loc(before[-1]) if len(before) else 0
    else:
        n = int(window.replace("거래일", ""))
        base_i = max(0, len(cl) - 1 - n)
    rets = (cl.iloc[-1] / cl.iloc[base_i] - 1) * 100
    caps = pd.to_numeric(sub.set_index("code")["cap_krw"], errors="coerce").reindex(rets.index)
    ok = rets.notna()
    w = caps.where(caps > 0)
    if w[ok].notna().sum() >= max(1, ok.sum() // 2):
        w = w.fillna(w[ok].median())
        out["ret_cap"] = float((rets[ok] * w[ok]).sum() / w[ok].sum())
    else:
        out["ret_cap"] = float(rets[ok].mean())                     # 시총을 모르면 동일가중
    out["ret_eq"] = float(rets[ok].mean())
    out["breadth"] = float((rets[ok] > 0).mean() * 100)
    daily = cl.pct_change().iloc[-5:] * 100
    wd = w.reindex(daily.columns).fillna(1.0) if w is not None else pd.Series(1.0, index=daily.columns)
    out["daily"] = (daily.mul(wd, axis=1).sum(axis=1) / daily.notna().mul(wd, axis=1).sum(axis=1)).round(2)
    names = sub.set_index("code")["name"]
    top = rets[ok].sort_values(ascending=False)
    out["top"] = [(names[c], float(top[c])) for c in top.index[:3]]
    out["bottom"] = [(names[c], float(top[c])) for c in top.index[-3:][::-1]]
    ft = _flow_table(trends)
    ft = ft[ft["code"].isin(sub["code"])]
    if not ft.empty:
        for k in ("f5", "i5", "f20", "i20"):
            out[k] = float(ft[k].sum(min_count=1)) if ft[k].notna().any() else None
    return out


def _state(r: float | None, t: float, crash: float) -> str | None:
    if r is None or r != r:
        return None
    if r >= t:
        return "강세"
    if r > -t:
        return "보합"
    if r > -crash * t:
        return "약세"
    return "급락"


# (A 상태, B 상태) → (시나리오 번호, 한 줄 판정). A = 지금 들고 있는/주도 쪽, B = 다음 후보 쪽
_SCN_MATRIX = {
    ("강세", "강세"): (3, "{A}도 가고 {B}도 간다"), ("보합", "강세"): (3, "{A} 쪽이 버티고 {B} 쪽이 간다 — 3번 쪽"),
    ("약세", "강세"): (2.5, "{A} 쪽이 덜 빠지고 {B} 쪽이 간다 — 2번 신호지만 3번 가능성"),
    ("급락", "강세"): (2, "{A} 쪽이 과하게 빠지고 {B} 쪽이 간다 — 완전한 로테이션"),
    ("강세", "보합"): (1, "{A}만 간다"), ("강세", "약세"): (1, "{A}만 간다"), ("강세", "급락"): (1, "{A}만 간다"),
    ("보합", "보합"): (0, "둘 다 방향 없음"), ("보합", "약세"): (0, "{B} 쪽이 밀리는 중 — {A} 쪽이 버티는지 확인"),
    ("보합", "급락"): (0, "{B} 쪽 급락 — {A} 쪽이 버티는지 확인"),
    ("약세", "보합"): (4, "{A} 쪽이 빠지는데 {B} 쪽도 못 간다 — 4번 초기"),
    ("급락", "보합"): (4, "{A} 쪽이 급락하고 {B} 쪽도 못 간다"),
    ("약세", "약세"): (4, "{A}·{B} 동반 약세"), ("약세", "급락"): (4, "{A}·{B} 동반 약세"),
    ("급락", "약세"): (4, "{A}·{B} 동반 약세"), ("급락", "급락"): (4, "{A}·{B} 동반 급락"),
}

SCN_INFO = {   # 번호 → (이름, 시장 해석, 행동). 효석 메모(9/27) 기준. {A}·{B}는 바스켓 이름으로 바뀌어요
    1: ("1번 · {A}만 계속", "{B} 쪽에 시간이 필요한 시장", "{A} 보유 유지(유리). {B} 쪽은 매수 후보 원칙이 뜰 때까지 관찰"),
    2: ("2번 · {A} 빠지고 {B} 감", "수급이 부족한 시장(로테이션)",
        "{A} 일부를 팔아서라도 {B} 담기. {A} 쪽이 과하게 빠지면 완벽한 섹터 로테이션장으로 보고 비중 이동"),
    2.5: ("2번 신호 · 3번 가능성", "{A} 쪽이 덜 빠지면 수급 개선(3번)일 수 있음",
          "{A} 쪽을 급하게 다 팔지 말고, {B} 쪽은 원칙 통과 종목부터 일부 선진입 검토. {A} 쪽이 과하게 빠지면 2번 확정"),
    3: ("3번 · {A}도 {B}도 감", "수급이 개선되는 시장", "{A} 유지(유리) + {B} 쪽도 원칙 통과 종목 추가"),
    4: ("4번 · 전체 Risk-off", "{A}도 빠지고 {B}도 못 가는 장",
        "{A} 팔아 {B} 사는 게 아니라 현금 확대"),
    0: ("관망 · 방향 미정", "아직 어느 시나리오도 아님", "포지션 유지, 다음 신호 대기(아래 경우의 수 기준선 확인)"),
}


def classify_scenario(ra: float | None, rb: float | None, t: float, crash: float = 2.5) -> dict:
    sa, sb = _state(ra, t, crash), _state(rb, t, crash)
    if sa is None or sb is None:
        return {"id": None, "a_state": sa, "b_state": sb, "text": "데이터 부족"}
    sid, text = _SCN_MATRIX[(sa, sb)]
    return {"id": sid, "a_state": sa, "b_state": sb, "text": text}


def scenario_paths(ra: float | None, rb: float | None, t: float, crash: float = 2.5) -> list[dict]:
    """경우의 수: 각 시나리오가 되려면 A·B가 어디까지 가야 하는지(지금 값과의 거리)."""
    if ra is None or rb is None:
        return []
    def need(cur, lo=None, hi=None):
        """cur를 [lo, hi) 범위로 넣으려면 몇 %p 움직여야 하는지. 이미 안이면 0."""
        if lo is not None and cur < lo:
            return lo - cur
        if hi is not None and cur >= hi:
            return hi - cur
        return 0.0
    rows = [
        (3, "{A} ≥ +{t} · {B} ≥ +{t}", need(ra, lo=t), need(rb, lo=t)),
        (1, "{A} ≥ +{t} · {B} < +{t}", need(ra, lo=t), need(rb, hi=t)),
        (2.5, "−{c} < {A} ≤ −{t} · {B} ≥ +{t}", need(ra, lo=-crash * t + 1e-9, hi=-t), need(rb, lo=t)),
        (2, "{A} ≤ −{c} · {B} ≥ +{t}", need(ra, hi=-crash * t), need(rb, lo=t)),
        (4, "{A} ≤ −{t} · {B} < +{t}", need(ra, hi=-t), need(rb, hi=t)),
    ]
    out = []
    for sid, cond, da, db in rows:
        out.append({"id": sid, "cond": cond.replace("{t}", f"{t:g}%").replace("{c}", f"{crash * t:g}%"),
                    "move_a": da, "move_b": db, "dist": abs(da) + abs(db)})
    return sorted(out, key=lambda r: r["dist"])



# ─────────────────────────── 돈의 방향(일당백 관점: 예측보다 확인) ───────────────────────────
# 진짜 로테이션 = 기존 리더의 약세 + 새 리더의 강세가 같이 나와야 해요.
#   새 리더 쪽: 거래대금이 늘고, 돌파가 나오고, 한두 종목이 아니라 여러 종목의 상대강도가 같이 올라와야
#   기존 리더 쪽: 조정 때 20일선이나 기존 돌파가격을 지키는지
#   아무도 돈을 못 받아가면 로테이션이 아니라 섹터에서 돈이 빠지는 것 → 현금도 선택지
MONEY_DEFAULTS = {"tv_up": 1.2, "tv_down": 0.85, "min_break": 2, "rs_up": 0.5, "fresh_days": 5}


def money_stats(sub: pd.DataFrame, fresh_days: int = 5) -> dict:
    """종목 묶음의 돈 흐름: 거래대금 5일/이전 20일, 신규 돌파 수, 돌파 유지 수, RS 동반 상승 비율, 20일선 위 비율."""
    sub = sub[sub["price"].notna()]
    n = len(sub)
    out = {"n": n, "tv5": None, "tvp20": None, "tv_ratio": None, "n_break": 0, "n_hold": 0,
           "rs_up": None, "above20": None, "rs1m_med": None}
    if not n:
        return out
    tv5 = pd.to_numeric(sub["tv5"], errors="coerce")
    tvp = pd.to_numeric(sub["tvp20"], errors="coerce")
    ok = tv5.notna() & tvp.notna()
    if ok.any():
        out["tv5"], out["tvp20"] = float(tv5[ok].sum()), float(tvp[ok].sum())
        out["tv_ratio"] = out["tv5"] / out["tvp20"] if out["tvp20"] > 0 else None
    hold = sub["bo_status"] == "유지"
    days = pd.to_numeric(sub["bo_days"], errors="coerce")
    out["n_hold"] = int(hold.sum())
    out["n_break"] = int((hold & (days <= fresh_days)).sum())
    r1, r = pd.to_numeric(sub["rs_1m"], errors="coerce"), pd.to_numeric(sub["rs"], errors="coerce")
    both = r1.notna() & r.notna()
    if both.any():
        out["rs_up"] = float((r1[both] > r[both]).mean())
        out["rs1m_med"] = float(r1[both].median())
    ma20 = pd.to_numeric(sub["ma20"], errors="coerce")
    has = ma20.notna()
    if has.any():
        out["above20"] = float((sub.loc[has, "price"] >= ma20[has]).mean())
    return out


def leaders_defense(sub: pd.DataFrame, top: int = 3) -> list[dict]:
    """기존 리더들이 20일선이나 기존 돌파가격을 지키는지. 리더 = 분류별 섹터 순위 상위(RS 70↑)."""
    d = sub[(sub["lead_ok"] == True) & (pd.to_numeric(sub["lead_rank"], errors="coerce") <= 1)]  # noqa: E712
    if d.empty:
        d = sub[sub["lead_ok"] == True]  # noqa: E712
    d = d.sort_values("lead_score", ascending=False).head(top)
    rows = []
    for _, r in d.iterrows():
        p, ma20 = r["price"], r.get("ma20")
        on20 = ma20 is not None and not pd.isna(ma20) and p >= ma20
        on_bo = r.get("bo_status") == "유지"          # 유지 = 돌파가격 위에서 계속 마감
        lvl = r.get("bo_level")
        rows.append({"name": r["name"], "group": r["group"], "ok": bool(on20 or on_bo),
                     "on20": bool(on20), "on_bo": bool(on_bo),
                     "vs20": (p / ma20 - 1) * 100 if (ma20 is not None and not pd.isna(ma20) and ma20) else None,
                     "bo_level": lvl if on_bo else None})
    return rows


def rotation_confirm(df: pd.DataFrame, a_groups, b_groups, p: dict | None = None) -> dict:
    """가격이 아니라 돈으로 확인. B(새 리더 후보) 네 가지, A(기존 리더) 두 가지를 봐요."""
    p = {**MONEY_DEFAULTS, **(p or {})}
    kr = df[df["code"].map(is_kr) & df["price"].notna() & ~df["group"].isin(NON_SECTOR_GROUPS)]
    A = kr[kr["group"].isin(list(a_groups))]
    B = kr[kr["group"].isin(list(b_groups))]
    ma, mb, mall = money_stats(A, p["fresh_days"]), money_stats(B, p["fresh_days"]), money_stats(kr, p["fresh_days"])

    def share(m):
        return (m["tv5"] / mall["tv5"], m["tvp20"] / mall["tvp20"]) if (m["tv5"] and mall["tv5"] and m["tvp20"]
                                                                         and mall["tvp20"]) else (None, None)
    bs_now, bs_prev = share(mb)
    b_checks = [
        ("거래대금이 실제로 들어오는지", None if mb["tv_ratio"] is None else mb["tv_ratio"] >= p["tv_up"],
         "-" if mb["tv_ratio"] is None else f"최근 5일 거래대금 = 이전 20일 평균의 {mb['tv_ratio']:.2f}배"),
        ("보드 안 거래대금 비중이 느는지", None if bs_now is None else bs_now > bs_prev,
         "-" if bs_now is None else f"비중 {bs_prev * 100:.1f}% → {bs_now * 100:.1f}%"),
        ("돌파 성공 종목이 여러 개인지", mb["n_break"] >= p["min_break"],
         f"최근 {p['fresh_days']}거래일 신규 돌파 {mb['n_break']}개 · 돌파 유지 {mb['n_hold']}개"),
        ("여러 종목의 상대강도가 같이 오르는지", None if mb["rs_up"] is None else mb["rs_up"] >= p["rs_up"],
         "-" if mb["rs_up"] is None else f"1개월 RS가 종합 RS보다 높은 종목 {mb['rs_up'] * 100:.0f}%"),
    ]
    defense = leaders_defense(A)
    a_checks = [
        ("상대강도가 유지되는지", None if ma["rs1m_med"] is None else ma["rs1m_med"] >= 60,
         "-" if ma["rs1m_med"] is None else f"1개월 RS 중앙값 {ma['rs1m_med']:.0f}"),
        ("리더들이 20일선·기존 돌파가격을 지키는지",
         None if not defense else sum(x["ok"] for x in defense) * 2 > len(defense),   # 과반이 지키면 통과
         " · ".join(f"{x['name']} {'✅' if x['ok'] else '❌'}" for x in defense) or "리더 없음"),
    ]
    b_ok = sum(1 for _, v, _ in b_checks if v is True)
    b_known = sum(1 for _, v, _ in b_checks if v is not None)
    # 기존 리더 판정의 중심은 '리더들이 20일선·돌파가격을 지키는지'. 상대강도는 함께 보여주되 약해지면 경고만
    a_ok = a_checks[1][1] is True
    a_rs_weak = a_checks[0][1] is False
    b_conf = b_ok >= 3
    if b_known < 3:
        verdict = ("확인 불가", "데이터가 부족해서 돈의 방향을 확인할 수 없어요", "기다리기")
    elif b_conf and a_ok:
        verdict = ("섹터 확장", "새 돈이 섹터 전체로 들어오는 그림(3번 확인)",
                   "기존 승자는 추세가 살아 있는 동안 보유 · 새로 강해지는 쪽은 작은 비중부터 · 돌파 성공이 늘면 조금씩 비중 확대")
    elif b_conf:
        verdict = ("로테이션 확인", "기존 리더 약세 + 새 리더 강세가 같이 나옴(2번 확인)",
                   "기존 리더 일부를 줄이고 새 리더 쪽 원칙 통과 종목으로 이동 — 시장이 증명하는 만큼만")
    elif a_ok:
        verdict = ("기존 리더 유지", "새 쪽으로 돈이 넘어간 증거는 아직 없음 — 강한 종목도 쉬어감(1번)"
                   + (" · 단, 바스켓 전체 상대강도는 약해지는 중" if a_rs_weak else ""),
                   "미리 팔고 갈아타지 않기 · 기존 리더 보유, 새 쪽은 확인될 때까지 관찰"
                   + (" · 리더가 20일선을 깨면 비중 줄이기" if a_rs_weak else ""))
    else:
        verdict = ("돈이 빠지는 중", "기존 리더도 약하고 받아가는 쪽도 없음 — 로테이션이 아니라 이탈(4번)",
                   "종목 이름만 바꾸지 말고 현금 확대 · 새 종목을 찾아 헤매지 않기")
    return {"b_checks": b_checks, "a_checks": a_checks, "b_ok": b_ok, "b_known": b_known, "a_ok": a_ok,
            "a_rs_weak": a_rs_weak, "b_share": (bs_prev, bs_now),
            "verdict": verdict, "A": ma, "B": mb, "ALL": mall, "defense": defense}


def sector_money_radar(df: pd.DataFrame, leaders: set, p: dict | None = None) -> tuple[pd.DataFrame, dict]:
    """섹터 간 돈의 이동(와리가리) 레이더. 섹터마다 거래대금 비중 변화·돌파 수·RS 동반 상승으로 상태를 붙여요."""
    p = {**MONEY_DEFAULTS, **(p or {})}
    kr = df[df["code"].map(is_kr) & df["price"].notna() & ~df["group"].isin(NON_SECTOR_GROUPS)]
    mall = money_stats(kr, p["fresh_days"])
    rows = []
    for g, sub in kr.groupby("group"):
        if len(sub) < 2:
            continue
        m = money_stats(sub, p["fresh_days"])
        if not (m["tv5"] and mall["tv5"] and m["tvp20"] and mall["tvp20"]):
            continue
        s_now, s_prev = m["tv5"] / mall["tv5"] * 100, m["tvp20"] / mall["tvp20"] * 100
        share_ratio = s_now / s_prev if s_prev > 0 else None
        lead = g in leaders
        rs_up = m["rs_up"] or 0
        strong_in = (share_ratio or 0) >= p["tv_up"] and (m["n_break"] >= p["min_break"] or rs_up >= p["rs_up"])
        weak_out = (share_ratio or 9) <= p["tv_down"] and rs_up < 0.4
        if strong_in:
            state = "🔥 리더 강화" if lead else "💰 돈 들어오는 중"
        elif weak_out:
            state = "📉 리더 약화(돈 빠짐)" if lead else "🧊 돈 빠지는 중"
        else:
            state = "👑 리더 유지" if lead else "· 변화 없음"
        rows.append({"group": g, "state": state, "leading": lead, "n": m["n"],
                     "share_prev": s_prev, "share_now": s_now, "share_chg": s_now - s_prev, "share_ratio": share_ratio,
                     "tv_ratio": m["tv_ratio"], "n_break": m["n_break"], "n_hold": m["n_hold"],
                     "rs_up": m["rs_up"], "above20": m["above20"]})
    out = pd.DataFrame(rows)
    summary = {"all_tv_ratio": mall["tv_ratio"], "gainers": [], "losers": []}
    if not out.empty:
        out = out.sort_values("share_chg", ascending=False).reset_index(drop=True)
        summary["gainers"] = out[out["state"].isin(["🔥 리더 강화", "💰 돈 들어오는 중"])]["group"].head(4).tolist()
        summary["losers"] = out[out["state"].isin(["📉 리더 약화(돈 빠짐)", "🧊 돈 빠지는 중"])] \
            .sort_values("share_chg")["group"].head(4).tolist()
    return out, summary


# ─────────────────────────── 🏁 신고가 후보 · 💥 거래량 폭발 ───────────────────────────
NHC_DEFAULTS = {"imminent": 2.0, "near": 7.0, "watch": 15.0, "tv_min": 1.5, "rs_min": 80, "fresh_days": 60}
NHC_STATES = ("돌파", "터치", "임박", "근접", "관찰")


def _is_session_now(today: date) -> bool:
    """오늘이 평일이고 9시가 지났으면 '오늘 봉이 생겼어야 하는' 시간이에요(주말·장 전엔 일봉 마지막 봉이 지금 세션)."""
    now = now_kst()
    return now.date() == today and now.weekday() < 5 and now.time() >= time(9, 0)


def _session_bars(hist: pd.DataFrame, quote: dict | None, today: date | None = None):
    """일봉 + 실시간 시세 → 마지막 칸이 '지금 세션'인 배열 (날짜, 시가, 고가, 저가, 종가, 거래량).

    - 일봉 마지막이 오늘이면 그 봉에 현재가·장중 고가·누적 거래량을 덮어써요.
    - 일봉에 오늘 봉이 아직 없고 장중(OPEN)이면 오늘 봉을 새로 붙여요.
    - 그 밖(주말·장 전)이면 일봉 마지막 봉이 지금 세션이에요.
    """
    today = today or now_kst().date()
    d = hist["date"].values.astype("datetime64[D]")
    o = hist["open"].to_numpy(dtype=float) if "open" in hist else hist["close"].to_numpy(dtype=float)
    h, l, c = (hist[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    if not ((h > 0).all() and (l > 0).all() and (o > 0).all()):
        h, l, o = np.where(h > 0, h, c), np.where(l > 0, l, c), np.where(o > 0, o, c)
    v = hist["volume"].to_numpy(dtype=float) if "volume" in hist else np.full(len(d), np.nan)
    q = quote or {}
    price, qh, ql, qv = q.get("price"), q.get("high"), q.get("low"), q.get("volume")
    t64 = np.datetime64(today, "D")
    if len(d) and d[-1] == t64:
        o, h, l, c, v = (x.copy() for x in (o, h, l, c, v))
        if price:
            c[-1] = float(price)
            h[-1] = max(h[-1], float(qh or price), float(price))
            l[-1] = min(l[-1], float(ql or price), float(price))
        if qv and not (v[-1] >= qv):
            v[-1] = float(qv)
    elif price and q.get("status") == "OPEN" and _is_session_now(today):
        d = np.append(d, t64)
        o = np.append(o, float(price))
        h = np.append(h, max(float(qh or price), float(price)))
        l = np.append(l, min(float(ql or price), float(price)))
        c = np.append(c, float(price))
        v = np.append(v, float(qv) if qv else np.nan)
    return d, o, h, l, c, v


def nh_candidate(hist: pd.DataFrame, quote: dict | None, today: date | None = None, lookback: int = 250,
                 spark_n: int = 120, p: dict | None = None, ref_override: float | None = None) -> dict:
    """신고가 후보 상태.

    기준가 = 지금 세션을 뺀 직전 250거래일 최고가(장중 고가). 상태:
      돌파 = 현재가가 기준가 위 · 터치 = 오늘 고가는 기준가를 넘었지만 현재가는 아래
      임박/근접/관찰 = 기준가까지 남은 %가 imminent/near/watch 이내
    거래대금 배수 = 오늘 거래대금 ÷ 직전 20거래일 평균 거래대금
    최근 신고가 횟수 = 지금 세션 전 fresh_days거래일 안에 52주 신고가(고가 기준)를 쓴 날 수 (0이면 '신선')
    """
    p = {**NHC_DEFAULTS, **(p or {})}
    out = {"ref_high": None, "nh_state": None, "nh_dist": None, "nh_brk": None, "tv_today": None, "tv_ratio": None,
           "nh_recent": None, "spark": None}
    if hist is None or hist.empty or len(hist) < 60:
        return out
    d, o, h, l, c, v = _session_bars(hist, quote, today)
    n = len(c)
    if n < 61:
        return out
    prior = h[max(0, n - 1 - lookback):n - 1]
    prior = prior[np.isfinite(prior)]
    if not len(prior):
        return out
    ref = float(ref_override) if ref_override else float(prior.max())
    price, hi_now = float(c[-1]), float(h[-1])
    dist = (ref / price - 1) * 100
    if price > ref:
        state = "돌파"
    elif hi_now >= ref:
        state = "터치"
    elif dist <= p["imminent"]:
        state = "임박"
    elif dist <= p["near"]:
        state = "근접"
    elif dist <= p["watch"]:
        state = "관찰"
    else:
        state = None
    tv = c * v
    base = tv[max(0, n - 21):n - 1]
    base = base[np.isfinite(base)]
    tv20 = float(base.mean()) if len(base) >= 10 else None
    tv_today = float(tv[-1]) if np.isfinite(tv[-1]) else None
    pm = _rolling_prior_max(h, lookback, 120)
    fd = int(p["fresh_days"])
    seg_h, seg_pm = h[n - 1 - fd:n - 1], pm[n - 1 - fd:n - 1]
    ok = np.isfinite(seg_pm)
    out.update(
        ref_high=ref, nh_state=state, nh_dist=dist, nh_brk=(price / ref - 1) * 100,
        tv_today=tv_today, tv_ratio=(tv_today / tv20) if (tv_today and tv20) else None,
        nh_recent=int((seg_h[ok] > seg_pm[ok]).sum()),
        spark=[round(float(x), 2) for x in c[-spark_n:] if np.isfinite(x)],
    )
    return out


def nh_candidates(df: pd.DataFrame, histories: dict, quotes: dict, p: dict | None = None) -> pd.DataFrame:
    """보드의 국내 종목 중 신고가 후보(돌파·터치·임박·근접·관찰)만 골라 섹션(신선·돌파권·돌파 중·터치 후 밀림)을 붙여요."""
    p = {**NHC_DEFAULTS, **(p or {})}
    today = now_kst().date()
    rows = []
    for _, r in df.iterrows():
        code = r["code"]
        if not is_kr(code):
            continue
        hist = (histories.get(code) or (None, None, None))[1]
        # 일봉 최고가가 네이버 공식값과 달라 교정된 종목: 오늘 이전에 쓴 52주 최고가를 기준가로 써요
        ref = None
        if r.get("h52_fixed") is True and r.get("days_since_high") == r.get("days_since_high") \
                and (r.get("days_since_high") or 0) > 0 and r.get("high52") == r.get("high52"):
            ref = float(r["high52"])
        res = nh_candidate(hist, quotes.get(code), today=today, p=p, ref_override=ref)
        if res["nh_state"] is None:
            continue
        rows.append({"code": code, **res})
    cols = ["code", *nh_candidate(None, None).keys()]
    cand = pd.DataFrame(rows, columns=cols)
    if cand.empty:
        return cand.assign(section=[], fresh=[])
    keep = [k for k in ("name", "group", "sector", "market", "price", "change", "atr_pct", "rs", "rs_1m", "url",
                        "cap_krw", "tv20") if k in df.columns]
    cand = cand.merge(df[["code", *keep]].drop_duplicates("code"), on="code", how="left")
    rs = pd.to_numeric(cand["rs"], errors="coerce").fillna(0)
    tvr = pd.to_numeric(cand["tv_ratio"], errors="coerce").fillna(0)
    strong = (rs >= p["rs_min"]) & (tvr >= p["tv_min"])
    st_ = cand["nh_state"]
    cand["section"] = None
    cand.loc[st_ == "돌파", "section"] = "돌파 중"
    cand.loc[st_ == "터치", "section"] = "터치 후 밀림"
    cand.loc[st_.isin(["임박", "근접"]) & strong, "section"] = "돌파권"
    cand["fresh"] = st_.isin(["돌파", "터치", "임박", "근접"]) & strong & (cand["nh_recent"] == 0)
    # 정렬: 돌파(돌파 폭 큰 순) → 나머지는 기준가까지 가까운 순
    order = {s: i for i, s in enumerate(NHC_STATES)}
    cand["_o"] = cand["nh_state"].map(order)
    cand["_k"] = np.where(cand["nh_state"] == "돌파", -cand["nh_brk"], cand["nh_dist"])
    return cand.sort_values(["_o", "_k"]).drop(columns=["_o", "_k"]).reset_index(drop=True)


VS_DEFAULTS = {"quiet": 90, "recent": 5, "mult": 3.0, "quiet_cap": 2.0, "min_tv": 10e8}


def volume_surge(hist: pd.DataFrame, quote: dict | None, today: date | None = None, p: dict | None = None,
                 spark_n: int = 160) -> dict:
    """몇 달 조용하다가 최근 거래량이 갑자기 터진 종목(일진전기형) 판정용 수치.

    - 조용한 기간 = 최근 recent거래일을 뺀 그 앞 quiet거래일. 기준 거래량 = 그 기간 거래량의 중앙값
    - 폭발 배수 = 최근 recent거래일 중 가장 큰 거래량 ÷ 기준 거래량
    - 조용함 = 조용한 기간의 5일 평균 거래량이 기준의 quiet_cap배를 한 번도 안 넘었는지(한두 날 튄 건 괜찮아요)
    - 거래 수준 = 조용한 기간 중앙값 ÷ 그 앞 1년 중앙값 (1보다 작을수록 평소보다 더 말라 있던 것)
    """
    p = {**VS_DEFAULTS, **(p or {})}
    keys = ("vs_mult", "vs_days", "vs_peak_date", "vs_peak_up", "vs_peak_tv", "vs_avg_mult", "vs_quiet_ok",
            "vs_quiet_max", "vs_dry", "vs_ret", "vs_range", "vs_since", "vs_vol", "vs_close", "vs_split")
    out = {k: None for k in keys}
    if hist is None or hist.empty:
        return out
    d, o, h, l, c, v = _session_bars(hist, quote, today)
    q, rcn = int(p["quiet"]), int(p["recent"])
    n = len(v)
    if n < q + rcn + 5:
        return out
    base = v[n - rcn - q:n - rcn]
    base = base[np.isfinite(base) & (base > 0)]
    if len(base) < q * 0.7:
        return out
    med = float(np.median(base))
    if med <= 0:
        return out
    rec = v[n - rcn:]
    rec = np.where(np.isfinite(rec), rec, 0)
    ratios = rec / med
    pk = int(np.argmax(ratios))
    pk_i = n - rcn + pk
    roll5 = pd.Series(v[n - rcn - q:n - rcn]).rolling(5, min_periods=3).mean().to_numpy() / med
    quiet_max = float(np.nanmax(roll5)) if np.isfinite(roll5).any() else None
    older = v[max(0, n - rcn - q - 250):n - rcn - q]
    older = older[np.isfinite(older) & (older > 0)]
    seg_c = c[n - rcn - q:n - rcn]
    seg_c = seg_c[np.isfinite(seg_c)]
    hit = np.nonzero(ratios >= p["mult"])[0]
    out.update(
        vs_mult=float(ratios[pk]),
        vs_days=int(len(hit)),
        vs_peak_date=pd.Timestamp(d[pk_i]).date(),
        vs_peak_up=bool(c[pk_i] >= o[pk_i]) if np.isfinite(o[pk_i]) else None,
        vs_peak_tv=float(c[pk_i] * v[pk_i]) if np.isfinite(v[pk_i]) else None,
        vs_avg_mult=float(rec.mean() / med),
        vs_quiet_ok=bool(quiet_max is not None and quiet_max <= p["quiet_cap"]),
        vs_quiet_max=quiet_max,
        vs_dry=float(med / np.median(older)) if len(older) >= 60 else None,
        vs_ret=float((c[-1] / c[n - rcn - 1] - 1) * 100) if c[n - rcn - 1] > 0 else None,
        vs_range=float((seg_c.max() / seg_c.min() - 1) * 100) if len(seg_c) and seg_c.min() > 0 else None,
        vs_since=int(n - 1 - (n - rcn + int(hit[0]))) if len(hit) else None,
        vs_vol=[0.0 if not np.isfinite(x) else round(float(x)) for x in v[-spark_n:]],
        vs_close=[round(float(x), 2) for x in c[-spark_n:]],
        vs_split=int(min(spark_n, n) - rcn - q) ,
    )
    return out


def volume_surges(df: pd.DataFrame, histories: dict, quotes: dict, p: dict | None = None,
                  only_quiet: bool = True) -> pd.DataFrame:
    """보드의 국내 종목 중 거래량 폭발 종목. 조건: 폭발 배수 ≥ mult · 폭발일 거래대금 ≥ min_tv · (조용함)."""
    p = {**VS_DEFAULTS, **(p or {})}
    today = now_kst().date()
    rows = []
    for _, r in df.iterrows():
        code = r["code"]
        if not is_kr(code):
            continue
        hist = (histories.get(code) or (None, None, None))[1]
        res = volume_surge(hist, quotes.get(code), today=today, p=p)
        if res["vs_mult"] is None or res["vs_mult"] < p["mult"]:
            continue
        if (res["vs_peak_tv"] or 0) < p["min_tv"]:
            continue
        if only_quiet and not res["vs_quiet_ok"]:
            continue
        rows.append({"code": code, **res})
    out = pd.DataFrame(rows, columns=["code", *volume_surge(None, None).keys()])
    if out.empty:
        return out
    keep = [k for k in ("name", "group", "sector", "market", "price", "change", "to_high", "rs", "rs_1m", "url",
                        "aligned", "cap_krw") if k in df.columns]
    out = out.merge(df[["code", *keep]].drop_duplicates("code"), on="code", how="left")
    return out.sort_values("vs_mult", ascending=False).reset_index(drop=True)
