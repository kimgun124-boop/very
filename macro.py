"""매크로 시황: 세계 지수·섹터 ETF·대장주·금리·환율·원자재·위험 지표와 사실 필터 뉴스.

- 시세: 야후(yfinance) 일봉(15분 안팎 지연). 두 번째 출처로 스투크(stooq) 종가를 받아 맞춰 봐요.
  같은 날 종가 차이가 0.15% 안이면 '교차확인', 넘으면 '상충', 두 번째 출처가 없으면 '단일출처'.
- 신용 스프레드·기대인플레·실질금리: 미국 세인트루이스 연준(FRED) CSV.
- 뉴스: 구글 뉴스 RSS(언론사 이름이 붙어 와요)와 CNBC RSS. 같은 사건을 서로 다른 언론사 2곳 이상이 보도하면
  '교차확인', 한 곳이면 '단일출처'. 당사자 주장·미확인 보도는 '주장', 전망·추측·질문형 제목은 '의견'으로 나눠요.
"""
from __future__ import annotations

import html
import io
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import pandas as pd

import data

MACRO_VERSION = "2026-10-20-macro"   # app.py의 MACRO_VERSION과 같아야 해요

# ─────────────────────────── 지켜볼 종목 ───────────────────────────
# stooq: 두 번째 출처 티커(없으면 단일출처). region: 표의 묶음
INDICES = [
    {"name": "S&P 500", "yf": "^GSPC", "stooq": "^spx", "region": "미국"},
    {"name": "나스닥 종합", "yf": "^IXIC", "stooq": "^ndq", "region": "미국"},
    {"name": "다우존스", "yf": "^DJI", "stooq": "^dji", "region": "미국"},
    {"name": "러셀 2000", "yf": "^RUT", "stooq": None, "region": "미국"},
    {"name": "코스피", "yf": "^KS11", "stooq": None, "region": "한국"},
    {"name": "코스닥", "yf": "^KQ11", "stooq": None, "region": "한국"},
    {"name": "닛케이 225", "yf": "^N225", "stooq": "^nkx", "region": "일본"},
    {"name": "대만 가권", "yf": "^TWII", "stooq": None, "region": "대만"},
    {"name": "상하이종합", "yf": "000001.SS", "stooq": "^shc", "region": "중국"},
    {"name": "항셍", "yf": "^HSI", "stooq": "^hsi", "region": "홍콩"},
    {"name": "인도 니프티", "yf": "^NSEI", "stooq": None, "region": "인도"},
    {"name": "독일 DAX", "yf": "^GDAXI", "stooq": "^dax", "region": "유럽"},
    {"name": "유로스톡스 50", "yf": "^STOXX50E", "stooq": None, "region": "유럽"},
    {"name": "영국 FTSE", "yf": "^FTSE", "stooq": "^ukx", "region": "유럽"},
]

# kind: rate(금리, 변화는 %p) · fx · commodity · crypto
MACRO = [
    {"name": "미국 5년물", "yf": "^FVX", "kind": "rate", "group": "금리", "fred": "DGS5"},
    {"name": "미국 10년물", "yf": "^TNX", "kind": "rate", "group": "금리", "fred": "DGS10"},
    {"name": "미국 30년물", "yf": "^TYX", "kind": "rate", "group": "금리", "fred": "DGS30"},
    {"name": "달러인덱스", "yf": "DX-Y.NYB", "kind": "fx", "group": "환율"},
    {"name": "원/달러", "yf": "KRW=X", "kind": "fx", "group": "환율", "stooq": "usdkrw"},
    {"name": "엔/달러", "yf": "JPY=X", "kind": "fx", "group": "환율", "stooq": "usdjpy"},
    {"name": "유로/달러", "yf": "EURUSD=X", "kind": "fx", "group": "환율", "stooq": "eurusd"},
    {"name": "WTI", "yf": "CL=F", "kind": "commodity", "group": "원자재", "unit": "$"},
    {"name": "브렌트", "yf": "BZ=F", "kind": "commodity", "group": "원자재", "unit": "$"},
    {"name": "금", "yf": "GC=F", "kind": "commodity", "group": "원자재", "unit": "$"},
    {"name": "구리", "yf": "HG=F", "kind": "commodity", "group": "원자재", "unit": "$"},
    {"name": "천연가스", "yf": "NG=F", "kind": "commodity", "group": "원자재", "unit": "$"},
    {"name": "비트코인", "yf": "BTC-USD", "kind": "crypto", "group": "원자재", "unit": "$"},
]

