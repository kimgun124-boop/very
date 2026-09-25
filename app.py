"""밸류체인 신고가 보드.

실행: streamlit run app.py   (윈도우는 run.bat 더블클릭)
"""
from __future__ import annotations

import html

import altair as alt
import pandas as pd
import streamlit as st

import importlib
import time

import data
import stocks as stock_list

st.set_page_config(page_title="밸류체인 신고가 보드", page_icon="📈", layout="wide")

REQUIRED = ("is_kr", "market_of", "quote_url", "INDEXES", "fetch_index_histories", "index_summary",
            "fetch_kr_shares", "fetch_fx", "format_krw", "fetch_investor_flows", "fetch_market_overview",
            "market_mood", "fetch_stock_trends", "trend_summary", "resolve_codes", "INST_DETAIL",
            "breakout_hold", "add_rs_ranks", "atr_pct", "BO_MODES",
            "fetch_financials", "fetch_financials_many", "earnings_trend")
if any(not hasattr(data, n) for n in REQUIRED):
    # GitHub에서 파일을 바꾼 직후, 서버가 예전 data.py를 기억하고 있는 경우가 있어 한 번 새로 읽어 봅니다.
    data = importlib.reload(data)
    stock_list = importlib.reload(stock_list)
_missing = [n for n in REQUIRED if not hasattr(data, n)]
if _missing:
    st.error("GitHub의 data.py가 예전 내용이에요. 저장소에서 data.py를 열어 새 파일 내용으로 바꿔 주세요. "
             "'data (1).py'처럼 이름이 바뀐 파일이 따로 올라가 있지 않은지도 확인해 주세요. "
             f"(없는 기능: {', '.join(_missing)})")
    st.stop()

STOCKS = stock_list.STOCKS
GROUP_ORDER = stock_list.GROUP_ORDER
SECTOR_ORDER = stock_list.SECTOR_ORDER
TAGS = getattr(stock_list, "TAGS", {})
for _s in STOCKS:
    _s.setdefault("tags", [])
    _s.setdefault("notes", [])


@st.cache_data(ttl=3600, show_spinner="새로 추가된 종목의 코드를 네이버에서 찾는 중이에요.")
def load_pending_codes(names: tuple[str, ...]):
    return data.resolve_codes(names)


# stocks.py에서 코드를 ""로 둔 종목은 네이버 검색으로 코드를 찾아 붙여요(1시간마다 다시 확인).
PENDING_NAMES = tuple(r[2] for r in getattr(stock_list, "PENDING", []))
PENDING_MISS: dict = {}
if PENDING_NAMES and hasattr(stock_list, "attach"):
    _resolved, PENDING_MISS = load_pending_codes(PENDING_NAMES)
    STOCKS = STOCKS + stock_list.attach(_resolved)

UP, DOWN = "#D6333B", "#1F66C9"        # 한국식: 상승 빨강, 하락 파랑
NEW_HIGH_BG = "#FFF1C9"                # 신고가 행 강조
REFRESH = {"끄기": None, "30초": 30, "1분": 60, "5분": 300}
ALL_CODES = tuple(sorted({s["code"] for s in STOCKS}))
KR_CODES = tuple(c for c in ALL_CODES if data.is_kr(c))
OS_CODES = tuple(c for c in ALL_CODES if not data.is_kr(c))
UNIT = {"KRW": "원", "USD": "달러", "JPY": "엔", "EUR": "유로", "AUD": "호주달러", "HKD": "홍콩달러", "TWD": "대만달러", "GBp": "펜스",
        "CNY": "위안", "CHF": "스위스프랑"}


def fmt_price(value, currency: str) -> str:
    """원화는 정수, 외화는 소수 둘째 자리까지."""
    if value is None or pd.isna(value):
        return "-"
    return f"{value:,.0f}" if currency in ("KRW", "JPY") else f"{value:,.2f}"