# 변동성 지수는 야후, 신용·물가·실질금리는 FRED
VOL = [
    {"name": "VIX (주식 변동성)", "yf": "^VIX", "read": "20 위면 주식시장 불안이 큰 편, 30 위면 매우 큰 편"},
    {"name": "MOVE (채권 변동성)", "yf": "^MOVE", "read": "미 국채 옵션 내재변동성. 100 위면 채권 불안이 큰 편"},
    {"name": "OVX (원유 변동성)", "yf": "^OVX", "read": "WTI 옵션 내재변동성"},
]
FRED = [
    {"name": "하이일드 스프레드", "id": "BAMLH0A0HYM2", "group": "신용", "read": "하이일드 회사채 − 국채 금리. 오르면 신용 위험을 더 높게 본다는 뜻"},
    {"name": "BBB 회사채 스프레드", "id": "BAMLC0A4CBBB", "group": "신용", "read": "투자등급 하단 회사채 − 국채 금리"},
    {"name": "10년 기대인플레(BEI)", "id": "T10YIE", "group": "물가·실질금리", "read": "10년 명목금리 − 물가연동채 금리"},
    {"name": "10년 실질금리(TIPS)", "id": "DFII10", "group": "물가·실질금리", "read": "물가 영향을 뺀 10년 금리"},
    {"name": "장단기 금리차(10Y−2Y)", "id": "T10Y2Y", "group": "물가·실질금리", "read": "음수면 장단기 금리 역전"},
]

SECTORS = [
    ("XLK", "기술"), ("XLC", "커뮤니케이션"), ("XLY", "경기소비재"), ("XLP", "필수소비재"), ("XLE", "에너지"),
    ("XLF", "금융"), ("XLV", "헬스케어"), ("XLI", "산업재"), ("XLB", "소재"), ("XLRE", "부동산"),
    ("XLU", "유틸리티"), ("SMH", "반도체"),
]
# 섹터 ETF → 대장주(시가총액 상위 위주)
LEADERS = {
    "SMH": [("NVDA", "엔비디아"), ("TSM", "TSMC"), ("AVGO", "브로드컴"), ("MU", "마이크론"), ("AMD", "AMD")],
    "XLK": [("AAPL", "애플"), ("MSFT", "마이크로소프트"), ("ORCL", "오라클"), ("PLTR", "팔란티어")],
    "XLC": [("GOOGL", "알파벳"), ("META", "메타"), ("NFLX", "넷플릭스")],
    "XLY": [("AMZN", "아마존"), ("TSLA", "테슬라"), ("HD", "홈디포")],
    "XLP": [("WMT", "월마트"), ("COST", "코스트코"), ("PG", "P&G")],
    "XLE": [("XOM", "엑슨모빌"), ("CVX", "셰브론")],
    "XLF": [("JPM", "JP모건"), ("BRK-B", "버크셔"), ("V", "비자")],
    "XLV": [("LLY", "일라이릴리"), ("UNH", "유나이티드헬스"), ("JNJ", "존슨앤존슨")],
    "XLI": [("GE", "GE에어로스페이스"), ("CAT", "캐터필러"), ("VRT", "버티브")],
    "XLB": [("LIN", "린데")],
    "XLRE": [("PLD", "프롤로지스")],
    "XLU": [("NEE", "넥스트에라"), ("CEG", "컨스텔레이션"), ("VST", "비스트라")],
}
THEMES = [
    ("EWY", "한국"), ("DRAM", "메모리"), ("MAGS", "M7"), ("XBI", "바이오"), ("IWO", "소형 성장"), ("EEM", "신흥국"),
    ("LIT", "리튬·배터리"), ("URA", "우라늄·원전"), ("GRID", "전력망"), ("ITA", "미 방산"), ("SHLD", "방산테크"),
    ("TAN", "태양광"), ("FAN", "풍력"), ("RSP", "S&P 동일가중"), ("SPY", "S&P 500"),
]
# 한국 대장주는 네이버 일봉(장중 갱신)으로 받아요
KR_LEADERS = [
    ("005930", "삼성전자", "반도체"), ("000660", "SK하이닉스", "반도체"), ("373220", "LG에너지솔루션", "2차전지"),
    ("207940", "삼성바이오로직스", "바이오"), ("005380", "현대차", "자동차"), ("034020", "두산에너빌리티", "원전·전력"),
    ("012450", "한화에어로스페이스", "방산"), ("267260", "HD현대일렉트릭", "전력기기"), ("105560", "KB금융", "금융"),
]