st.markdown(
    """
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css');
.stApp, .stApp p, .stApp label, .stApp h1, .stApp h2, .stApp h3, .stMarkdown {
  font-family: 'Pretendard Variable', Pretendard, 'Malgun Gothic', sans-serif;
}
.stApp h1 { font-weight: 800; letter-spacing: -0.025em; }
.status { color: #51616C; font-size: 0.88rem; margin: -0.4rem 0 0.9rem; }
.radar { background: #17232E; border-radius: 14px; padding: 1.1rem 1.25rem 1.2rem; margin: 0 0 1.2rem; }
.radar-title { color: #FFFFFF; font-size: 1.05rem; font-weight: 700; }
.radar-rule { color: #9FB0BA; font-size: 0.84rem; margin: 0.15rem 0 0.85rem; }
.chips { display: flex; flex-wrap: wrap; gap: 0.6rem; }
.chip { background: #213241; border: 1px solid #304657; border-radius: 10px;
        padding: 0.6rem 0.8rem; min-width: 11rem; max-width: 22rem; }
.chip-top { display: flex; justify-content: space-between; align-items: baseline; gap: 0.8rem; }
.chip-group { color: #FFFFFF; font-weight: 700; font-size: 0.95rem; }
.chip-count { white-space: nowrap; flex-shrink: 0; color: #F2B134; font-weight: 800; font-size: 1.35rem; font-variant-numeric: tabular-nums; }
.chip-count small { font-size: 0.72rem; font-weight: 600; margin-left: 0.1rem; }
.chip-names { color: #B9C7CF; font-size: 0.8rem; line-height: 1.45; margin-top: 0.2rem; }
.radar-empty { color: #9FB0BA; font-size: 0.9rem; }
.st-key-mobile_view { display: none; }
.m-kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.4rem; margin: 0 0 0.8rem; }
.m-kpis div { background: #E7EDEF; border-radius: 10px; padding: 0.5rem 0.3rem; text-align: center; }
.m-kpis span { display: block; font-size: 0.68rem; color: #51616C; line-height: 1.25; }
.m-kpis b { font-size: 1.15rem; font-weight: 800; color: #16212B; font-variant-numeric: tabular-nums; }
.cards { display: flex; flex-direction: column; gap: 0.5rem; }
.stApp a.card { display: block; background: #FFFFFF; border: 1px solid #DCE3E7; border-radius: 12px;
  padding: 0.7rem 0.85rem; text-decoration: none; color: #16212B; }
.stApp a.card.hot { background: #FFF6DA; border-color: #EFD48A; }
.c-top, .c-mid, .c-meta { display: flex; justify-content: space-between; align-items: baseline; gap: 0.6rem; }
.c-name, .c-price { font-weight: 700; font-size: 1.02rem; }
.c-price, .c-chg, .c-meta { font-variant-numeric: tabular-nums; }
.c-mid { font-size: 0.78rem; color: #51616C; margin-top: 0.1rem; }
.c-chg { font-weight: 700; }
.c-bar { height: 5px; background: #E3E9EC; border-radius: 3px; margin: 0.5rem 0 0.35rem; overflow: hidden; }
.c-bar i { display: block; height: 100%; background: #2E6B6F; border-radius: 3px; }
.c-meta { font-size: 0.78rem; color: #51616C; }
.c-meta b { color: #16212B; }
.c-desc { font-size: 0.76rem; color: #6B7A84; margin-top: 0.3rem; line-height: 1.4;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.c-tag { display: inline-block; font-size: 0.66rem; font-weight: 700; padding: 0.05rem 0.4rem;
  border-radius: 6px; margin-left: 0.35rem; vertical-align: 2px; }
.c-tag.new { background: #F2B134; color: #16212B; }
.c-tag.al { background: #D9E8E6; color: #2E6B6F; }
@media (max-width: 640px) {
  .st-key-desk_kpis, .st-key-desk_table { display: none !important; }
  .st-key-mobile_view { display: flex !important; }
  .stApp h1 { font-size: 1.6rem !important; }
  .radar { padding: 0.85rem 0.9rem; }
  .chip { flex: 1 1 calc(50% - 0.3rem); max-width: none; min-width: 0; padding: 0.5rem 0.6rem; }
  .chip-group { font-size: 0.85rem; }
  .chip-count { font-size: 1.1rem; }
  .chip-names { font-size: 0.72rem; }
  [data-testid="stMainBlockContainer"] { padding-top: 2.5rem; }
  [data-testid="stMetricValue"] { font-size: 1.35rem !important; }
}
.idx-row { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 0.6rem; margin: 0 0 0.5rem; }
.idx-badge.neutral { background: #E7EDEF; color: #51616C; }
.idx-row .idx-val { font-size: 1.15rem; }
.idx-row .idx-chg { display: block; margin-left: 0; }
.flow { background: #FFFFFF; border: 1px solid #DCE3E7; border-radius: 12px; padding: 0.7rem 0.9rem; margin: 0 0 0.5rem; }
.flow-title { font-size: 0.85rem; color: #51616C; margin-bottom: 0.35rem; }
.flow table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
.flow th { font-size: 0.78rem; color: #8A979F; font-weight: 600; text-align: right; padding: 0.15rem 0.3rem; }
.flow th:first-child, .flow td:first-child { text-align: left; }
.flow td { font-size: 0.92rem; font-weight: 700; text-align: right; padding: 0.25rem 0.3rem; border-top: 1px solid #EEF2F4; }
.flow td small { display: block; font-size: 0.7rem; font-weight: 500; color: #8A979F; }
.idx { background: #FFFFFF; border: 1px solid #DCE3E7; border-radius: 12px; padding: 0.7rem 0.9rem; }
.idx-name { font-size: 0.85rem; color: #51616C; }
.idx-name small { margin-left: 0.3rem; color: #8A979F; }
.idx-val { font-size: 1.35rem; font-weight: 800; color: #16212B; font-variant-numeric: tabular-nums; }
.idx-chg { font-size: 0.85rem; font-weight: 700; margin-left: 0.35rem; font-variant-numeric: tabular-nums; }
.idx-badge { display: inline-block; font-size: 0.72rem; font-weight: 700; padding: 0.1rem 0.45rem;
  border-radius: 6px; margin-top: 0.25rem; font-variant-numeric: tabular-nums; }
.idx-badge.on { background: #D9E8E6; color: #2E6B6F; }
.idx-badge.off { background: #F4E1E2; color: #A2343B; }
.idx-err { font-size: 0.85rem; color: #8A979F; margin-top: 0.3rem; }
@media (max-width: 640px) {
  .idx-row { gap: 0.35rem; grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .flow { padding: 0.55rem 0.6rem; }
  .flow td { font-size: 0.8rem; }
  .idx { padding: 0.5rem 0.55rem; }
  .idx-name { font-size: 0.74rem; }
  .idx-name small { display: none; }
  .idx-val { font-size: 1.0rem; }
  .idx-chg { display: block; margin-left: 0; font-size: 0.76rem; }
  .idx-badge { font-size: 0.62rem; padding: 0.05rem 0.3rem; }
}
.mk-row { display: grid; grid-template-columns: 1fr 1fr 1.05fr; gap: 0; background: #FFFFFF;
  border: 1px solid #E3E8EB; border-radius: 14px; overflow: hidden; margin: 0 0 0.6rem; }
.mk { padding: 0.95rem 1.2rem 0.85rem; border-right: 1px solid #EEF1F3; }
.mk-name { font-size: 0.98rem; font-weight: 700; color: #16212B; display: flex; justify-content: space-between; align-items: center; }
.mk-name small { font-size: 0.72rem; font-weight: 500; color: #8A979F; }
.mk-val { font-size: 1.55rem; font-weight: 800; color: #16212B; font-variant-numeric: tabular-nums; letter-spacing: -0.02em; }
.mk-chg { font-size: 0.95rem; font-weight: 700; margin-left: 0.45rem; font-variant-numeric: tabular-nums; }
.mk-bar { display: flex; height: 5px; border-radius: 3px; overflow: hidden; margin: 0.65rem 0 0.35rem; background: #E3E9EC; }
.mk-bar i { display: block; height: 100%; }
.mk-cnt { display: flex; justify-content: space-between; font-size: 0.8rem; font-variant-numeric: tabular-nums; color: #6B7A84; }
.mk-cnt .u { color: #D6333B; } .mk-cnt .d { color: #1F66C9; }
.mk-sub { display: flex; justify-content: space-between; gap: 0.4rem; margin-top: 0.45rem; font-size: 0.74rem; color: #8A979F;
  font-variant-numeric: tabular-nums; }
.mk-sub b { font-weight: 700; }
.mood { background: #FFF6EC; padding: 0.9rem 1.1rem 0.8rem; }
.mood-top { display: flex; justify-content: space-between; align-items: center; font-weight: 700; color: #16212B; font-size: 0.95rem; }
.mood-top .dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; background: #F29A18; margin-right: 0.35rem; }
.mood-top .lbl { color: #E0781A; font-weight: 800; }
.mood-gauge { position: relative; height: 4px; border-radius: 2px; margin: 0.6rem 0 0.7rem;
  background: linear-gradient(90deg, #1F66C9, #B9C4CC 50%, #D6333B); }
.mood-gauge i { position: absolute; top: -4px; width: 12px; height: 12px; margin-left: -6px; border-radius: 50%;
  background: #FFFFFF; border: 2px solid #F29A18; }
.mood-row { display: grid; grid-template-columns: 3.2rem 1fr 5.4rem; align-items: center; gap: 0.5rem;
  font-size: 0.8rem; color: #51616C; margin-top: 0.3rem; }
.mood-row .bar { position: relative; height: 5px; background: #ECE4DA; border-radius: 3px; }
.mood-row .bar i { position: absolute; top: 0; height: 100%; border-radius: 3px; }
.mood-row b { text-align: right; font-variant-numeric: tabular-nums; }
.mood-note { font-size: 0.68rem; color: #9A8F84; margin-top: 0.45rem; }
.inst { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; font-size: 0.85rem; }
.inst th { color: #8A979F; font-weight: 600; text-align: right; padding: 0.2rem 0.35rem; font-size: 0.76rem; white-space: nowrap; }
.inst td { text-align: right; padding: 0.25rem 0.35rem; border-top: 1px solid #EEF2F4; font-weight: 600; white-space: nowrap; }
.inst th:first-child, .inst td:first-child { text-align: left; }
.c-flow { font-size: 0.74rem; color: #51616C; margin-top: 0.2rem; font-variant-numeric: tabular-nums; }
.notes { margin: 0.2rem 0 0; padding-left: 1.2rem; font-size: 0.88rem; line-height: 1.55; color: #26343E; }
.notes li { margin: 0.12rem 0; }
@media (max-width: 640px) {
  .mk-row { grid-template-columns: 1fr 1fr; }
  .mood { grid-column: 1 / -1; border-top: 1px solid #F1E6D8; }
  .mk { padding: 0.7rem 0.75rem 0.65rem; }
  .mk-val { font-size: 1.2rem; }
  .mk-chg { display: block; margin-left: 0; font-size: 0.8rem; }
  .mk-sub { flex-direction: column; gap: 0.05rem; }
}
</style>
""",
    unsafe_allow_html=True,
)


@st.cache_data(ttl=1800, show_spinner="2년치 일봉을 불러오는 중이에요. 처음 한 번만 몇 초 걸려요.")
def load_histories(codes: tuple[str, ...]):
    return data.fetch_histories(codes)


@st.cache_data(ttl=300, show_spinner="해외 종목 일봉을 불러오는 중이에요.")
def load_histories_overseas(codes: tuple[str, ...]):
    return data.fetch_histories(codes, workers=4)


@st.cache_resource
def _shares_store():
    return {"t": 0.0, "t_retry": 0.0, "data": {}, "kr": {}, "os": {}, "fails": {}}


def load_shares() -> dict:
    """상장주식수: 12시간마다 전체를 다시 받고, 빠진 종목은 10분마다 그것만 다시 시도해요(종목당 3번까지)."""
    store = _shares_store()
    store.setdefault("fails", {})
    store.setdefault("t_retry", 0.0)
    now = time.time()
    full = now - store["t"] >= 43200
    missing = [c for c in ALL_CODES if c not in store["data"] and store["fails"].get(c, 0) < 3]
    if not full and not (missing and now - store["t_retry"] >= 600):
        return store
    kr_t = KR_CODES if full else tuple(c for c in missing if data.is_kr(c))
    os_t = OS_CODES if full else tuple(c for c in missing if not data.is_kr(c))
    with st.spinner("시가총액 계산용 상장주식수를 불러오는 중이에요."):
        kr, kr_diag = data.fetch_kr_shares(kr_t) if kr_t else ({}, {})
        os_, os_diag = data.fetch_overseas_shares(os_t) if os_t else ({}, {})
    got = {**kr, **os_}
    if full:
        store["fails"] = {}
    for c in kr_t + os_t:
        if c not in got:
            store["fails"][c] = store["fails"].get(c, 0) + 1
    store["data"] = {**store["data"], **got}
    if kr_t:
        store["kr"] = kr_diag
    if os_t:
        store["os"] = os_diag
    store["t_retry"] = now
    if full:
        store["t"] = now if len(kr) >= 0.8 * max(1, len(KR_CODES)) else now - 43200 + 600
    return store


@st.cache_data(ttl=3600, show_spinner=False)
def load_fx():
    return data.fetch_fx()


@st.cache_data(ttl=120, show_spinner=False)
def load_indexes():
    return data.fetch_index_histories()


@st.cache_data(ttl=30, show_spinner=False)
def load_overview():
    return data.fetch_market_overview()


@st.cache_data(ttl=300, show_spinner=False)
def load_flows():
    return data.fetch_investor_flows(load_overview())


def load_trends(codes: tuple[str, ...]) -> dict:
    """종목별 수급. data 쪽에서 종목마다 10분씩 기억해서 자동 새로고침 때는 거의 바로 나와요."""
    with st.spinner("종목별 개인·외국인·기관 수급을 불러오는 중이에요."):
        return data.fetch_stock_trends(codes)


def price_chart(chart: pd.DataFrame, colors: list[str], height: int = 260):
    """y축을 0부터 시작하지 않는 선 차트(주가·지수용)."""
    d = chart.copy()
    d.index.name = "날짜"
    long = d.reset_index().melt("날짜", var_name="구분", value_name="값").dropna()
    c = (
        alt.Chart(long)
        .mark_line(strokeWidth=1.7)
        .encode(
            x=alt.X("날짜:T", title=None, axis=alt.Axis(format="%y.%m", labelAngle=0, grid=False)),
            y=alt.Y("값:Q", title=None, scale=alt.Scale(zero=False)),
            color=alt.Color("구분:N", scale=alt.Scale(domain=list(chart.columns), range=colors),
                            legend=alt.Legend(orient="bottom", title=None)),
            tooltip=[alt.Tooltip("날짜:T", format="%Y-%m-%d"), alt.Tooltip("구분:N"),
                     alt.Tooltip("값:Q", format=",.2f")],
        )
        .properties(height=height, width="container")
    )
    st.altair_chart(c)


@st.cache_data(ttl=10, show_spinner=False)
def load_quotes(codes: tuple[str, ...]):
    return data.fetch_quotes(codes)


# ─────────────────────────── 사이드바 ───────────────────────────
with st.sidebar:
    st.subheader("보기 설정")
    sectors = st.multiselect("산업", SECTOR_ORDER, default=["반도체"], placeholder="전체")
    active_sectors = sectors or SECTOR_ORDER
    group_choices = [g for g in GROUP_ORDER
                     if any(s["group"] == g and s["sector"] in active_sectors for s in STOCKS)]
    groups = st.multiselect("세부 분류", group_choices, placeholder="전체")
    tags = st.multiselect("리포트 태그", list(TAGS), placeholder="선택 안 함",
                          help="태그를 고르면 위의 산업·분류와 관계없이 전체 종목에서 그 리포트에 나온 종목만 보여줘요.")
    query = st.text_input("종목 검색", placeholder="이름이나 코드")
    max_drop = st.slider("52주 최고가에서 몇 % 이내만 볼까요", 0, 90, 90, step=5,
                         help="10으로 두면 최고가 대비 -10% 이내 종목만 보여줘요. 90이면 전체.")
    only_aligned = st.toggle("정배열 종목만", help="현재가 > 20일선 > 60일선 > 120일선")
    only_holding = st.toggle("신고가 돌파 유지 종목만",
                             help="52주 신고가를 처음 쓴 뒤 종가가 한 번도 기준가 아래로 내려가지 않은 종목만")
    min_rs = st.slider("종합 RS 이상", 0, 99, 0, step=5, help="0이면 전체. 70으로 두면 RS 70 이상만")
    only_earn = st.toggle("영업이익·EPS 정배열만",
                          help="최근 실적 연도부터 컨센서스(E)까지 영업이익과 EPS가 해마다 모두 증가한 국내 종목만. "
                               "전망(E)이 없는 종목·적자 종목·해외 종목은 빠져요. 처음 켤 때 종목 수에 따라 30초~1분 걸려요.")
    earn_years = st.radio("실적 정배열 판정: 최근 실적 몇 년부터", [2, 3], index=1, horizontal=True,
                          format_func=lambda n: f"{n}년 + 전망(E)")
    show_flow = st.toggle("종목별 수급 열 보기", value=True,
                          help="국내 종목의 최근 거래일 개인·외국인·기관 순매수(억원, 종가로 환산한 추정치)와 5일 누적을 표에 붙여요. "
                               "보고 있는 종목 중 앞쪽 150개까지만 불러와요.")

    st.divider()
    st.caption("주도섹터 기준")
    recent_days = st.slider("신고가로 인정할 기간(거래일)", 1, 20, 5)
    min_count = st.slider("분류 안 신고가 종목 수", 2, 5, 2)

    st.divider()
    st.caption("신고가 돌파 유지 기준")
    bo_mode = st.radio("유지 판정 기준가", list(data.BO_MODES), format_func=data.BO_MODES.get, index=0,
                       help="돌파선: 첫 신고가일에 넘어선 직전 52주 최고가. 첫 신고가일 종가: 그날 종가. "
                            "종가가 이 가격 아래로 한 번이라도 내려가면 '이탈'이에요.")

    st.divider()
    refresh_label = st.radio("자동 새로고침", list(REFRESH), index=1, horizontal=True)
    if st.button("시세 지금 새로고침"):
        load_quotes.clear()
    if st.button("일봉까지 다시 받기", help="52주 최고가가 이상해 보일 때 눌러요."):
        load_histories.clear()
        load_histories_overseas.clear()
        _shares_store().update(t=0.0, ok=False)
        load_quotes.clear()


# ─────────────────────────── 화면 조각 ───────────────────────────
def apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    if tags:
        f = df[df["tags"].apply(lambda ts: any(t in ts for t in tags))]
    else:
        f = df[df["sector"].isin(active_sectors)]
        if groups:
            f = f[f["group"].isin(groups)]
    if query.strip():
        q = query.strip().lower()
        f = f[f["name"].str.lower().str.contains(q, regex=False) | f["code"].str.contains(q, regex=False)]
    if max_drop < 90:
        f = f[f["gap"].notna() & (f["gap"] >= -max_drop)]
    if only_aligned:
        f = f[f["aligned"] == True]  # noqa: E712
    if only_holding:
        f = f[f["bo_status"] == "유지"]
    if min_rs > 0:
        f = f[f["rs"].notna() & (f["rs"] >= min_rs)]
    return f.sort_values("gap", ascending=False, na_position="last")


def _sign_color(v) -> str:
    if v is None or pd.isna(v) or v == 0:
        return "#51616C"
    return UP if v > 0 else DOWN


def _flow_cell(v, digits: int = 0) -> str:
    if v is None or pd.isna(v):
        return "-"
    return f'<span style="color:{_sign_color(v)}">{v:+,.{digits}f}</span>'


def _eok(v) -> str:
    """억원 값 → '+5,473억' / '-1.2조'."""
    if v is None or pd.isna(v):
        return "-"
    return f"{v / 1e4:+,.2f}조" if abs(v) >= 1e4 else f"{v:+,.0f}억"