TOL = 0.15          # 두 출처 종가 차이 허용(%)


def all_yahoo() -> list[str]:
    t = [i["yf"] for i in INDICES] + [m["yf"] for m in MACRO] + [v["yf"] for v in VOL]
    t += [s for s, _ in SECTORS] + [s for s, _ in THEMES]
    t += [s for v in LEADERS.values() for s, _ in v]
    return list(dict.fromkeys(t))


# ─────────────────────────── 시세 ───────────────────────────
def fetch_prices() -> dict[str, pd.DataFrame]:
    """야후 일봉 7개월치. 한국 대장주는 네이버. {티커: 일봉}"""
    tickers = all_yahoo()
    if data.MOCK:
        out = {t: data.mock_history(t, 150) for t in tickers}
    else:
        out = data._yf_batch(tickers, period="7mo", interval="1d")
        miss = [t for t in tickers if t not in out]
        for t in miss[:25]:          # 한꺼번에 받다 빠진 티커만 하나씩 다시(너무 많으면 야후가 막힌 것)
            _, df, _err = data.fetch_history_overseas(t)
            if not df.empty:
                out[t] = df.tail(150).reset_index(drop=True)
    kr = [c for c, _, _ in KR_LEADERS]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for c, (_, df, _err) in zip(kr, pool.map(lambda c: data.fetch_history(c, count=150), kr)):
            if df is not None and not df.empty:
                out[c] = df
    return out


def summarize(df: pd.DataFrame | None) -> dict | None:
    """마지막 종가·기준일·1일/5일/20일 변화·50일선 위치."""
    if df is None or len(df) < 2:
        return None
    c = df["close"].astype(float).reset_index(drop=True)
    last = float(c.iloc[-1])

    def back(n):
        return float(c.iloc[-1 - n]) if len(c) > n else None

    def pct(n):
        b = back(n)
        return (last / b - 1) * 100 if b else None

    ma50 = float(c.tail(50).mean()) if len(c) >= 50 else None
    return {
        "last": last, "date": pd.Timestamp(df["date"].iloc[-1]),
        "d1": pct(1), "d5": pct(5), "d20": pct(20),
        "a1": last - back(1) if back(1) is not None else None,          # 금리용 절대 변화(%p)
        "a20": last - back(20) if back(20) is not None else None,
        "ma50": ma50, "above50": (last > ma50) if ma50 else None,
        "spark": c.tail(60).round(4).tolist(),
    }


def parse_stooq(text: str) -> dict | None:
    """stooq 간이 시세 CSV(Symbol,Date,Time,Open,High,Low,Close,...) → {date, close}."""
    try:
        df = pd.read_csv(io.StringIO(text))
        row = df.iloc[0]
        close = float(row["Close"])
        if not close or pd.isna(close):
            return None
        return {"date": pd.Timestamp(str(row["Date"])), "close": close}
    except Exception:
        return None


def fetch_stooq(sym: str) -> dict | None:
    if data.MOCK:
        return None
    try:
        r = data.session.get("https://stooq.com/q/l/", params={"s": sym, "f": "sd2t2ohlc", "h": "", "e": "csv"})
        return parse_stooq(r.text) if r.ok else None
    except Exception:
        return None


def verify(primary: dict | None, second: dict | None, tol: float = TOL) -> tuple[str, str]:
    """(검증 상태, 설명). 같은 날 종가끼리만 비교해요."""
    if not primary:
        return "없음", "불러오지 못했어요"
    if not second:
        return "단일출처", "두 번째 출처 없음"
    if second["date"].normalize() != primary["date"].normalize():
        return "단일출처", f"두 번째 출처 기준일이 달라요({second['date']:%m/%d})"
    gap = abs(second["close"] / primary["last"] - 1) * 100
    if gap <= tol:
        return "교차확인", f"stooq {second['close']:,.2f} (차이 {gap:.2f}%)"
    return "상충", f"stooq {second['close']:,.2f} (차이 {gap:.2f}%)"