def _market_card(name: str, sm: dict | None, hist_sm: dict | None) -> str:
    """네이버 증권 첫 화면처럼: 지수, 등락률·전일대비, 상승/보합/하락 막대, 투자자별 순매수."""
    sm = sm or {}
    last = sm.get("last") if sm.get("last") is not None else (hist_sm or {}).get("last")
    if last is None:
        return f'<div class="mk"><div class="mk-name">{name}</div><div class="idx-err">불러오지 못했어요</div></div>'
    rate = sm.get("rate") if sm.get("rate") is not None else (hist_sm or {}).get("change")
    diff = sm.get("diff")
    if diff is None and hist_sm and hist_sm.get("change") is not None:
        diff = last - last / (1 + hist_sm["change"] / 100)
    color = _sign_color(rate)
    arrow = "▲" if (diff or 0) > 0 else ("▼" if (diff or 0) < 0 else "")
    chg = (f'<span class="mk-chg" style="color:{color}">{rate:+.2f}% '
           f'<span style="font-weight:600">{arrow}{abs(diff):,.2f}</span></span>') if rate is not None and diff is not None else ""
    badge = ""
    if hist_sm and hist_sm.get("above60") is not None:
        cls, word = ("on", "위") if hist_sm["above60"] else ("off", "아래")
        badge = f'<small><span class="idx-badge {cls}" style="margin:0">60일선 {word} {hist_sm["dist60"]:+.1f}%</span></small>'
    html_ = f'<div class="mk"><div class="mk-name">{name}{badge}</div><span class="mk-val">{last:,.2f}</span>{chg}'
    b = sm.get("breadth")
    if b:
        up = (b.get("상승") or 0) + (b.get("상한") or 0)
        flat = b.get("보합") or 0
        down = (b.get("하락") or 0) + (b.get("하한") or 0)
        tot = max(1.0, up + flat + down)
        html_ += (f'<div class="mk-bar"><i style="width:{up / tot * 100:.1f}%;background:#EF7A80"></i>'
                  f'<i style="width:{flat / tot * 100:.1f}%;background:#C9D1D6"></i>'
                  f'<i style="width:{down / tot * 100:.1f}%;background:#6FA0E6"></i></div>'
                  f'<div class="mk-cnt"><span class="u">↗{up:,.0f}</span><span>{flat:,.0f}</span>'
                  f'<span class="d">↘{down:,.0f}</span></div>')
    d = sm.get("deal")
    if d:
        cells = "".join(f'<span>{k} <b style="color:{_sign_color(d.get(k))}">{_eok(d.get(k))}</b></span>'
                        for k in ("외국인", "기관", "개인"))
        html_ += f'<div class="mk-sub">{cells}</div>'
    return html_ + "</div>"


def _mood_card(overview: dict) -> str:
    mood = data.market_mood(overview)
    label, ratio = mood if mood else ("-", 0.5)
    tot = {k: 0.0 for k in data.INVESTORS}
    got = False
    for sm in overview.values():
        d = (sm or {}).get("deal") or {}
        for k in data.INVESTORS:
            if d.get(k) is not None:
                tot[k] += d[k]
                got = True
    rows = ""
    if got:
        mx = max(1.0, max(abs(v) for v in tot.values()))
        for k in ("외국인", "기관", "개인"):
            v = tot[k]
            w = abs(v) / mx * 50
            pos = f"left:50%;width:{w:.1f}%" if v >= 0 else f"left:{50 - w:.1f}%;width:{w:.1f}%"
            rows += (f'<div class="mood-row"><span>{k}</span><span class="bar"><i style="{pos};background:{_sign_color(v)}"></i></span>'
                     f'<b style="color:{_sign_color(v)}">{_eok(v)}</b></div>')
    else:
        rows = '<div class="mood-note">투자자별 순매수를 불러오지 못했어요.</div>'
    return (f'<div class="mood"><div class="mood-top"><span><span class="dot"></span>오늘의 시장</span>'
            f'<span class="lbl">{label}</span></div>'
            f'<div class="mood-gauge"><i style="left:{ratio * 100:.0f}%"></i></div>{rows}'
            f'<div class="mood-note">분위기는 코스피·코스닥 상승 종목 비율({ratio * 100:.0f}%) 기준, 순매수는 두 시장 합계예요.</div></div>')


def render_flows():
    """시장별 개인·외국인·기관 순매수(억원) + 기관 세부(연기금·투신 등)와 20일 추이."""
    flows = load_flows()
    latest = [df["date"].iloc[-1] for df in flows.values() if not df.empty]
    if not latest:
        st.caption("시장 전체 투자자별 일별 추이를 불러오지 못했어요. 잠시 뒤 자동으로 다시 시도해요.")
        return
    with st.expander(f"투자자별 순매수 자세히 — 연기금·투신 등 기관 세부 ({max(latest):%m/%d} 기준, 억원)"):
        cols = data.INVESTORS + data.INST_DETAIL
        head = "".join(f"<th>{c}</th>" for c in cols)
        body = ""
        for market, df in flows.items():
            if df.empty:
                continue
            last = df.iloc[-1]
            cum5 = df.tail(5)[cols].sum(min_count=1)
            body += f"<tr><td>{market} <small>{last['date']:%m/%d}</small></td>" + "".join(
                f"<td>{_flow_cell(last[c])}</td>" for c in cols) + "</tr>"
            body += f"<tr><td style='color:#8A979F'>{market} 5일</td>" + "".join(
                f"<td>{_flow_cell(cum5[c])}</td>" for c in cols) + "</tr>"
        st.markdown(f'<div style="overflow-x:auto"><table class="inst"><tr><th></th>{head}</tr>{body}</table></div>',
                    unsafe_allow_html=True)
        tabs = st.tabs(list(flows))
        for tab, (market, df) in zip(tabs, flows.items()):
            with tab:
                if df.empty:
                    st.caption("데이터를 불러오지 못했어요.")
                    continue
                pick = st.radio("보기", ["개인·외국인·기관", "기관 세부"], horizontal=True, key=f"flowpick_{market}",
                                label_visibility="collapsed")
                series = data.INVESTORS if pick == "개인·외국인·기관" else data.INST_DETAIL
                sub = df.tail(20).dropna(axis=1, how="all")
                series = [c for c in series if c in sub.columns]
                if not series:
                    st.caption("기관 세부는 새 네이버 API에서만 받아와요. 지금은 불러오지 못했어요.")
                    continue
                long = sub.melt("date", value_vars=series, var_name="투자자", value_name="순매수")
                palette = ["#9AA9B3", "#2E6B6F", "#F2B134"] if len(series) == 3 else \
                    ["#2E6B6F", "#7FA7A3", "#F2B134", "#9AA9B3", "#C77D4B", "#D6333B", "#51616C"]
                chart = (
                    alt.Chart(long).mark_bar()
                    .encode(
                        x=alt.X("date:T", title=None, axis=alt.Axis(format="%m/%d", labelAngle=0, grid=False)),
                        xOffset=alt.XOffset("투자자:N", sort=series),
                        y=alt.Y("순매수:Q", title="억원"),
                        color=alt.Color("투자자:N", sort=series, scale=alt.Scale(domain=series, range=palette[:len(series)]),
                                        legend=alt.Legend(orient="bottom", title=None)),
                        tooltip=[alt.Tooltip("date:T", format="%Y-%m-%d"), "투자자:N", alt.Tooltip("순매수:Q", format="+,.0f")],
                    )
                    .properties(height=240, width="container")
                )
                st.altair_chart(chart)
        st.caption("네이버 증권 투자자별 매매동향 기준. 기관 = 금융투자+보험+투신(사모)+은행+기타금융+연기금이고, "
                   "기타법인은 기관에 넣지 않아요. 장중 값은 잠정치예요.")


def render_market():
    """맨 위 시장 요약: 코스피·코스닥 카드 + 오늘의 시장, 그 아래 해외 지수·환율·유가."""
    overview = load_overview()
    hist = load_indexes()
    hist_sm = {idx["name"]: data.index_summary(hist.get(idx["symbol"], (data.empty_frame(), None))[0])
               for idx in data.INDEXES}
    cards = "".join(_market_card(name, overview.get(name), hist_sm.get(name)) for name in data.MARKETS)
    st.markdown(f'<div class="mk-row">{cards}{_mood_card(overview)}</div>', unsafe_allow_html=True)

    chips = []
    for idx in data.INDEXES:
        if idx["name"] in data.MARKETS:
            continue
        sm = hist_sm.get(idx["name"])
        if not sm:
            chips.append(f'<div class="idx"><div class="idx-name">{idx["name"]}</div>'
                         f'<div class="idx-err">불러오지 못했어요</div></div>')
            continue
        color = _sign_color(sm["change"])
        if idx.get("kind", "index") == "index":
            if sm["above60"] is None:
                badge = ""
            elif sm["above60"]:
                badge = f'<span class="idx-badge on">60일선 위 {sm["dist60"]:+.1f}%</span>'
            else:
                badge = f'<span class="idx-badge off">60일선 아래 {sm["dist60"]:+.1f}%</span>'
        else:
            badge = (f'<span class="idx-badge neutral">20일 {sm["chg20"]:+.1f}%</span>'
                     if sm.get("chg20") is not None else "")
        chips.append(
            f'<div class="idx"><div class="idx-name">{idx["name"]}<small>{sm["date"]:%m/%d}</small></div>'
            f'<span class="idx-val">{idx.get("unit", "")}{sm["last"]:,.2f}</span>'
            f'<span class="idx-chg" style="color:{color}">{sm["change"]:+.2f}%</span><br>{badge}</div>'
        )
    st.markdown('<div class="idx-row">' + "".join(chips) + "</div>",
                unsafe_allow_html=True)

    render_flows()

    with st.expander("지수·환율·유가 차트 보기"):
        tabs = st.tabs([i["name"] for i in data.INDEXES])
        for tab, idx in zip(tabs, data.INDEXES):
            with tab:
                df, _err = hist.get(idx["symbol"], (data.empty_frame(), None))
                if df.empty:
                    st.caption("지수 데이터를 불러오지 못했어요. 잠시 뒤 다시 열어 보세요.")
                    continue
                h = df.set_index("date")["close"].astype(float)
                chart = pd.DataFrame({idx["name"]: h, "20일선": h.rolling(20).mean(), "60일선": h.rolling(60).mean()}).tail(180)
                price_chart(chart, ["#16212B", "#2E6B6F", "#F2B134"], height=240)
        st.caption("코스피·코스닥은 네이버 증권, 나머지는 야후 파이낸스(15분 안팎 지연) 일봉이에요. "
                   "유가는 근월물 선물 기준이에요.")


def render_radar(df: pd.DataFrame):
    leaders = data.leading_groups(df, recent_days, min_count)
    if leaders:
        body = "".join(
            f'<div class="chip"><div class="chip-top">'
            f'<span class="chip-group">{html.escape(g)}</span>'
            f'<span class="chip-count">{len(sub)}<small>종목</small></span></div>'
            f'<div class="chip-names">{html.escape(", ".join(sub["name"]))}</div></div>'
            for g, sub in leaders
        )
    else:
        body = ('<div class="radar-empty">지금은 조건에 맞는 분류가 없어요. '
                '왼쪽에서 신고가 인정 기간을 늘리거나 종목 수 기준을 낮춰 보세요.</div>')
    st.markdown(
        f'<div class="radar"><div class="radar-title">주도섹터 레이더</div>'
        f'<div class="radar-rule">최근 {recent_days}거래일 안에 52주 신고가를 쓴 종목이 '
        f'{min_count}개 이상인 분류예요. 전체 종목 기준으로 계산해요.</div>'
        f'<div class="chips">{body}</div></div>',
        unsafe_allow_html=True,
    )


def _eok_num(v) -> str:
    """억원 값: 10억 미만은 소수 첫째 자리까지."""
    if v is None or pd.isna(v):
        return "-"
    return f"{v:+,.1f}" if abs(v) < 10 else f"{v:+,.0f}"


FLOW_HELP = ("네이버 종목별 투자자 매매동향의 순매수 수량 × 그날 종가로 환산한 추정 금액(억원). "
             "최근 거래일 값이고, 장중에는 전 거래일 값일 수 있어요. 5일은 최근 5거래일 합계예요.")


RS_HELP = ("종합 RS(1~99). 오닐 방식 가중 수익률(3개월 40% + 6·9·12개월 각 20%)을 보드 안 국내 종목끼리 순위 매긴 백분위. "
           "해외 종목은 해외 종목끼리. 전 종목 기준 사이트 RS와는 숫자가 다를 수 있어요")


def _bo_text(r) -> str | None:
    if r.get("bo_status") == "유지":
        return f"유지 {int(r['bo_days'])}일"
    if r.get("bo_status") == "이탈":
        return f"이탈 ({int(r['bo_days'])}일 유지)"
    return None


def _rs_color(v):
    if pd.isna(v):
        return ""
    if v >= 80:
        return f"color: {UP}; font-weight: 600"
    if v < 40:
        return f"color: {DOWN}"
    return ""


def render_table(f: pd.DataFrame):
    view = pd.DataFrame({
        "종목": f["name"],
        "코드": f["code"],
        "시장": f["market"],
        "분류": f["group"],
        "통화": f["currency"].map(lambda c: UNIT.get(c, c)),
        "현재가": f["price"],
        "시가총액(원)": f["cap_krw"],
        "등락률": f["change"],
        **({"외국인(억원)": f["flow_외국인"], "기관(억원)": f["flow_기관"], "개인(억원)": f["flow_개인"],
            "외국인 5일(억원)": f["flow5_외국인"], "기관 5일(억원)": f["flow5_기관"],
            "개인 5일(억원)": f["flow5_개인"]} if show_flow else {}),
        "52주 최고": f["high52"],
        "괴리율": f["gap"],
        "신고가까지": f["to_high"],
        "52주 위치": f["pos"],
        "신고가 후": f["days_since_high"],
        "돌파 유지": f.apply(_bo_text, axis=1),
        "기준가 대비": f["bo_vs"].where(f["bo_status"] == "유지"),
        "RS": f["rs"],
        "RS(1M)": f["rs_1m"],
        "RS(3M)": f["rs_3m"],
        "RS(6M)": f["rs_6m"],
        "ATR%(2주)": f["atr_pct"],
        "정배열": f["aligned"],
        "설명": f["desc"],
    })

    whole_rows = view.index[f["currency"].isin(["KRW", "JPY"]).values]
    flow_cols = [c for c in view.columns if c.endswith("(억원)")]
    for c in flow_cols:
        view[c] = pd.to_numeric(view[c], errors="coerce")

    def color_sign(v):
        if pd.isna(v) or v == 0:
            return ""
        return f"color: {UP}; font-weight: 600" if v > 0 else f"color: {DOWN}; font-weight: 600"

    def mark_new_high(row):
        hit = pd.notna(row["신고가 후"]) and row["신고가 후"] <= recent_days
        return [f"background-color: {NEW_HIGH_BG}" if hit else ""] * len(row)

    styled = (
        view.style
        .format({
            "현재가": "{:,.2f}", "52주 최고": "{:,.2f}",
            "등락률": "{:+.2f}%", "괴리율": "{:.1f}%", "신고가까지": "{:+.1f}%",
            "시가총액(원)": data.format_krw,
            "신고가 후": "{:.0f}일", "52주 위치": "{:.0f}",
            "기준가 대비": "{:+.1f}%", "RS": "{:.0f}", "RS(1M)": "{:.0f}", "RS(3M)": "{:.0f}", "RS(6M)": "{:.0f}",
            "ATR%(2주)": "{:.1f}%",
        }, na_rep="-")
        .format("{:,.0f}", subset=pd.IndexSlice[whole_rows, ["현재가", "52주 최고"]], na_rep="-")
        .format(_eok_num, subset=flow_cols, na_rep="-")
        .map(color_sign, subset=["등락률"] + flow_cols)
        .map(lambda _: "font-weight: 600", subset=["종목", "RS"])
        .map(_rs_color, subset=["RS", "RS(1M)", "RS(3M)", "RS(6M)"])
        .map(lambda v: f"color: {UP}; font-weight: 600" if isinstance(v, str) and v.startswith("유지")
             else (f"color: {DOWN}" if isinstance(v, str) and v.startswith("이탈") else ""), subset=["돌파 유지"])
        .apply(mark_new_high, axis=1)
    )
    st.dataframe(
        styled,
        hide_index=True,
        height=min(720, 36 * (len(view) + 1) + 4),
        column_config={
            "52주 위치": st.column_config.ProgressColumn(
                "52주 위치", min_value=0, max_value=100, format="%.0f",
                help="52주 최저가를 0, 최고가를 100으로 봤을 때 현재가 위치"),
            "괴리율": st.column_config.Column(help="현재가가 52주 최고가보다 몇 % 낮은지"),
            "신고가까지": st.column_config.Column(help="52주 최고가를 넘으려면 필요한 상승률"),
            "신고가 후": st.column_config.Column(help="52주 최고가를 찍은 뒤 지난 거래일 수. 0이면 오늘"),
            "시가총액(원)": st.column_config.Column(help="상장주식수 × 현재가, 원화 기준(조·억). 해외 종목은 환율로 환산. "
                                                           "상장주식수를 못 받은 종목은 '-'이고, 10분마다 다시 시도해요."),
            "통화": st.column_config.Column(help="현재가·52주 최고가의 단위. 국내는 원, 해외는 각 시장 통화"),
            "현재가": st.column_config.Column(help="통화 열의 단위예요"),
            "정배열": st.column_config.CheckboxColumn(help="현재가 > 20일선 > 60일선 > 120일선"),
            "돌파 유지": st.column_config.Column(help="52주 신고가를 처음 쓴 뒤 종가가 기준가 위에 머문 거래일 수. "
                                                  "한 번이라도 종가가 기준가 아래면 '이탈'(유지 일수는 이탈 전까지)"),
            "기준가 대비": st.column_config.Column(help="유지 중인 종목의 현재가가 돌파 기준가보다 몇 % 위인지"),
            "RS": st.column_config.Column(help=RS_HELP),
            "RS(1M)": st.column_config.Column(help="단기 RS. 최근 1개월(21거래일) 수익률 순위(1~99)"),
            "RS(3M)": st.column_config.Column(help="최근 3개월(63거래일) 수익률 순위(1~99)"),
            "RS(6M)": st.column_config.Column(help="최근 6개월(126거래일) 수익률 순위(1~99)"),
            "ATR%(2주)": st.column_config.Column(help="최근 10거래일 평균 진폭 ÷ 현재가. 손절폭 잡을 때 참고(8%보다 크면 ATR로 확대)"),
            "설명": st.column_config.TextColumn(width="large"),
            **{c: st.column_config.Column(help=FLOW_HELP) for c in flow_cols},
        },
    )