def stooq_for(sym: str) -> str | None:
    """ETF·미국 종목은 stooq에서 '티커.us'."""
    for i in INDICES + MACRO:
        if i["yf"] == sym:
            return i.get("stooq")
    if re.fullmatch(r"[A-Z]{1,5}", sym):
        return sym.lower() + ".us"
    if sym == "BRK-B":
        return "brk-b.us"
    return None


def cross_check(syms: list[str]) -> dict[str, dict | None]:
    pairs = [(s, stooq_for(s)) for s in syms]
    pairs = [(s, q) for s, q in pairs if q]
    with ThreadPoolExecutor(max_workers=6) as pool:
        got = list(pool.map(lambda p: fetch_stooq(p[1]), pairs))
    return {s: g for (s, _), g in zip(pairs, got)}


# ─────────────────────────── FRED ───────────────────────────
def parse_fred_csv(text: str) -> pd.DataFrame:
    """FRED fredgraph.csv → date, value (빈 값 '.' 제거)."""
    df = pd.read_csv(io.StringIO(text))
    if df.shape[1] < 2:
        return pd.DataFrame(columns=["date", "value"])
    df.columns = ["date", "value"]
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.dropna().reset_index(drop=True)


def fetch_fred(series: str) -> pd.DataFrame:
    if data.MOCK:
        h = data.mock_history(series, 150)
        return pd.DataFrame({"date": h["date"], "value": (h["close"] / h["close"].iloc[0] * 3).round(2)})
    try:
        r = data.session.get("https://fred.stlouisfed.org/graph/fredgraph.csv",
                             params={"id": series, "cosd": (pd.Timestamp.today() - pd.Timedelta(days=200)).strftime("%Y-%m-%d")})
        return parse_fred_csv(r.text) if r.ok else pd.DataFrame(columns=["date", "value"])
    except Exception:
        return pd.DataFrame(columns=["date", "value"])


def fred_summary(df: pd.DataFrame) -> dict | None:
    if df is None or len(df) < 2:
        return None
    v = df["value"].astype(float).reset_index(drop=True)
    last = float(v.iloc[-1])
    return {"last": last, "date": pd.Timestamp(df["date"].iloc[-1]), "a1": last - float(v.iloc[-2]),
            "a20": last - float(v.iloc[-21]) if len(v) > 20 else None, "spark": v.tail(60).round(3).tolist()}


# ─────────────────────────── 뉴스 사실 필터 ───────────────────────────
NEWS_QUERIES = [
    ("en", "stock market close S&P 500 Nasdaq"), ("en", "Federal Reserve rates Treasury yields"),
    ("en", "oil prices Brent WTI"), ("en", "semiconductor stocks Nvidia memory"),
    ("en", "Iran Strait of Hormuz"), ("en", "CPI inflation jobs report"),
    ("ko", "코스피 마감"), ("ko", "원달러 환율"), ("ko", "반도체 수출"),
]
DIRECT_FEEDS = [("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html")]

# 전망·추측·질문형: 사실이 아니라 의견
OPINION = re.compile(
    r"\b(could|might|may|would|should|likely|unlikely|expected to|expects?|forecasts?|predicts?|outlook|"
    r"poised|set to|eyes|bets?|fears?|hopes?|why|what to (?:know|expect|watch)|here'?s|how to|"
    r"stocks? to (?:buy|watch)|top picks?|best|worst|opinion|analysis|commentary|preview)\b|\?$|"
    r"전망|가능성|예상|관측|우려|기대감|분석|칼럼|시각|어디로|될까|할까|주목",
    re.I,
)
# 당사자 주장·미확인 보도
CLAIM = re.compile(
    r"\b(claims?|claimed|allegedly|reportedly|unconfirmed|unverified|rumou?rs?|sources say|according to sources)\b|"
    r"주장|미확인|설\s|소식통|카더라",
    re.I,
)
STOP = set("the a an of to in on for and or at by with from as is are be its it this that after amid over into "
           "new says said stock stocks market markets news today live update updates vs".split())


def _strip_source(title: str, source: str) -> str:
    """구글 뉴스 제목 끝의 ' - 언론사'를 떼요."""
    if source and title.endswith(" - " + source):
        return title[: -len(source) - 3]
    return re.sub(r"\s+-\s+[^-]{2,40}$", "", title)