def _n(v) -> str:
    return "-" if pd.isna(v) else f"{v:.0f}"


def _pct(v) -> str:
    return "-" if pd.isna(v) else f"{v:.1f}%"


def render_cards(f: pd.DataFrame, n_hot: int, n_near: int, n_aligned: int):
    """휴대폰 화면용: 표 대신 카드 목록. CSS가 좁은 화면에서만 보여줘요."""
    kpis = (
        f'<div class="m-kpis"><div><span>종목</span><b>{len(f)}</b></div>'
        f'<div><span>최근 {recent_days}일 신고가</span><b>{n_hot}</b></div>'
        f'<div><span>10% 이내</span><b>{n_near}</b></div>'
        f'<div><span>정배열</span><b>{n_aligned}</b></div></div>'
    )
    cards = []
    for r in f[f["price"].notna()].itertuples():
        hot = pd.notna(r.days_since_high) and r.days_since_high <= recent_days
        if pd.isna(r.change) or r.change == 0:
            chg = '<span class="c-chg">-</span>' if pd.isna(r.change) else '<span class="c-chg">0.00%</span>'
        else:
            color = UP if r.change > 0 else DOWN
            chg = f'<span class="c-chg" style="color:{color}">{r.change:+.2f}%</span>'
        tags = ""
        if hot:
            tags += f'<span class="c-tag new">신고가 {int(r.days_since_high)}일</span>'
        if r.aligned:
            tags += '<span class="c-tag al">정배열</span>'
        if r.bo_status == "유지":
            tags += f'<span class="c-tag new">돌파유지 {int(r.bo_days)}일</span>'
        cards.append(
            f'<a class="card{" hot" if hot else ""}" target="_blank" '
            f'href="{r.url}">'
            f'<div class="c-top"><span class="c-name">{html.escape(r.name)}{tags}</span>'
            f'<span class="c-price">{fmt_price(r.price, r.currency)}'
            f'{"" if r.currency == "KRW" else " " + r.currency}</span></div>'
            f'<div class="c-mid"><span>{html.escape(r.group)}</span>{chg}</div>'
            f'<div class="c-bar"><i style="width:{max(2.0, min(100.0, r.pos)):.0f}%"></i></div>'
            f'<div class="c-meta"><span>52주 최고 {fmt_price(r.high52, r.currency)} ({r.gap:.1f}%)</span>'
            f'<span>신고가까지 <b>{r.to_high:+.1f}%</b></span></div>'
            f'<div class="c-meta" style="margin-top:0.15rem"><span>시가총액 <b>{data.format_krw(r.cap_krw)}{"원" if pd.notna(r.cap_krw) else ""}</b></span></div>'
            f'<div class="c-meta" style="margin-top:0.15rem"><span>RS <b>{_n(r.rs)}</b> · 1M {_n(r.rs_1m)}</span>'
            f'<span>ATR(2주) <b>{_pct(r.atr_pct)}</b></span></div>'
            + (f'<div class="c-flow">수급 {r.flow_date:%m/%d} (억원) · 외 {_flow_cell(r.flow_외국인)} · '
               f'기 {_flow_cell(r.flow_기관)} · 개 {_flow_cell(r.flow_개인)}'
               f'<br>5일 누적 · 외 {_flow_cell(r.flow5_외국인)} · 기 {_flow_cell(r.flow5_기관)} · 개 {_flow_cell(r.flow5_개인)}</div>'
               if show_flow and pd.notna(getattr(r, "flow_date", None)) else "")
            + f'<div class="c-desc">{html.escape(r.desc)}</div></a>'
        )
    st.markdown(kpis + '<div class="cards">' + "".join(cards) + "</div>", unsafe_allow_html=True)
    st.caption("카드를 누르면 종목 화면이 열려요. 국내는 네이버 증권, 해외는 야후 파이낸스예요.")


def render_stock_flow(code: str, trends: dict):
    """종목 자세히 보기의 수급 탭: 최근 20거래일 개인·외국인·기관 순매수(억원 추정)와 외국인 보유율."""
    if not data.is_kr(code):
        st.caption("해외 종목은 투자자별 매매동향을 제공하지 않아요.")
        return
    tr = trends.get(code)
    if tr is None:
        tr = data.fetch_stock_trend(code)
    if tr is None or tr.empty:
        st.caption("이 종목의 투자자별 매매동향을 불러오지 못했어요. 잠시 뒤 다시 열어 보세요.")
        return
    sm = data.trend_summary(tr)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(f"외국인 ({sm['flow_date']:%m/%d})", _eok(sm["flow_외국인"]), f"5일 {_eok(sm['flow5_외국인'])}", delta_color="off")
    c2.metric(f"기관 ({sm['flow_date']:%m/%d})", _eok(sm["flow_기관"]), f"5일 {_eok(sm['flow5_기관'])}", delta_color="off")
    c3.metric(f"개인 ({sm['flow_date']:%m/%d})", _eok(sm["flow_개인"]), f"5일 {_eok(sm['flow5_개인'])}", delta_color="off")
    streak = []
    for k in ("외국인", "기관"):
        n = sm.get(f"streak_{k}") or 0
        if n:
            streak.append(f"{k} {abs(n)}일 연속 순{'매수' if n > 0 else '매도'}")
    hold = tr["외국인보유율"].dropna()
    c4.metric("외국인 보유율", f"{hold.iloc[-1]:.2f}%" if not hold.empty else "-",
              " · ".join(streak) if streak else None, delta_color="off")

    long = tr.melt("date", value_vars=[f"{k}금액" for k in data.INVESTORS], var_name="투자자", value_name="순매수")
    long["투자자"] = long["투자자"].str.replace("금액", "", regex=False)
    chart = (
        alt.Chart(long.dropna()).mark_bar()
        .encode(
            x=alt.X("date:T", title=None, axis=alt.Axis(format="%m/%d", labelAngle=0, grid=False)),
            xOffset=alt.XOffset("투자자:N", sort=data.INVESTORS),
            y=alt.Y("순매수:Q", title="억원(추정)"),
            color=alt.Color("투자자:N", sort=data.INVESTORS,
                            scale=alt.Scale(domain=data.INVESTORS, range=["#9AA9B3", "#2E6B6F", "#F2B134"]),
                            legend=alt.Legend(orient="bottom", title=None)),
            tooltip=[alt.Tooltip("date:T", format="%Y-%m-%d"), "투자자:N", alt.Tooltip("순매수:Q", format="+,.1f")],
        )
        .properties(height=240, width="container")
    )
    st.altair_chart(chart)
    table = tr.sort_values("date", ascending=False).head(10)
    view = pd.DataFrame({
        "날짜": table["date"].dt.strftime("%m/%d"),
        "종가": table["close"],
        "외국인(주)": table["외국인"], "기관(주)": table["기관"], "개인(주)": table["개인"],
        "외국인(억)": table["외국인금액"], "기관(억)": table["기관금액"], "개인(억)": table["개인금액"],
        "외국인 보유율": table["외국인보유율"],
    })
    num = [c for c in view.columns if c not in ("날짜",)]
    for c in num:
        view[c] = pd.to_numeric(view[c], errors="coerce")
    sign_cols = ["외국인(주)", "기관(주)", "개인(주)", "외국인(억)", "기관(억)", "개인(억)"]
    st.dataframe(
        view.style.format({"종가": "{:,.0f}", "외국인 보유율": "{:.2f}%", **{c: "{:+,.0f}" for c in sign_cols[:3]},
                           **{c: "{:+,.1f}" for c in sign_cols[3:]}}, na_rep="-")
        .map(lambda v: "" if pd.isna(v) or v == 0 else f"color: {UP if v > 0 else DOWN}", subset=sign_cols),
        hide_index=True,
    )
    st.caption("네이버 증권 종목별 투자자 매매동향 기준. 금액은 순매수 수량 × 그날 종가로 환산한 추정치예요. "
               "연기금·투신 같은 기관 세부는 종목별로는 네이버가 주지 않고(한국거래소는 로그인 필요), "
               "시장 전체 기준으로 위쪽 '투자자별 순매수 자세히'에 보여줘요.")


def render_detail(f: pd.DataFrame, histories: dict, trends: dict):
    options = f[f["price"].notna()]
    if options.empty:
        return
    st.subheader("종목 자세히 보기")
    labels = {r.code: f"{r.name} ({r.code})" for r in options.itertuples()}
    code = st.selectbox("종목", list(labels), format_func=labels.get, label_visibility="collapsed",
                        key="detail_code")
    row = options[options["code"] == code].iloc[0]

    c1, c2, c3, c4 = st.columns(4)
    unit = UNIT.get(row.currency, row.currency)
    c1.metric("현재가", f"{fmt_price(row.price, row.currency)}{unit}",
              f"{row.change:+.2f}%" if pd.notna(row.change) else None, delta_color="off")
    c2.metric("52주 최고", f"{fmt_price(row.high52, row.currency)}{unit}", f"{row.gap:.1f}%", delta_color="off")
    c3.metric("신고가까지", f"{row.to_high:+.1f}%",
              "오늘 신고가" if row.days_since_high == 0 else f"고점 후 {row.days_since_high:.0f}거래일",
              delta_color="off")
    c4.metric("52주 최저", f"{fmt_price(row.low52, row.currency)}{unit}")

    d1, d2, d3, d4 = st.columns(4)
    d1.metric("종합 RS", _n(row.rs), f"3M {_n(row.rs_3m)} · 6M {_n(row.rs_6m)}", delta_color="off", help=RS_HELP)
    d2.metric("단기 RS(1M)", _n(row.rs_1m),
              f"1개월 {row.ret_1m:+.1f}%" if pd.notna(row.ret_1m) else None, delta_color="off")
    d3.metric("ATR%(2주)", _pct(row.atr_pct), "손절폭은 8%와 ATR 중 큰 값", delta_color="off")
    if isinstance(row.bo_status, str):
        d4.metric("신고가 돌파 유지", f"{'유지' if row.bo_status == '유지' else '이탈'} {int(row.bo_days)}일",
                  f"첫 신고가 {row.bo_date:%m/%d} · 기준가 {fmt_price(row.bo_level, row.currency)}"
                  + (f" · {row.bo_vs:+.1f}%" if row.bo_status == "유지" else f" · {row.bo_break_date:%m/%d} 이탈"),
                  delta_color="off")
    else:
        d4.metric("신고가 돌파 유지", "-", "최근 1년 안에 52주 신고가 없음", delta_color="off")
    if pd.notna(row.cap_local):
        cap_text = f"시가총액 {data.format_local_cap(row.cap_local, row.currency)}"
        if row.currency != "KRW" and pd.notna(row.cap_krw):
            cap_text += f" (약 {data.format_krw(row.cap_krw)}원)"
        st.markdown(f"**{cap_text}**  \n상장주식수 {row.shares:,.0f}주")
    st.write(f"**{row['group']}**  \n{row.desc}")
    if row.tags:
        st.caption("리포트 태그: " + ", ".join(row.tags))

    notes = row.get("notes") or []
    tab_chart, tab_earn, tab_flow, tab_notes = st.tabs(["차트", "실적(영업이익·EPS)", "수급(개인·외국인·기관)",
                                                        f"자료 메모 {len(notes)}"])
    with tab_chart:
        hist = histories.get(code, (None, data.empty_frame(), None))[1]
        if not hist.empty:
            h = hist.tail(250).set_index("date")
            chart = pd.DataFrame({
                "종가": h["close"],
                "20일선": h["close"].rolling(20).mean(),
                "60일선": h["close"].rolling(60).mean(),
                "52주 최고": row.high52,
            })
            colors = ["#16212B", "#2E6B6F", "#9AA9B3", "#F2B134"]
            if isinstance(row.bo_status, str) and pd.notna(row.bo_level):
                chart["돌파 기준가"] = row.bo_level
                colors.append(UP)
            price_chart(chart, colors, height=280)
            hist_rows = row.get("bo_history")
            hist_rows = hist_rows if isinstance(hist_rows, list) else []
            if hist_rows:
                st.caption(f"52주 신고가 돌파 기록 (기준: {data.BO_MODES[bo_mode]}, 종가가 기준가 아래면 이탈)")
                st.dataframe(pd.DataFrame(hist_rows).style.format({"기준가": "{:,.2f}"}), hide_index=True)
    with tab_earn:
        render_earnings(code, row.currency)
    with tab_flow:
        render_stock_flow(code, trends)
    with tab_notes:
        if notes:
            st.markdown('<ol class="notes">' + "".join(f"<li>{html.escape(n)}</li>" for n in notes) + "</ol>",
                        unsafe_allow_html=True)
            st.caption("엣지방 대화 요약(9/22~24)과 캡처 자료에서 모은 포인트예요. 매수·매도 추천이 아니에요.")
        else:
            st.caption("이 종목은 따로 모아 둔 메모가 없어요. 위 설명이 요약이에요.")
    if data.is_kr(code):
        st.link_button("네이버 증권에서 보기", f"https://m.stock.naver.com/domestic/stock/{code}/total")
    else:
        st.link_button("야후 파이낸스에서 보기", row.url)