def parse_rss(text: str, default_source: str = "") -> list[dict]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    out = []
    for it in root.iter("item"):
        title = html.unescape((it.findtext("title") or "").strip())
        link = (it.findtext("link") or "").strip()
        src_el = it.find("source")
        source = (src_el.text or "").strip() if src_el is not None and src_el.text else default_source
        pub = it.findtext("pubDate")
        try:
            when = parsedate_to_datetime(pub).astimezone(timezone.utc) if pub else None
        except (TypeError, ValueError):
            when = None
        if not title or not source:
            continue
        out.append({"title": _strip_source(title, source), "source": source, "url": link, "when": when})
    return out


def tokens(title: str) -> set[str]:
    t = re.sub(r"[^\w%.$]+", " ", title.lower())
    return {w for w in t.split() if (len(w) > 2 or any(ch.isdigit() for ch in w)) and w not in STOP}


def classify(title: str) -> str:
    if CLAIM.search(title):
        return "주장"
    if OPINION.search(title):
        return "의견"
    return "사실"


def cluster(items: list[dict], thr: float = 0.34) -> list[dict]:
    """비슷한 제목끼리 묶어 사건 하나로. 서로 다른 언론사 수로 교차확인을 정해요."""
    groups: list[dict] = []
    for it in sorted(items, key=lambda x: x["when"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True):
        tk = tokens(it["title"])
        if not tk:
            continue
        best, score = None, 0.0
        for g in groups:
            inter = len(tk & g["tokens"])
            j = inter / len(tk | g["tokens"])
            num = any(any(ch.isdigit() for ch in w) for w in tk & g["tokens"])
            s = j + (0.1 if num and inter >= 3 else 0)
            if s > score:
                best, score = g, s
        if best is not None and score >= thr:
            best["items"].append(it)
            best["sources"].add(it["source"])
            best["tokens"] |= tk
        else:
            groups.append({"items": [it], "sources": {it["source"]}, "tokens": set(tk)})
    out = []
    for g in groups:
        head = g["items"][0]
        kinds = [classify(i["title"]) for i in g["items"]]
        kind = "주장" if "주장" in kinds else ("사실" if "사실" in kinds else "의견")
        # 사실로 분류된 제목이 있으면 그걸 대표로
        lead = next((i for i, k in zip(g["items"], kinds) if k == kind), head)
        status = kind if kind in ("주장", "의견") else ("교차확인" if len(g["sources"]) >= 2 else "단일출처")
        out.append({
            "title": lead["title"], "url": lead["url"], "when": lead["when"], "status": status,
            "sources": sorted(g["sources"]), "n": len(g["sources"]),
            "has_number": bool(re.search(r"\d", lead["title"])),
            "others": [{"title": i["title"], "source": i["source"], "url": i["url"]} for i in g["items"] if i is not lead][:5],
        })
    rank = {"교차확인": 0, "단일출처": 1, "주장": 2, "의견": 3}
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    out.sort(key=lambda x: (rank[x["status"]], -x["n"], not x["has_number"], -(x["when"] or epoch).timestamp()))
    return out


def _google_rss(lang: str, q: str) -> list[dict]:
    hl = {"en": ("en-US", "US", "US:en"), "ko": ("ko", "KR", "KR:ko")}[lang]
    try:
        r = data.session.get("https://news.google.com/rss/search",
                             params={"q": q + " when:1d", "hl": hl[0], "gl": hl[1], "ceid": hl[2]})
        return parse_rss(r.text) if r.ok else []
    except Exception:
        return []


def _direct(name: str, url: str) -> list[dict]:
    try:
        r = data.session.get(url)
        return parse_rss(r.text, default_source=name) if r.ok else []
    except Exception:
        return []


def fetch_news() -> list[dict]:
    if data.MOCK:
        now = datetime.now(timezone.utc)
        items = [
            {"title": "S&P 500 rises 0.4% as chip stocks rebound", "source": "Reuters", "url": "https://example.com/1", "when": now},
            {"title": "S&P 500 gains 0.4% as chipmakers rebound", "source": "AP News", "url": "https://example.com/2", "when": now},
            {"title": "Oil could hit $120 if Hormuz stays closed, analysts say", "source": "CNBC", "url": "https://example.com/3", "when": now},
            {"title": "Houthis claim attack on Saudi Aramco facility", "source": "Reuters", "url": "https://example.com/4", "when": now},
            {"title": "코스피 1.76% 오른 5,768 마감…기관 순매수", "source": "연합뉴스", "url": "https://example.com/5", "when": now},
        ]
        return cluster(items)
    jobs = [lambda l=l, q=q: _google_rss(l, q) for l, q in NEWS_QUERIES]
    jobs += [lambda n=n, u=u: _direct(n, u) for n, u in DIRECT_FEEDS]
    with ThreadPoolExecutor(max_workers=6) as pool:
        lists = list(pool.map(lambda f: f(), jobs))
    seen, items = set(), []
    for lst in lists:
        for it in lst:
            key = (it["source"], it["title"].lower())
            if key not in seen:
                seen.add(key)
                items.append(it)
    return cluster(items)


# ─────────────────────────── 한 번에 모으기 ───────────────────────────
def snapshot() -> dict:
    """화면·알림이 함께 쓰는 매크로 묶음."""
    prices = fetch_prices()
    sm = {t: summarize(df) for t, df in prices.items()}
    check_syms = [i["yf"] for i in INDICES if i.get("stooq")] + [m["yf"] for m in MACRO if m.get("stooq")]
    check_syms += [s for s, _ in SECTORS] + [s for s, _ in THEMES] + [s for v in LEADERS.values() for s, _ in v]
    second = cross_check(check_syms)
    ver = {s: verify(sm.get(s), second.get(s)) for s in sm}
    with ThreadPoolExecutor(max_workers=4) as pool:
        fred_raw = dict(zip([f["id"] for f in FRED] + [m["fred"] for m in MACRO if m.get("fred")],
                            pool.map(fetch_fred, [f["id"] for f in FRED] + [m["fred"] for m in MACRO if m.get("fred")])))
    fred = {k: fred_summary(v) for k, v in fred_raw.items()}
    # 금리는 야후(^TNX 등)와 FRED 종가를 맞춰 봐요(0.03%p 안이면 교차확인)
    for m in MACRO:
        if m.get("fred"):
            a, b = sm.get(m["yf"]), fred.get(m["fred"])
            if a and b:
                same = a["date"].normalize() == b["date"].normalize()
                gap = abs(a["last"] - b["last"])
                ver[m["yf"]] = (("교차확인" if gap <= 0.03 else "상충") if same else "단일출처",
                                f"FRED {b['last']:.3f}% ({b['date']:%m/%d})")
            elif b and not a:
                sm[m["yf"]] = {**b, "d1": None, "d5": None, "d20": None, "above50": None, "ma50": None}
                ver[m["yf"]] = ("단일출처", "FRED")
    return {"at": data.now_kst(), "sm": sm, "ver": ver, "fred": fred, "news": fetch_news()}


def facts(snap: dict) -> list[str]:
    """숫자끼리 비교만 해서 만든 한 줄 요약(전망 없음)."""
    sm, out = snap["sm"], []
    spx, rsp, spy = sm.get("^GSPC"), sm.get("RSP"), sm.get("SPY")
    if spx and spx["d1"] is not None:
        out.append(f"S&P 500 {spx['d1']:+.2f}%")
    if rsp and spy and rsp["d1"] is not None and spy["d1"] is not None:
        gap = rsp["d1"] - spy["d1"]
        if abs(gap) >= 0.3:
            out.append(f"동일가중(RSP) {rsp['d1']:+.2f}% → " + ("시총가중보다 약함(소수 대형주 쏠림)" if gap < 0 else "시총가중보다 강함(중소형주까지 확산)"))
    secs = [(n, sm[s]["d1"]) for s, n in SECTORS if sm.get(s) and sm[s]["d1"] is not None]
    if secs:
        secs.sort(key=lambda x: x[1])
        up = sum(1 for _, v in secs if v > 0)
        out.append(f"섹터 {up}/{len(secs)}개 상승 · 최강 {secs[-1][0]} {secs[-1][1]:+.1f}% · 최약 {secs[0][0]} {secs[0][1]:+.1f}%")
    above = [sm[s]["above50"] for s, _ in SECTORS if sm.get(s) and sm[s]["above50"] is not None]
    if above:
        out.append(f"50일선 위 섹터 {sum(above)}/{len(above)}")
    vix, hy = sm.get("^VIX"), snap["fred"].get("BAMLH0A0HYM2")
    if vix:
        out.append(f"VIX {vix['last']:.1f}")
    if hy and hy.get("a20") is not None:
        out.append(f"하이일드 스프레드 {hy['last']:.2f}% (20일 {hy['a20']:+.2f}%p)")
    return out