def render_checks(df: pd.DataFrame, quote_error: str | None, shares_store: dict | None = None):
    failed = df[df["price"].isna()]
    mismatch = df[df["name_ok"] == False]  # noqa: E712
    cap_missing = int(df["cap_krw"].isna().sum())
    if failed.empty and mismatch.empty and not quote_error and cap_missing == 0 and not PENDING_MISS:
        return
    n_items = len(failed) + len(mismatch) + (1 if cap_missing else 0) + len(PENDING_MISS)
    with st.expander(f"데이터 점검 필요 {n_items}건"):
        for name, cands in PENDING_MISS.items():
            hint = ", ".join(f"{nm} {c}" for c, nm in cands) if cands else "후보 없음"
            st.write(f"코드 못 찾음: {name} — 네이버 검색 후보: {hint}")
        if cap_missing and shares_store:
            miss = df[df["cap_krw"].isna()]
            kr, os_ = shares_store.get("kr", {}), shares_store.get("os", {})
            by_market = miss.groupby("market")["name"].apply(list)
            parts = [f"{m} {len(v)}개: {', '.join(v[:15])}{' 외' if len(v) > 15 else ''}" for m, v in by_market.items()]
            st.write(f"시가총액 없는 종목 {cap_missing}개 — " + " / ".join(parts))
            st.caption(
                f"최근 조회 결과: 국내 {sum(v for k, v in kr.items() if k != 'total')}/{kr.get('total', 0)} "
                f"(상세API {kr.get('상세API', 0)}, 순위표 {kr.get('순위표', 0)}, 종목페이지 {kr.get('종목페이지', 0)}, 모바일 {kr.get('모바일', 0)}), "
                f"해외 {os_.get('SEC', 0) + os_.get('네이버', 0) + os_.get('야후', 0)}/{os_.get('total', 0)} "
                f"(SEC {os_.get('SEC', 0)}, 네이버 {os_.get('네이버', 0)}, 야후 {os_.get('야후', 0)}). "
                "빠진 종목은 10분마다 다시 시도해요(종목당 3번까지, 12시간마다 전체 새로 받기). "
                "대만·유럽·호주 종목은 네이버가 다루지 않고 야후가 막히면 '-'로 남을 수 있어요."
            )
        if quote_error:
            st.write(f"실시간 시세: {quote_error}. 일봉 마지막 값으로 대신 보여주고 있어요.")
        for r in failed.itertuples():
            st.write(f"조회 실패: {r.name} ({r.code}) — {r.error}")
        for r in mismatch.itertuples():
            st.write(f"이름 불일치: 목록에는 {r.name}, 네이버에는 {r.naver_name} ({r.code})")
        st.caption("코드가 틀렸거나 사명이 바뀐 경우예요. verify.bat을 실행하면 올바른 코드를 찾아줘요.")


def load_financials(codes: tuple[str, ...]) -> dict:
    """종목별 연간 영업이익·EPS. data 쪽에서 종목마다 12시간 기억해요."""
    with st.spinner(f"영업이익·EPS 실적과 전망을 불러오는 중이에요({len(codes)}종목, 처음 한 번만 오래 걸려요)."):
        return data.fetch_financials_many(codes)


def add_earnings(f: pd.DataFrame, fins: dict) -> pd.DataFrame:
    res = [data.earnings_trend(fins.get(c), earn_years) for c in f["code"]]
    keys = list(data.earnings_trend(None))
    return f.assign(**{k: [r[k] for r in res] for k in keys}) if len(f) else f.assign(**{k: [] for k in keys})


def render_earnings(code: str, currency: str):
    """종목 자세히 보기: 연간 매출·영업이익·EPS와 정배열 판정."""
    if not data.is_kr(code):
        st.caption("해외 종목은 실적 정배열 판정을 하지 않아요.")
        return
    fin = data.fetch_financials(code)
    if fin is None or fin.empty:
        st.caption("실적 자료를 불러오지 못했어요. 잠시 뒤 다시 열어 보세요.")
        return
    tr = data.earnings_trend(fin, earn_years)
    mark = {True: "✅ 정배열", False: "❌ 아님", None: "판정 불가(전망 없음/자료 부족)"}
    c1, c2, c3 = st.columns(3)
    c1.metric("영업이익·EPS 정배열", mark[tr["earn_ok"]], tr["earn_span"], delta_color="off")
    c2.metric("영업이익", mark[tr["op_ok"]])
    c3.metric("EPS", mark[tr["eps_ok"]])
    view = fin.set_index("period")[[c for c in ("sales", "op", "eps") if c in fin]].T
    view.index = [{"sales": "매출액(억원)", "op": "영업이익(억원)", "eps": "EPS(원)"}[i] for i in view.index]
    st.dataframe(view.style.format("{:,.0f}", na_rep="-"))
    st.caption("네이버 증권 기업실적분석(연결 우선) 기준, (E)는 컨센서스 전망치예요. "
               "네이버는 보통 전망을 1~2년만 줘서 3년 치 전망을 보여주는 사이트와 판정이 다를 수 있어요.")


def render_board():
    histories = {**load_histories(KR_CODES), **load_histories_overseas(OS_CODES)}
    quotes, quote_error = load_quotes(KR_CODES)
    shares_store = load_shares()
    df = data.build_table(STOCKS, histories, quotes, shares_store["data"], load_fx(), bo_mode=bo_mode)
    df["cap_krw"] = pd.to_numeric(df["cap_krw"], errors="coerce")
    df["cap_local"] = pd.to_numeric(df["cap_local"], errors="coerce")

    interval = REFRESH[refresh_label]
    refresh_text = f"{refresh_label}마다 새로 불러와요." if interval else "자동 새로고침은 꺼져 있어요."
    st.markdown(
        f'<div class="status">{data.now_kst():%Y-%m-%d %H:%M:%S} 기준, {data.market_status(quotes)}이에요. '
        f'{refresh_text} 시세 출처는 네이버 금융이에요.</div>',
        unsafe_allow_html=True,
    )
    if data.MOCK:
        st.info("가짜 데이터로 보여주는 테스트 모드예요. 실제 시세를 보려면 STOCK_MOCK 설정 없이 실행하세요.")

    render_market()
    render_radar(df)
    f = apply_filters(df)
    if only_earn:
        fins = load_financials(tuple(c for c in f["code"] if data.is_kr(c)))
        f = add_earnings(f, fins)
        f = f[f["earn_ok"] == True]  # noqa: E712
    trends = load_trends(tuple([c for c in f["code"] if data.is_kr(c)][:150])) if show_flow else {}
    summ = [data.trend_summary(trends.get(c)) for c in f["code"]]
    flow_keys = list(data.trend_summary(None))
    f = f.assign(**{k: [x[k] for x in summ] for k in flow_keys}) if len(f) else f.assign(**{k: [] for k in flow_keys})

    n_hot = int((f["days_since_high"] <= recent_days).sum())
    n_near = int((f["to_high"] <= 10).sum())
    n_aligned = int((f["aligned"] == True).sum())  # noqa: E712
    n_holding = int((f["bo_status"] == "유지").sum())

    with st.container(key="desk_kpis"):
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("보고 있는 종목", f"{len(f)}개")
        c2.metric(f"최근 {recent_days}거래일 신고가", f"{n_hot}개")
        c3.metric("신고가 돌파 유지", f"{n_holding}개")
        c4.metric("신고가까지 10% 이내", f"{n_near}개")
        c5.metric("정배열", f"{n_aligned}개")

    if f.empty:
        st.info("조건에 맞는 종목이 없어요. 왼쪽에서 산업이나 하락폭 조건을 넓혀 보세요.")
    else:
        with st.container(key="desk_table"):
            render_table(f)
            st.caption("노란 줄은 설정한 기간 안에 52주 신고가를 쓴 종목이에요. 해외 종목은 야후 파이낸스 일봉 기준이라 "
                       "15분 안팎 늦고, 가격은 현지 통화예요. 설명은 각 자료 작성 시점 기준 요약이에요. "
                       "단위: 현재가·52주 최고는 통화 열 기준, 시가총액은 원화(조·억), 등락률·괴리율은 %, "
                       "수급 열은 억원(1억 원 = 100,000,000원)으로 순매수 수량 × 종가로 환산한 추정치예요.")
        with st.container(key="mobile_view"):
            render_cards(f, n_hot, n_near, n_aligned)
        render_detail(f, histories, trends)
    render_checks(df, quote_error, shares_store)


st.title("밸류체인 신고가 보드")
st.fragment(render_board, run_every=REFRESH[refresh_label])()
