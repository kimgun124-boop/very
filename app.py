"""밸류체인 신고가 보드.

실행: streamlit run app.py   (윈도우는 run.bat 더블클릭)
"""
from __future__ import annotations

import html
import re
import os

import altair as alt
import pandas as pd
import streamlit as st

import importlib
import threading
import time

import data
import holdings
import reports
import stocks as stock_list

st.set_page_config(page_title="밸류체인 신고가 보드", page_icon="📈", layout="wide")

# 크롬 자동 번역이 화면 글자를 바꿔 끼우면 'removeChild' 오류로 앱이 멈춰요. 한국어 페이지로 알리고 번역을 막아요.
_NO_TRANSLATE_JS = """<script>
try { const d = (window.parent && window.parent.document) || document;
  d.documentElement.setAttribute('lang', 'ko'); d.documentElement.setAttribute('translate', 'no');
  d.documentElement.classList.add('notranslate'); d.body.classList.add('notranslate');
  if (!d.querySelector('meta[name=google][content=notranslate]')) {
    const m = d.createElement('meta'); m.name = 'google'; m.content = 'notranslate'; d.head.appendChild(m); }
} catch (e) {}
</script>"""
try:        # 새 Streamlit: st.html로 바로 실행(components.html은 곧 없어져요)
    with st.container(key="no_translate", height=1, border=False):
        st.html(_NO_TRANSLATE_JS, unsafe_allow_javascript=True)
except TypeError:
    import streamlit.components.v1 as _components
    _components.html(_NO_TRANSLATE_JS, height=0)

# 카드·표의 종목 링크(?chart=코드)로 들어오면 그 종목 차트를 바로 보여줘요
_qp_chart = st.query_params.get("chart")
if _qp_chart:
    st.session_state["chart_code"] = _qp_chart
    st.session_state["detail_code"] = _qp_chart
    del st.query_params["chart"]

REQUIRED = ("is_kr", "market_of", "quote_url", "INDEXES", "fetch_index_histories", "index_summary",
            "fetch_kr_shares", "fetch_fx", "format_krw", "fetch_investor_flows", "fetch_market_overview",
            "market_mood", "fetch_stock_trends", "trend_summary", "resolve_codes", "INST_DETAIL",
            "breakout_hold", "add_rs_ranks", "atr_pct", "BO_MODES",
            "fetch_financials", "fetch_financials_many", "earnings_trend",
            "fetch_monthlies", "newhigh_flags", "ma_signal", "index_rs",
            "buy_screen", "buy_checks", "position_plan", "BUY_RULES", "BUY_DEFAULTS", "EXTRA_KEYS",
            "add_leader_ranks", "sector_leaders", "momentum_engine", "next_leader_sectors", "NEXT_DEFAULTS",
            "nh_candidates", "fetch_official_52w", "NHC_DEFAULTS", "NHC_STATES", "volume_surges", "VS_DEFAULTS",
            "SCENARIO_PRESETS", "SCN_WINDOWS", "op_growth", "rotation_confirm", "sector_money_radar", "money_stats", "SCN_INFO", "basket_stats", "classify_scenario", "scenario_paths",
            "live_volume", "sector_money", "session_frac", "fetch_chart", "chart_with_live", "CHART_TF",
            "market_turnover", "fetch_market_turnover_hist")
DATA_VERSION = "2026-09-28-turnover"   # data.py의 DATA_VERSION과 같아야 해요


def _data_stale() -> bool:
    return any(not hasattr(data, n) for n in REQUIRED) or getattr(data, "DATA_VERSION", None) != DATA_VERSION


if _data_stale():
    # GitHub에서 파일을 바꾼 직후, 서버가 예전 data.py를 기억하고 있는 경우가 있어 한 번 새로 읽어 봅니다.
    data = importlib.reload(data)
    stock_list = importlib.reload(stock_list)
_missing = [n for n in REQUIRED if not hasattr(data, n)]
if getattr(data, "DATA_VERSION", None) != DATA_VERSION:
    _missing.append(f"버전 {DATA_VERSION}")
REPORTS_VERSION = "2026-09-28-reports"
if getattr(reports, "REPORTS_VERSION", None) != REPORTS_VERSION:
    reports = importlib.reload(reports)
if getattr(reports, "REPORTS_VERSION", None) != REPORTS_VERSION:
    _missing.append("reports.py 새 파일")
HOLDINGS_VERSION = "2026-09-28-holdings"
if getattr(holdings, "HOLDINGS_VERSION", None) != HOLDINGS_VERSION:
    holdings = importlib.reload(holdings)
if getattr(holdings, "HOLDINGS_VERSION", None) != HOLDINGS_VERSION:
    _missing.append("holdings.py 새 파일")
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

# 앱에서 직접 넣은 리포트(user_reports.json)의 종목을 보드에 합쳐요
USER_STORE = reports.load_local()
STOCKS, TAGS = reports.merge(STOCKS, TAGS, USER_STORE)
for _s in STOCKS:
    _s.setdefault("tags", [])
    _s.setdefault("notes", [])
if any(s["sector"] not in SECTOR_ORDER for s in STOCKS):
    SECTOR_ORDER = SECTOR_ORDER + sorted({s["sector"] for s in STOCKS} - set(SECTOR_ORDER))
if any(s["group"] not in GROUP_ORDER for s in STOCKS):
    GROUP_ORDER = GROUP_ORDER + sorted({s["group"] for s in STOCKS} - set(GROUP_ORDER))


def _secret(key: str, default: str = "") -> str:
    try:
        v = st.secrets.get(key)
    except Exception:
        v = None
    return str(v or os.environ.get(key, default) or "")

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
/* ── 사이드바(보기 설정) ── */
[data-testid="stSidebar"] { background: #F6F8F9; border-right: 1px solid #E3E8EB; }
[data-testid="stSidebar"] .block-container, [data-testid="stSidebarUserContent"] { padding-top: 1.2rem; }
.sb-title { font-size: 1.15rem; font-weight: 800; color: #16212B; letter-spacing: -0.02em;
            padding-bottom: 0.55rem; margin-bottom: 0.4rem; border-bottom: 2px solid #16212B; }
[data-testid="stSidebar"] label p { font-size: 0.8rem !important; font-weight: 700 !important; color: #3A4852 !important; }
[data-testid="stSidebar"] [data-testid="stButtonGroup"] button { border-radius: 8px; }
[data-testid="stSidebar"] [role="radiogroup"]:has(> button[data-variant="segmented_control"]) {
  display: flex !important; flex-wrap: nowrap !important; width: 100%; }
[data-testid="stSidebar"] button[data-variant="segmented_control"] {
  flex: 1 1 0 !important; min-width: 0 !important; padding: 0.35rem 0.2rem !important; }
[data-testid="stSidebar"] [data-testid="stButtonGroup"] button[data-selected="true"] {
  background: #16212B !important; border-color: #16212B !important; }
[data-testid="stSidebar"] [data-testid="stButtonGroup"] button[data-selected="true"] p {
  color: #FFFFFF !important; font-weight: 700; }
[data-testid="stSidebar"] [data-testid="stExpander"] details { background: #FFFFFF; }
[data-testid="stSidebar"] [class*="st-key-sb_box_"] { background: #FFFFFF; border-radius: 12px !important;
  border: 1px solid #E3E8EB !important; padding: 0.75rem 0.75rem 0.8rem !important; gap: 0.45rem !important; }
.sb-head { display: flex; justify-content: space-between; align-items: baseline;
  padding-bottom: 0.35rem; border-bottom: 1px solid #EEF1F3; margin-bottom: 0.1rem; }
.sb-head span { font-size: 0.86rem; font-weight: 800; color: #16212B; }
.sb-head em { font-style: normal; font-size: 0.72rem; font-weight: 700; color: #7A8A94; }
.sb-cat { font-size: 0.7rem; font-weight: 800; color: #7A8A94; letter-spacing: 0.02em; margin: 0.45rem 0 0;
  display: flex; align-items: center; gap: 0.4rem; }
.sb-cat::after { content: ""; flex: 1; height: 1px; background: #EEF1F3; }
.sb-cat { line-height: 1.1rem; height: 1.1rem; }
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"]:has(.sb-cat),
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"]:has(.sb-head) { margin-bottom: 0 !important; overflow: visible !important; }
[data-testid="stSidebar"] [data-testid="stElementContainer"]:has(.sb-cat) { height: auto !important; min-height: 1.4rem; overflow: visible !important; }
[data-testid="stSidebarHeader"] { height: 2.2rem !important; min-height: 2.2rem !important; padding-top: 0.4rem !important; }
[data-testid="stSidebar"] .st-key-sb_box_sector [data-testid="stHorizontalBlock"] { gap: 0.35rem !important; }
[data-testid="stSidebar"] .st-key-sb_box_sector [data-testid="stColumn"] { min-width: 0 !important; }
[data-testid="stSidebar"] .st-key-sb_box_sector button {
  min-height: 2.05rem; height: 2.05rem; padding: 0 0.45rem !important; border-radius: 8px;
  justify-content: flex-start; border: 1px solid #DCE3E7; background: #F6F8F9; }
[data-testid="stSidebar"] .st-key-sb_box_sector button p {
  font-size: 0.78rem !important; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
[data-testid="stSidebar"] .st-key-sb_box_sector button[kind="primary"] { background: #16212B; border-color: #16212B; }
[data-testid="stSidebar"] .st-key-sb_box_sector button[kind="primary"] p { color: #FFFFFF !important; }
[data-testid="stSidebar"] .st-key-sb_box_sector button:hover { border-color: #16212B; }
[data-testid="stSidebar"] [data-testid="stExpander"] summary p { font-weight: 700; color: #16212B; }
/* ── 메인 탭: 기본 · 종목 시그널 · 섹터 흐름 3묶음으로 나눠 보이게 ── */
.st-key-main_tabs [role="tablist"] { gap: 0.3rem; border-bottom: 2px solid #DCE3E7; padding-bottom: 0; }
.st-key-main_tabs [role="tab"] {
  position: relative; --gc: #51616C; --gt: #F1F4F6;
  background: var(--gt); border: 1px solid #E3E8EB; border-top: 3px solid var(--gc); border-bottom: none;
  border-radius: 8px 8px 0 0; padding: 1.15rem 0.85rem 0.45rem !important; overflow: visible !important; }
.st-key-main_tabs [role="tab"]:nth-child(n+5):nth-child(-n+7) { --gc: #E0672B; --gt: #FDF3EC; }
.st-key-main_tabs [role="tab"]:nth-child(n+8) { --gc: #1F66C9; --gt: #EEF4FC; }
.st-key-main_tabs [role="tab"]:nth-child(5),
.st-key-main_tabs [role="tab"]:nth-child(8) { margin-left: 0.9rem; }
.st-key-main_tabs [role="tab"]::before {
  position: absolute; top: 0.2rem; left: 0.85rem; font-size: 0.64rem; font-weight: 800;
  letter-spacing: 0.02em; color: var(--gc); white-space: nowrap; content: ""; z-index: 2; }
.st-key-main_tabs [role="tab"]:nth-child(1)::before { content: "기본"; }
.st-key-main_tabs [role="tab"]:nth-child(5)::before { content: "종목 시그널"; }
.st-key-main_tabs [role="tab"]:nth-child(8)::before { content: "섹터 흐름"; }
.st-key-main_tabs [role="tab"] p { color: #3A4852; font-size: 0.98rem !important; }
.st-key-main_tabs [role="tab"][aria-selected="true"] { background: var(--gc) !important; border-color: var(--gc); }
.st-key-main_tabs [role="tab"][aria-selected="true"] p,
.st-key-main_tabs [role="tab"][aria-selected="true"]::before { color: #FFFFFF !important; }
.st-key-main_tabs .react-aria-SelectionIndicator, .st-key-main_tabs [data-baseweb="tab-highlight"], .st-key-main_tabs [data-baseweb="tab-border"] { display: none; }
.ch-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.35rem 0.7rem; margin: 0.3rem 0 0.4rem; }
.ch-head { background: #FFFFFF; border: 1px solid #E3E8EB; border-radius: 12px; padding: 0.8rem 1rem; margin: 0.6rem 0 0.7rem !important; }
.ch-head b { font-size: 1.7rem; font-weight: 800; color: #16212B; letter-spacing: -0.02em; }
.ch-code { font-size: 0.95rem; color: #7A8A94; }
.ch-px { font-size: 1.6rem; font-weight: 800; font-variant-numeric: tabular-nums; }
.ch-px small { font-size: 1.05rem; font-weight: 700; margin-left: 0.25rem; }
.ch-sub { font-size: 0.95rem; color: #51616C; flex-basis: 100%; }
[class*="st-key-"][class*="chart_bar"], .st-key-chart_search {
  background: #FFFFFF; border: 1px solid #E3E8EB; border-radius: 12px; padding: 0.75rem 0.9rem 0.85rem; }
[class*="st-key-"][class*="chart_bar"] label p, .st-key-chart_search label p {
  font-size: 0.9rem !important; font-weight: 800 !important; color: #16212B !important; }
[class*="st-key-"][class*="chart_bar"] button[data-variant="segmented_control"] {
  min-height: 2.5rem; padding: 0.35rem 0.4rem !important; }
[class*="st-key-"][class*="chart_bar"] button p { font-size: 0.98rem !important; font-weight: 700; }
[class*="st-key-"][class*="chart_bar"] button[data-selected="true"] { background: #16212B !important; border-color: #16212B !important; }
[class*="st-key-"][class*="chart_bar"] button[data-selected="true"] p { color: #FFFFFF !important; }
.st-key-chart_search input { font-size: 1rem !important; min-height: 2.6rem; }
.st-key-chart_search [data-baseweb="select"] div { font-size: 1rem; }
.st-key-chart_search [data-testid="stButtonGroup"] button { min-height: 2.2rem; }
.st-key-chart_search [data-testid="stButtonGroup"] button p { font-size: 0.95rem !important; }
.st-key-chart_search [data-testid="stButtonGroup"] button[data-selected="true"] { background: #16212B !important; }
.st-key-chart_search [data-testid="stButtonGroup"] button[data-selected="true"] p { color: #FFFFFF !important; }
/* ── 코스피·코스닥 거래대금 ── */
.tv-box { background: #FFFFFF; border: 1px solid #E3E8EB; border-radius: 14px; padding: 0.9rem 1rem 0.8rem; margin: 0.2rem 0 1rem; }
.tv-title { display: flex; justify-content: space-between; align-items: baseline; gap: 0.6rem; margin-bottom: 0.7rem; }
.tv-title b { font-size: 1.08rem; font-weight: 800; color: #16212B; }
.tv-title span { font-size: 0.8rem; color: #7A8A94; font-weight: 600; }
.tv-chips { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 0.6rem; margin-bottom: 0.8rem; }
.tv-chip { background: #F6F8F9; border: 1px solid #E3E8EB; border-radius: 12px; padding: 0.65rem 0.85rem; }
.tv-chip.tot { background: #16212B; border-color: #16212B; }
.tv-chip.tot .tv-chip-h, .tv-chip.tot .tv-chip-f { color: #9FB0BA; }
.tv-chip.tot .tv-chip-v, .tv-chip.tot .tv-chip-s { color: #FFFFFF !important; }
.tv-chip-h { font-size: 0.8rem; color: #6B7A84; font-weight: 700; }
.tv-chip-v { font-size: 1.6rem; font-weight: 800; color: #16212B; font-variant-numeric: tabular-nums; line-height: 1.25; }
.tv-chip-v small { font-size: 0.9rem; margin-left: 0.1rem; }
.tv-chip-s { font-size: 0.92rem; font-weight: 800; margin-top: 0.1rem; }
.tv-chip-f { font-size: 0.76rem; color: #7A8A94; margin-top: 0.2rem; }
.tv-wrap { overflow-x: auto; }
.tv-tbl { width: 100%; border-collapse: collapse; font-size: 0.88rem; font-variant-numeric: tabular-nums; white-space: nowrap; }
.tv-tbl th { font-size: 0.76rem; color: #6B7A84; font-weight: 700; text-align: right; padding: 0.45rem 0.55rem;
  border-bottom: 2px solid #E3E8EB; background: #F6F8F9; }
.tv-tbl td { text-align: right; padding: 0.5rem 0.55rem; border-bottom: 1px solid #EEF1F3; color: #16212B; }
.tv-tbl .l { text-align: left; }
.tv-tbl td.now { font-weight: 800; }
.tv-tbl tbody tr:nth-child(2n) td { border-bottom: 2px solid #E3E8EB; }
.tv-x { min-width: 11rem; }
.tv-bar { position: relative; height: 6px; background: #EEF1F3; border-radius: 3px; margin-bottom: 0.2rem; }
.tv-bar i { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 3px; }
.tv-bar b { position: absolute; left: 50%; top: -3px; bottom: -3px; width: 1.5px; background: #16212B; }
.tv-x span { font-size: 0.82rem; font-weight: 700; }
.tv-note { font-size: 0.74rem; color: #7A8A94; margin-top: 0.55rem; line-height: 1.5; }
.st-key-no_translate { display: none !important; }
.rp-badges { display: flex; flex-wrap: wrap; gap: 0.45rem; margin: 0 0 0.7rem; }
.rp-badge { font-size: 0.82rem; border-radius: 999px; padding: 0.3rem 0.75rem; border: 1px solid #E3E8EB; background: #FFFFFF; color: #51616C; }
.rp-badge b { margin-right: 0.25rem; }
.rp-badge.on { border-color: #2E9D5B; color: #1E7A45; background: #EEF8F2; }
.rp-badge.off { border-color: #E3C7A0; color: #9A6417; background: #FDF6EC; }
[class*="st-key-rp_box_"] { background: #FFFFFF; border-radius: 12px !important; }
/* 메인 탭 색칠이 탭 안쪽의 작은 탭(차트·실적·뉴스 등)에는 번지지 않게 */
.st-key-main_tabs [role="tabpanel"] [role="tab"], .st-key-main_tabs [role="tabpanel"] [role="tab"]:nth-child(n) {
  --gc: #16212B; --gt: transparent; background: transparent !important; border: none !important;
  border-bottom: 2px solid transparent !important; border-radius: 0 !important; margin-left: 0 !important;
  padding: 0.4rem 0.8rem !important; }
.st-key-main_tabs [role="tabpanel"] [role="tab"]::before { content: none !important; }
.st-key-main_tabs [role="tabpanel"] [role="tab"][aria-selected="true"] { background: transparent !important;
  border-bottom: 2px solid #16212B !important; }
.st-key-main_tabs [role="tabpanel"] [role="tab"][aria-selected="true"] p { color: #16212B !important; font-weight: 800; }
.st-key-main_tabs [role="tabpanel"] [role="tab"] p { font-size: 0.9rem !important; }
/* ── 💼 내 보유 ── */
[class*="st-key-hold_card_"] { background: #FFFFFF; border-radius: 14px !important; }
.hd-top { display: flex; justify-content: space-between; align-items: center; gap: 0.6rem; flex-wrap: wrap; }
.hd-name { font-size: 1.35rem; font-weight: 800; color: #16212B; }
.hd-code { font-size: 0.85rem; color: #7A8A94; margin: 0 0.5rem 0 0.35rem; }
.hd-px { font-size: 1.2rem; font-weight: 800; margin-right: 0.5rem; font-variant-numeric: tabular-nums; }
.hd-pnl { font-size: 1.05rem; font-weight: 800; }
.hd-verdict { font-size: 0.95rem; font-weight: 800; padding: 0.35rem 0.8rem; border-radius: 999px; border: 1.5px solid; }
.hd-sub { font-size: 0.85rem; color: #51616C; margin: 0.3rem 0 0.6rem; }
.hd-checks { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 0.4rem; }
.hd-chk { border: 1px solid #E3E8EB; border-radius: 10px; padding: 0.45rem 0.65rem; background: #F9FAFB; }
.hd-chk span { display: block; font-size: 0.84rem; font-weight: 800; color: #16212B; }
.hd-chk em { font-style: normal; font-size: 0.8rem; color: #51616C; }
.hd-chk.bad { border-color: #F2B8BB; background: #FDF3F3; }
.hd-chk.warn { border-color: #EED9A8; background: #FFFAEE; }
.hd-chk.good { border-color: #CBE7D5; background: #F4FBF6; }
.hd-act { margin: 0.6rem 0 0.4rem; padding: 0.55rem 0.8rem; border-left: 4px solid #16212B; background: #F6F8F9; border-radius: 6px; }
.hd-act b { font-size: 0.86rem; } .hd-act ul { margin: 0.2rem 0 0 1rem; padding: 0; font-size: 0.86rem; }
.hd-news { display: flex; flex-direction: column; gap: 0.3rem; }
.hd-news a { display: block; text-decoration: none; padding: 0.4rem 0.55rem; border: 1px solid #EEF1F3; border-radius: 8px; background: #FFFFFF; }
.hd-news a:hover { border-color: #16212B; }
.hd-news span { display: block; font-size: 0.88rem; color: #16212B; font-weight: 600; }
.hd-news em { font-style: normal; font-size: 0.74rem; color: #7A8A94; }
/* ── 공통 정리 ── */
[data-testid="stCaptionContainer"] p { color: #7A8A94 !important; font-size: 0.78rem !important; }
.stApp h4 { font-size: 1.05rem !important; font-weight: 800 !important; margin: 0.6rem 0 0.3rem !important; }
[data-testid="stTabs"] [role="tablist"] { gap: 0.2rem; border-bottom: 1px solid #DCE3E7; }
[data-testid="stTabs"] button[role="tab"] { padding: 0.45rem 0.8rem; border-radius: 8px 8px 0 0; }
[data-testid="stTabs"] button[role="tab"][aria-selected="true"] { background: #FFFFFF; }
[data-testid="stTabs"] button[role="tab"] p { font-weight: 700; }
[data-testid="stExpander"] details { border: 1px solid #E3E8EB !important; border-radius: 10px !important; background: #FFFFFF; }
[data-testid="stExpander"] summary p { font-size: 0.86rem; color: #51616C; }
[data-testid="stMetric"] { background: #FFFFFF; border: 1px solid #E3E8EB; border-radius: 12px; padding: 0.55rem 0.8rem; }
[data-testid="stMetricLabel"] p { font-size: 0.78rem !important; color: #6B7A84 !important; }
[data-testid="stMetricValue"] { font-size: 1.45rem !important; font-weight: 800 !important; }
[data-testid="stDataFrame"] { border-radius: 10px; overflow: hidden; }
.sec-h { display: flex; align-items: baseline; justify-content: space-between; gap: 0.6rem; margin: 0.2rem 0 0.5rem; }
.sec-h b { font-size: 1.12rem; font-weight: 800; color: #16212B; }
.sec-h span { font-size: 0.78rem; color: #7A8A94; }
/* 돈의 방향 */
.verdict { border-left: 6px solid var(--vc); background: #FFFFFF; border-radius: 12px; padding: 0.8rem 1rem;
  box-shadow: 0 1px 2px rgba(0,0,0,.04); margin-bottom: 0.7rem; }
.v-top { display: flex; justify-content: space-between; gap: 0.5rem; font-size: 0.76rem; color: #7A8A94; }
.v-lab { font-weight: 700; letter-spacing: .02em; }
.v-title { font-size: 1.55rem; font-weight: 900; color: var(--vc); line-height: 1.25; margin-top: 0.1rem; }
.v-act { font-size: 0.95rem; font-weight: 600; color: #26343E; margin-top: 0.15rem; }
.tiles-wrap { display: grid; grid-template-columns: 2fr 1fr; gap: 0.8rem; margin-bottom: 0.8rem; }
.tiles-h { font-size: 0.8rem; color: #51616C; margin-bottom: 0.3rem; font-weight: 600; }
.tiles-h b { color: #16212B; } .tiles-h small { color: #8A979F; font-weight: 500; }
.tiles { display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.4rem; }
.tiles.t2 { grid-template-columns: repeat(2, 1fr); }
.tile { position: relative; background: #FFFFFF; border: 1px solid #E3E8EB; border-radius: 10px; padding: 0.45rem 0.55rem; }
.tile.ok { border-color: #9ED3B2; background: #F1FAF4; } .tile.no { border-color: #F0C2C4; background: #FDF4F4; }
.t-ic { position: absolute; top: 0.35rem; right: 0.5rem; font-weight: 900; font-size: 0.85rem; }
.tile.ok .t-ic { color: #1B8A4B; } .tile.no .t-ic { color: #C0262E; } .tile.na .t-ic { color: #8A979F; }
.t-lb { font-size: 0.7rem; color: #6B7A84; } .t-v { font-size: 1.05rem; font-weight: 800; color: #16212B;
  font-variant-numeric: tabular-nums; white-space: nowrap; }
.lead-chips { display: flex; flex-wrap: wrap; gap: 0.3rem; margin-top: 0.4rem; }
.lc { font-size: 0.72rem; font-weight: 700; padding: 0.12rem 0.45rem; border-radius: 999px; }
.lc.ok { background: #E3F4EA; color: #1B6B3E; } .lc.no { background: #FBE3E4; color: #A2343B; }
.mv-row { display: flex; flex-wrap: wrap; align-items: center; gap: 0.4rem 0.6rem; background: #FFFFFF;
  border: 1px solid #E3E8EB; border-radius: 10px; padding: 0.55rem 0.8rem; margin-bottom: 0.5rem; font-size: 0.88rem; }
.mv { font-weight: 800; } .mv.out { color: #1F66C9; } .mv.in { color: #D6333B; }
.mv-arrow { font-size: 1.1rem; color: #8A979F; font-weight: 800; }
.mv-total { margin-left: auto; font-size: 0.78rem; color: #6B7A84; }
.mini { background: #FFFFFF; border: 1px solid #E3E8EB; border-radius: 10px; overflow: hidden; }
.mini-row { display: flex; justify-content: space-between; align-items: center; gap: 0.6rem; padding: 0.45rem 0.7rem;
  border-top: 1px solid #F0F3F5; font-size: 0.86rem; }
.mini-row:first-child { border-top: none; }
.mini-row small { display: block; color: #7A8A94; font-size: 0.72rem; margin-top: 0.05rem; }
.mini-row span { white-space: nowrap; font-weight: 700; font-size: 0.8rem; color: #26343E; }
@media (max-width: 640px) {
  .tiles-wrap { grid-template-columns: 1fr; }
  .tiles { grid-template-columns: repeat(2, 1fr); }
  .v-title { font-size: 1.3rem; }
  .v-top { flex-direction: column; gap: 0; }
  [data-testid="stMetric"] { padding: 0.4rem 0.55rem; }
  [data-testid="stMetricValue"] { font-size: 1.15rem !important; }
  [data-testid="stTabs"] button[role="tab"] { padding: 0.35rem 0.5rem; }
}
.st-key-mobile_view { display: none; }
.kcards { display: flex; flex-direction: column; gap: 0.45rem; }
.stApp a.kcard { display: block; background: #FFFFFF; border: 1px solid #DCE3E7; border-radius: 12px;
  padding: 0.65rem 0.8rem 0.6rem; text-decoration: none; color: #16212B; }
.stApp a.kcard.hot { background: #FFF8E3; border-color: #EFD48A; }
.k-top { display: flex; justify-content: space-between; align-items: baseline; gap: 0.5rem; }
.k-name { font-weight: 800; font-size: 1.02rem; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.k-crown { font-size: 0.68rem; font-weight: 800; background: #F2B134; color: #16212B; border-radius: 6px; padding: 0.05rem 0.35rem;
  vertical-align: 2px; }
.k-px { font-weight: 700; font-size: 0.95rem; white-space: nowrap; font-variant-numeric: tabular-nums; }
.k-chg { font-weight: 800; margin-left: 0.25rem; }
.k-keys { display: grid; grid-template-columns: 1fr 1.45fr 0.6fr; gap: 0.35rem; margin: 0.5rem 0 0.4rem; }
.k-keys div { background: #F2F5F7; border-radius: 8px; padding: 0.3rem 0.45rem; min-width: 0; }
.k-keys small { display: block; font-size: 0.64rem; color: #6B7A84; line-height: 1.2; }
.k-keys b { display: block; font-size: 0.92rem; font-weight: 800; color: #16212B; white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; font-variant-numeric: tabular-nums; }
.k-keys b.hot { color: #D6333B; } .k-keys b.near { color: #C27C0E; }
.k-keys b.first { color: #D6333B; } .k-keys b.brk { color: #B8452C; } .k-keys b.off { color: #6B7A84; font-weight: 700; }
.k-keys b.rs { color: #D6333B; }
.k-sub { font-size: 0.74rem; color: #51616C; line-height: 1.45; }
.k-flow { display: flex; justify-content: space-between; gap: 0.5rem; font-size: 0.72rem; color: #51616C; margin-top: 0.15rem;
  font-variant-numeric: tabular-nums; }
@media (max-width: 640px) {
  [data-testid="stMainBlockContainer"] { padding-left: 0.7rem !important; padding-right: 0.7rem !important; }
  .st-key-mobile_view div[role="radiogroup"] { gap: 0.25rem 0.6rem; }
  .st-key-mobile_view div[role="radiogroup"] label p { font-size: 0.8rem; }
  [data-testid="stTabs"] button p { font-size: 0.85rem; }
}

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
.sig { background: #FFFFFF; border: 1px solid #E3E8EB; border-radius: 12px; padding: 0.7rem 0.9rem;
       margin-bottom: 0.6rem; display: grid; grid-template-columns: repeat(2, 1fr); gap: 0.6rem 1.2rem; }
.sig-mk { display: flex; flex-wrap: wrap; align-items: center; gap: 0.35rem 0.9rem; }
.sig-name { font-weight: 800; font-size: 1rem; color: #16212B; min-width: 3.2rem; }
.sig-item { display: inline-flex; align-items: center; gap: 0.35rem; font-size: 0.8rem; color: #51616C; }
.tl { display: inline-flex; gap: 3px; background: #1C2530; border-radius: 999px; padding: 3px 6px; }
.tl i { width: 15px; height: 15px; border-radius: 50%; background: #D9DEE2; font-style: normal; font-size: 0.6rem;
        font-weight: 800; color: #FFFFFF; display: inline-flex; align-items: center; justify-content: center; }
.tl i.G { background: #1FA75A; } .tl i.Y { background: #F2B134; color: #3A2A00; } .tl i.R { background: #D6333B; }
.sig-txt.G { color: #1B8A4B; font-weight: 700; } .sig-txt.Y { color: #A87400; font-weight: 700; }
.sig-txt.R { color: #C0262E; font-weight: 700; }
.sig-rs { font-size: 0.8rem; color: #51616C; } .sig-rs b { color: #16212B; font-size: 0.92rem; }
@media (max-width: 640px) { .sig { grid-template-columns: 1fr; } }
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


# ─────────────────────────── 데이터 기억(속도) ───────────────────────────
# 예전에는 기억 시간(30분·5분·2분…)이 지나면 그 순간 화면이 멈추고 수백 종목을 다시 받았어요.
# 이제는 처음 한 번만 기다리고, 그 뒤로는 지난 데이터를 바로 보여주면서 뒤에서 새로 받아 바꿔 끼워요.
# (여러 사람이 봐도 한 번만 받고, 복사 없이 같은 데이터를 같이 써서 메모리도 덜 써요.)
@st.cache_resource
def _bg_store() -> dict:
    return {"lock": threading.Lock(), "items": {}}


def swr(key, ttl: int, loader, spinner: str | None = None, first_wait: bool = True, empty=None):
    """key의 데이터를 돌려줘요. ttl초가 지났으면 지난 값을 먼저 주고 뒤에서 loader()로 새로 받아요.
    first_wait=False면 처음에도 기다리지 않고 empty를 준 뒤 뒤에서 받아요(없어도 화면이 되는 데이터용)."""
    store = _bg_store()
    with store["lock"]:
        item = store["items"].setdefault(key, {"value": None, "t": 0.0, "busy": False})
        has_value = item["value"] is not None
        need = (not has_value) or time.time() - item["t"] >= ttl
        start_bg = need and not item["busy"] and (has_value or not first_wait)
        if start_bg:
            item["busy"] = True

    def work():
        try:
            item.update(value=loader(), t=time.time())
        except Exception:   # 네트워크 오류: 지난 값을 두고 1분 뒤 다시
            item["t"] = time.time() - ttl + 60
        finally:
            item["busy"] = False

    if start_bg:
        threading.Thread(target=work, daemon=True).start()
    if has_value:
        return item["value"]
    if not first_wait:
        return empty
    with st.spinner(spinner or "데이터를 불러오는 중이에요."):
        with store["lock"]:
            mine = not item["busy"]
            if mine:
                item["busy"] = True
        if mine:
            try:
                item.update(value=loader(), t=time.time())
            finally:
                item["busy"] = False
        else:                       # 다른 사람이 이미 받는 중이면 끝날 때까지 기다려요
            while item["busy"] and item["value"] is None:
                time.sleep(0.3)
            if item["value"] is None:
                item.update(value=loader(), t=time.time())
    return item["value"]


def swr_clear(*prefixes: str):
    store = _bg_store()
    with store["lock"]:
        for k in [k for k in store["items"] if isinstance(k, tuple) and k[0] in prefixes]:
            store["items"].pop(k, None)


def load_histories(codes: tuple[str, ...]):
    return swr(("hist_kr", codes), 1800, lambda: data.fetch_histories(codes),
               "2년치 일봉을 불러오는 중이에요. 처음 한 번만 몇 초 걸려요.")


def load_histories_overseas(codes: tuple[str, ...]):
    return swr(("hist_os", codes), 900, lambda: data.fetch_histories(codes), "해외 종목 일봉을 불러오는 중이에요.")


def load_official_52w(codes: tuple[str, ...]) -> dict:
    """네이버 공식 52주 최고·최저(10분마다). 일봉으로 계산한 값과 대조해서 다르면 공식값으로 고쳐요."""
    return swr(("h52", codes), 600, lambda: data.fetch_official_52w(codes),
               "네이버 공식 52주 최고·최저가와 대조하는 중이에요. 처음 한 번만 몇 초 걸려요.")


def load_monthlies(codes: tuple[str, ...]):
    """역대 최고가 계산용 월봉. 처음에도 기다리지 않아요(받는 동안은 '역대 신고가'만 잠깐 비어 있어요)."""
    return swr(("monthly", codes), 21600, lambda: data.fetch_monthlies(codes), first_wait=False, empty={})


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


def load_fx():
    return swr(("fx",), 3600, data.fetch_fx, "환율을 불러오는 중이에요.")


def load_indexes():
    return swr(("indexes",), 120, data.fetch_index_histories, "지수·환율·유가를 불러오는 중이에요.")


@st.cache_data(ttl=30, show_spinner=False)
def load_overview():
    return data.fetch_market_overview()


def load_flows():
    overview = load_overview()
    return swr(("flows",), 300, lambda: data.fetch_investor_flows(overview), "투자자별 순매수를 불러오는 중이에요.")


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


NH_FILTERS = {
    "전체": None,
    "일봉 52주 신고가(오늘)": "nh52_d", "주봉 52주 신고가(이번 주)": "nh52_w", "월봉 52주 신고가(이번 달)": "nh52_m",
    "일봉 역대 신고가(오늘)": "ath_d", "주봉 역대 신고가(이번 주)": "ath_w", "월봉 역대 신고가(이번 달)": "ath_m",
}

# ─────────────────────────── 사이드바 ───────────────────────────
MARKETS = {"한국": "KR", "미국": "US", "기타": "OTHER"}
# 산업을 큰 묶음으로 나눠 칸으로 보여줘요. 여기 없는 산업은 '기타'로 가요.
SECTOR_CATS = [
    ("반도체 · IT", ["반도체", "글로벌 반도체", "AI·IT", "AI 인프라(해외)", "AI 소프트웨어(해외)", "OLED",
                    "데이터센터 전력", "우주 데이터센터"]),
    ("에너지 · 산업재", ["2차전지", "전력·에너지", "풍력", "조선", "건설", "산업재", "전략광물"]),
    ("소비 · 바이오 · 금융", ["화장품", "바이오·헬스케어", "금융·지주"]),
    ("기타", ["기타", "기타(리포트 스크린)", "해외 기타"]),
]
SECTOR_SHORT = {"기타(리포트 스크린)": "리포트", "AI 인프라(해외)": "AI 인프라", "AI 소프트웨어(해외)": "AI SW",
                "글로벌 반도체": "반도체", "해외 기타": "기타",
                "바이오·헬스케어": "바이오·헬스", "데이터센터 전력": "DC 전력", "우주 데이터센터": "우주 DC"}


def _mkt(code: str) -> str:
    m = data.market_of(code)[0]
    return m if m in ("KR", "US") else "OTHER"


def _sb_head(text: str, right: str = ""):
    st.markdown(f'<div class="sb-head"><span>{text}</span><em>{right}</em></div>', unsafe_allow_html=True)


def _toggle_sector(key: str, sec: str | None):
    cur = list(st.session_state.get(key, []))
    if sec is None:
        cur = []
    elif sec in cur:
        cur.remove(sec)
    else:
        cur.append(sec)
    st.session_state[key] = cur


with st.sidebar:
    st.markdown('<div class="sb-title">보기 설정</div>', unsafe_allow_html=True)

    # ① 시장
    with st.container(border=True, key="sb_box_market"):
        _sb_head("시장")
        market_label = st.segmented_control("시장", list(MARKETS), default="한국", required=True,
                                            key="sb_market2", width="stretch", label_visibility="collapsed") or "한국"
        market = MARKETS[market_label]
        mkt_stocks = [x for x in STOCKS if _mkt(x["code"]) == market]

    # ② 산업 — 묶음별 2칸 격자. 아무것도 안 고르면 그 시장 전체
    sector_choices = [sec for sec in SECTOR_ORDER if any(x["sector"] == sec for x in mkt_stocks)]
    sector_choices += sorted({x["sector"] for x in mkt_stocks} - set(sector_choices))
    sec_count = {sec: sum(x["sector"] == sec for x in mkt_stocks) for sec in sector_choices}
    sel_key = f"sb_sel_{market}"
    st.session_state.setdefault(sel_key, [])
    sectors = [x for x in st.session_state[sel_key] if x in sector_choices]
    with st.container(border=True, key="sb_box_sector"):
        _sb_head("산업", f"{len(sectors)}개 선택" if sectors else "전체")
        st.button(f"전체  {len(mkt_stocks)}", key=f"sb_all_{market}", width="stretch",
                  type="primary" if not sectors else "secondary",
                  on_click=_toggle_sector, args=(sel_key, None))
        placed = set()
        cats = [(nm, [x for x in secs if x in sec_count]) for nm, secs in SECTOR_CATS]
        rest = [x for x in sector_choices if not any(x in secs for _, secs in cats)]
        if rest:
            cats[-1] = (cats[-1][0], cats[-1][1] + rest)
        for cat, secs in cats:
            if not secs:
                continue
            st.markdown(f'<div class="sb-cat">{cat}</div>', unsafe_allow_html=True)
            for i in range(0, len(secs), 2):
                cols = st.columns(2, gap="small")
                for col, sec in zip(cols, secs[i:i + 2]):
                    col.button(f"{SECTOR_SHORT.get(sec, sec)}  {sec_count[sec]}", key=f"sb_sec_{market}_{sec}",
                               width="stretch", type="primary" if sec in sectors else "secondary",
                               on_click=_toggle_sector, args=(sel_key, sec))
    active_sectors = sectors or sector_choices

    # ③ 종목 찾기 — 세부 분류 · 리포트 태그 · 검색
    with st.container(border=True, key="sb_box_find"):
        _sb_head("종목 찾기")
        group_choices = [g for g in GROUP_ORDER
                         if any(x["group"] == g and x["sector"] in active_sectors for x in mkt_stocks)]
        group_pick = st.selectbox("세부 분류", ["전체", *group_choices], key=f"sb_group_{market}")
        groups = [] if group_pick == "전체" else [group_pick]
        tag_choices = [t for t in TAGS if any(t in x["tags"] for x in mkt_stocks)]
        tags = st.multiselect("리포트 태그", tag_choices, placeholder="선택 안 함", key=f"sb_tags_{market}",
                              help="태그를 고르면 위의 산업·분류와 관계없이 이 시장 전체에서 그 리포트에 나온 종목만 보여줘요.")
        query = st.text_input("종목 검색", placeholder="이름이나 코드", key="sb_query")

    # ④ 조건 필터 — 평소엔 접어 둬요
    with st.expander("📐 조건 필터", expanded=False):
        max_drop = st.slider("52주 최고가에서 몇 % 이내만 볼까요", 0, 90, 90, step=5,
                             help="10으로 두면 최고가 대비 -10% 이내 종목만 보여줘요. 90이면 전체.")
        min_rs = st.slider("종합 RS 이상", 0, 99, 0, step=5, help="0이면 전체. 70으로 두면 RS 70 이상만")
        nh_filter = st.selectbox("신고가 봉 필터", list(NH_FILTERS), index=0,
                                 help="지금 봉(오늘 일봉·이번 주 주봉·이번 달 월봉)의 고가가 52주 또는 역대(상장 이후) 최고가를 넘은 종목만")
        only_aligned = st.toggle("정배열 종목만", help="현재가 > 20일선 > 60일선 > 120일선")
        only_holding = st.toggle("신고가 돌파 유지 종목만",
                                 help="직전 52주 최고가를 종가로 돌파한 뒤 한 번도 그 아래에서 마감하지 않은 종목만")
        only_earn = st.toggle("영업이익·EPS 정배열만",
                              help="최근 실적 연도부터 컨센서스(E)까지 영업이익과 EPS가 해마다 모두 증가한 국내 종목만. "
                                   "전망(E)이 없는 종목·적자 종목·해외 종목은 빠져요. 처음 켤 때 종목 수에 따라 30초~1분 걸려요.")
        earn_years = st.radio("실적 정배열 판정: 최근 실적 몇 년부터", [2, 3], index=1, horizontal=True,
                              format_func=lambda n: f"{n}년 + 전망(E)")
        show_flow = st.toggle("종목별 수급 열 보기", value=True, disabled=(market != "KR"),
                              help="국내 종목의 최근 거래일 개인·외국인·기관 순매수(억원, 종가로 환산한 추정치)와 5일 누적을 표에 붙여요. "
                                   "보고 있는 종목 중 앞쪽 150개까지만 불러와요.")
        show_flow = show_flow and market == "KR"   # 해외는 수급 자료가 없어서 열을 숨겨요

    with st.expander("🧭 주도섹터 기준", expanded=False):
        recent_days = st.slider("신고가로 인정할 기간(거래일)", 1, 20, 5)
        min_count = st.slider("분류 안 신고가 종목 수", 2, 5, 2)

    bo_mode = "line"   # 돌파선 = 돌파한 날 넘어선 직전 52주 최고가

    st.divider()
    refresh_label = st.segmented_control("자동 새로고침", list(REFRESH), default="1분", required=True,
                                         key="sb_refresh", width="stretch") or "1분"
    b1, b2 = st.columns(2)
    if b1.button("시세 새로고침", width="stretch"):
        load_quotes.clear()
    if b2.button("일봉 다시 받기", width="stretch", help="52주 최고가가 이상해 보일 때 눌러요."):
        swr_clear("hist_kr", "hist_os", "monthly")
        _shares_store().update(t=0.0, ok=False)
        load_quotes.clear()


# ─────────────────────────── 화면 조각 ───────────────────────────
def apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    df = df[df["code"].map(_mkt) == market]
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
    if NH_FILTERS[nh_filter]:
        f = f[f[NH_FILTERS[nh_filter]] == True]  # noqa: E712
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
    with st.expander(f"💰 투자자별 순매수 · 연기금·투신 ({max(latest):%m/%d}, 억원)"):
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


def _light(sig: dict | None) -> str:
    """G·Y·R 세 칸 신호등. 켜진 칸에만 색과 글자."""
    on = sig["code"] if sig else None
    return '<span class="tl">' + "".join(
        f'<i class="{c}">{c}</i>' if c == on else "<i></i>" for c in ("G", "Y", "R")) + "</span>"


def _sig_item(label: str, sig: dict | None, n: int) -> str:
    if not sig:
        return f'<span class="sig-item">{label} {_light(None)} <span>-</span></span>'
    slope = "상승" if sig["rising"] else "하락"
    return (f'<span class="sig-item" title="{n}일선 {sig["ma"]:,.2f}, {n}일선 기울기 {slope}">{label} {_light(sig)} '
            f'<span class="sig-txt {sig["code"]}">{sig["text"]}</span>'
            f'<span>{n}일선 {sig["dist"]:+.1f}%</span></span>')


def _signal_panel(hist: dict, board: pd.DataFrame | None) -> str:
    """시장신호: 코스피·코스닥 단기(20일선)·장기(60일선) 신호등 + 지수 RS."""
    cells = ""
    for name, sym in data.MARKETS.items():
        df = hist.get(sym, (data.empty_frame(), None))[0]
        s20, s60 = data.ma_signal(df, 20), data.ma_signal(df, 60)
        rs = data.index_rs(df, board) if board is not None else {}
        n = lambda v: "-" if v is None else f"{v:.0f}"  # noqa: E731
        cells += (f'<div class="sig-mk"><span class="sig-name">{name}</span>'
                  f'{_sig_item("단기", s20, 20)}{_sig_item("장기", s60, 60)}'
                  f'<span class="sig-rs">RS <b>{n(rs.get("rs"))}</b> · 1M {n(rs.get("rs_1m"))} · '
                  f'3M {n(rs.get("rs_3m"))} · 6M {n(rs.get("rs_6m"))}</span></div>')
    return f'<div class="sig">{cells}</div>'


def load_turnover_hist() -> dict:
    return swr(("turnover",), 1800, data.fetch_market_turnover_hist, first_wait=False, empty={})


def _shares_txt(v) -> str:
    if v is None or pd.isna(v):
        return "-"
    return f"{v / 1e8:,.2f}억주" if v >= 1e8 else f"{v / 1e4:,.0f}만주"


def _money_txt(v) -> str:
    return "-" if v is None or pd.isna(v) else data.format_krw(v)


def _ratio_cell(x) -> str:
    if x is None or pd.isna(x):
        return '<td class="tv-x">-</td>'
    pct = (x - 1) * 100
    col = UP if pct >= 10 else (DOWN if pct <= -10 else "#51616C")
    word = "많음" if pct >= 10 else ("적음" if pct <= -10 else "비슷")
    w = max(4, min(100, x / 2 * 100))
    return (f'<td class="tv-x"><div class="tv-bar"><i style="width:{w:.0f}%;background:{col}"></i><b></b></div>'
            f'<span style="color:{col}">{x:.2f}배 · {pct:+.0f}% {word}</span></td>')


def _pct_cell(v) -> str:
    if v is None or pd.isna(v):
        return "<td>-</td>"
    col = UP if v >= 10 else (DOWN if v <= -10 else "#51616C")
    return f'<td style="color:{col};font-weight:700">{v:+.0f}%</td>'


def render_turnover(overview: dict):
    """코스피·코스닥 오늘 거래대금·거래량(실시간)과 평소(5·20·60일 평균) 대비."""
    hist = load_turnover_hist()
    if not hist:
        st.caption("코스피·코스닥 거래대금 평균을 불러오는 중이에요. 잠시 뒤 새로고침하면 나와요.")
        return
    res = {m: data.market_turnover(hist.get(m), overview.get(m)) for m in data.MARKETS}
    res = {m: r for m, r in res.items() if r}
    if not res:
        st.caption("코스피·코스닥 거래대금 자료를 받지 못했어요.")
        return
    any_r = next(iter(res.values()))
    frac, is_open = any_r["frac"], any_r["open"]
    when = f"장 진행 {frac * 100:.0f}% · 실시간" if is_open else "장 마감 기준(오늘 또는 마지막 거래일)"

    # 요약 카드: 시장별 오늘 거래대금
    chips = []
    for m, r in res.items():
        v = r["val"]
        x = v["x_now"]
        col = UP if (x or 1) >= 1.1 else (DOWN if (x or 1) <= 0.9 else "#51616C")
        chips.append(
            f'<div class="tv-chip"><div class="tv-chip-h">{m} 거래대금</div>'
            f'<div class="tv-chip-v">{_money_txt(v["now"])}<small>원</small></div>'
            f'<div class="tv-chip-s" style="color:{col}">{"이 시각 평소" if is_open else "20일 평균"}의 '
            f'{(x if is_open else (v["now"] / v["a20"] if v["now"] else float("nan"))):.2f}배</div>'
            f'<div class="tv-chip-f">마감 예상 {_money_txt(v["proj"])} · 20일 평균 {_money_txt(v["a20"])}</div></div>')
    tot_now = sum((r["val"]["now"] or 0) for r in res.values())
    tot_same = sum(r["val"]["same_time"] for r in res.values())
    chips.append(
        f'<div class="tv-chip tot"><div class="tv-chip-h">두 시장 합계</div>'
        f'<div class="tv-chip-v">{_money_txt(tot_now)}<small>원</small></div>'
        f'<div class="tv-chip-s">{"이 시각 평소" if is_open else "20일 평균"}의 {tot_now / tot_same if tot_same else float("nan"):.2f}배</div>'
        f'<div class="tv-chip-f">{when}</div></div>')

    rows = []
    for m, r in res.items():
        for key, lab, fmt in (("val", "거래대금", _money_txt), ("vol", "거래량", _shares_txt)):
            v = r[key]
            rows.append(
                f'<tr><td class="l"><b>{m}</b></td><td class="l">{lab}</td>'
                f'<td class="now">{fmt(v["now"])}</td><td>{fmt(v["same_time"]) if is_open else "-"}</td>'
                f'{_ratio_cell(v["x_now"] if is_open else (v["now"] / v["a20"] if v["now"] else None))}'
                f'<td>{fmt(v["proj"]) if is_open else fmt(v["now"])}</td>'
                f'<td>{fmt(v["a5"])}</td><td>{fmt(v["a20"])}</td><td>{fmt(v["a60"])}</td>'
                f'{_pct_cell(v["vs5"])}{_pct_cell(v["vs20"])}{_pct_cell(v["vs60"])}</tr>')
    head = ("<tr><th class='l'>시장</th><th class='l'>항목</th><th>오늘 지금까지</th><th>같은 시각 평소</th>"
            "<th>평소 대비</th><th>마감 예상</th><th>5일 평균</th><th>20일 평균</th><th>60일 평균</th>"
            "<th>예상 vs 5일</th><th>예상 vs 20일</th><th>예상 vs 60일</th></tr>")
    st.markdown(
        f'<div class="tv-box"><div class="tv-title"><b>💵 코스피 · 코스닥 거래대금 · 거래량</b><span>{when}</span></div>'
        f'<div class="tv-chips">{"".join(chips)}</div>'
        f'<div class="tv-wrap"><table class="tv-tbl"><thead>{head}</thead><tbody>{"".join(rows)}</tbody></table></div>'
        f'<div class="tv-note">같은 시각 평소 = 20일 평균 × 장 진행 비율 · 마감 예상 = 지금 속도 그대로 15:30까지 갔을 때 · '
        f'장 초반·막판은 거래가 몰려서 예상이 크게 나올 수 있어요 · 평균은 30분마다, 오늘 값은 새로고침마다 갱신</div></div>',
        unsafe_allow_html=True)


def render_market(board: pd.DataFrame | None = None):
    """맨 위 시장 요약: 시장신호(신호등·지수 RS) → 코스피·코스닥 카드 + 오늘의 시장 → 해외 지수·환율·유가."""
    overview = load_overview()
    hist = load_indexes()
    st.markdown(_signal_panel(hist, board), unsafe_allow_html=True)
    hist_sm = {idx["name"]: data.index_summary(hist.get(idx["symbol"], (data.empty_frame(), None))[0])
               for idx in data.INDEXES}
    cards = "".join(_market_card(name, overview.get(name), hist_sm.get(name)) for name in data.MARKETS)
    st.markdown(f'<div class="mk-row">{cards}{_mood_card(overview)}</div>', unsafe_allow_html=True)
    render_turnover(overview)

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
    with st.expander("🌐 해외 지수 · 환율 · 유가 · 차트"):
        st.markdown('<div class="idx-row">' + "".join(chips) + "</div>", unsafe_allow_html=True)
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
        st.caption("시장신호: 단기 20일선 · 장기 60일선 — 🟢 선 위 + 선 상승 · 🟡 하나만 · 🔴 선 아래 + 선 하락. "
                   "지수 RS = 보드 종목과 같은 잣대의 1~99. 해외는 야후(15분 지연), 유가는 근월물.")
    render_flows()


def _chip_leader(df: pd.DataFrame, group: str) -> str:
    top = df[(df["group"] == group) & (df["lead_rank"] == 1) & (df["lead_ok"] == True)]  # noqa: E712
    if top.empty:
        return ""
    return f'<b style="color:#F2B134">👑 대장 {html.escape(top["name"].iloc[0])}</b><br>'


def render_radar(df: pd.DataFrame):
    leaders = data.leading_groups(df, recent_days, min_count)
    if leaders:
        body = "".join(
            f'<div class="chip"><div class="chip-top">'
            f'<span class="chip-group">{html.escape(g)}</span>'
            f'<span class="chip-count">{len(sub)}<small>종목</small></span></div>'
            f'<div class="chip-names">{_chip_leader(df, g)}{html.escape(", ".join(sub["name"]))}</div></div>'
            for g, sub in leaders
        )
    else:
        body = ('<div class="radar-empty">지금은 조건에 맞는 분류가 없어요. '
                '왼쪽에서 신고가 인정 기간을 늘리거나 종목 수 기준을 낮춰 보세요.</div>')
    st.markdown(
        f'<div class="radar"><div class="radar-title">주도섹터 레이더</div>'
        f'<div class="radar-rule">최근 {recent_days}거래일 52주 신고가 {min_count}종목 이상</div>'
        f'<div class="chips">{body}</div></div>',
        unsafe_allow_html=True,
    )


def fmt_vol(v) -> str:
    """거래량 → '449만' / '3.5만' / '1.2억' / '8,512'."""
    if v is None or pd.isna(v):
        return "-"
    if v >= 1e8:
        return f"{v / 1e8:,.1f}억"
    if v >= 1e5:
        return f"{v / 1e4:,.0f}만"
    if v >= 1e4:
        return f"{v / 1e4:,.1f}만"
    return f"{v:,.0f}"


def _x_color(v) -> str:
    if v is None or pd.isna(v):
        return ""
    if v >= 2:
        return f"color: {UP}; font-weight: 800"
    if v >= 1.3:
        return f"color: {UP}; font-weight: 600"
    if v < 0.7:
        return f"color: {DOWN}"
    return ""


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
    if r.get("bo_status") in ("유지", "이탈"):
        return f"{r['bo_status']} {int(r['bo_days'])}일"
    return None


def _bo_full(r) -> str:
    """돌파 상태 한 줄: '⭐ 첫 돌파 3일째' / '2번째 돌파 5일째' / '미돌파 · 이탈 4일'."""
    get = r.get if isinstance(r, dict) else (lambda k, d=None: getattr(r, k, d))
    st_, days, nth = get("bo_status"), get("bo_days"), get("bo_nth")
    if days is None or pd.isna(days):
        return "-"
    if st_ == "유지":
        if nth is not None and not pd.isna(nth) and int(nth) > 1:
            return f"{int(nth)}번째 돌파 {int(days)}일째"
        return f"⭐ 첫 돌파 {int(days)}일째"
    if st_ == "이탈":
        return f"미돌파 · 이탈 {int(days)}일"
    return "-"


BO_FULL_HELP = ("⭐ 첫 돌파 N일째 = 최근 52주 안에서 처음으로 종가가 직전 52주 최고가를 넘은 뒤 N거래일째(돌파일 = 1일째), "
                "그 뒤 한 번도 그 가격 아래에서 마감하지 않음. 2번째 돌파 = 52주 안에 한 번 돌파했다가 밀린 뒤 다시 돌파. "
                "미돌파 · 이탈 N일 = 지금의 52주 최고가 아래에서 마감한 지 N거래일")


def _bo_color(v):
    if not isinstance(v, str):
        return ""
    if v.startswith("⭐"):
        return f"color: {UP}; font-weight: 800"
    if "번째 돌파" in v:
        return f"color: {UP}; font-weight: 600"
    if v.startswith("미돌파"):
        return "color: #6B7A84"
    return ""


def _to_high_color(v):
    """신고가까지 남은 %: 3% 이내 진하게, 10% 이내 주황."""
    if v is None or pd.isna(v):
        return ""
    if v <= 3:
        return f"color: {UP}; font-weight: 800"
    if v <= 10:
        return "color: #C27C0E; font-weight: 700"
    return ""


def _nh_text(r, kind: str) -> str | None:
    """지금 봉이 신고가인 봉만 모아서 '일·주·월'처럼. 아무것도 아니면 None."""
    hit = [lab for k, lab in (("d", "일"), ("w", "주"), ("m", "월")) if r.get(f"{kind}_{k}") == True]  # noqa: E712
    return "·".join(hit) if hit else None


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
        "거래량": f.get("vol_live"),
        "거래대금": f.get("tv_live"),
        "평소 대비": f.get("tv_x"),
        **({"외국인(억원)": f["flow_외국인"], "기관(억원)": f["flow_기관"], "개인(억원)": f["flow_개인"],
            "외국인 5일(억원)": f["flow5_외국인"], "기관 5일(억원)": f["flow5_기관"],
            "개인 5일(억원)": f["flow5_개인"]} if show_flow else {}),
        "52주 최고": f["high52"],
        "괴리율": f["gap"],
        "신고가까지": f["to_high"],
        "52주 위치": f["pos"],
        "신고가 후": f["days_since_high"],
        "돌파": f.apply(_bo_full, axis=1),
        "52주 신고가(일·주·월)": f.apply(lambda r: _nh_text(r, "nh52"), axis=1),
        "역대 신고가(일·주·월)": f.apply(lambda r: _nh_text(r, "ath"), axis=1),
        "섹터 순위": f.apply(lambda r: "-" if pd.isna(r["lead_rank"]) else
                         (f"👑 1/{int(r['lead_n'])}" if r["lead_rank"] == 1 and r["lead_ok"] else f"{int(r['lead_rank'])}/{int(r['lead_n'])}"), axis=1),
        "RS": f["rs"],
        "RS(1M)": f["rs_1m"],
        "RS(3M)": f["rs_3m"],
        "RS(6M)": f["rs_6m"],
        "ATR%(20일)": f["atr_pct"],
        "정배열": f["aligned"],
        "설명": f["desc"],
    })

    simple = st.toggle("간단히 보기", key="tbl_simple", help="꼭 볼 열만: 신고가까지 · 돌파 · RS · 섹터 순위 · 5일 수급")
    if simple:
        keep = ["종목", "현재가", "등락률", "거래량", "평소 대비", "신고가까지", "돌파", "RS", "섹터 순위", "분류", "시가총액(원)",
                "외국인 5일(억원)", "기관 5일(억원)", "52주 위치"]
        view = view[[c for c in keep if c in view.columns]]
    hot_mask = (pd.to_numeric(f["days_since_high"], errors="coerce") <= recent_days).values
    whole_rows = view.index[f["currency"].isin(["KRW", "JPY"]).values]
    flow_cols = [c for c in view.columns if c.endswith("(억원)")]
    for c in flow_cols:
        view[c] = pd.to_numeric(view[c], errors="coerce")

    def color_sign(v):
        if pd.isna(v) or v == 0:
            return ""
        return f"color: {UP}; font-weight: 600" if v > 0 else f"color: {DOWN}; font-weight: 600"

    pos_of = {ix: i for i, ix in enumerate(view.index)}

    def mark_new_high(row):
        hit = bool(hot_mask[pos_of[row.name]])
        return [f"background-color: {NEW_HIGH_BG}" if hit else ""] * len(row)

    _fmt = {
            "현재가": "{:,.2f}", "52주 최고": "{:,.2f}",
            "등락률": "{:+.2f}%", "괴리율": "{:.1f}%", "신고가까지": "{:+.1f}%",
            "시가총액(원)": data.format_krw,
            "신고가 후": "{:.0f}일", "52주 위치": "{:.0f}",
            "RS": "{:.0f}", "RS(1M)": "{:.0f}", "RS(3M)": "{:.0f}", "RS(6M)": "{:.0f}",
            "ATR%(20일)": "{:.1f}%",
            "거래량": fmt_vol, "거래대금": data.format_krw, "평소 대비": "{:.1f}배",
        }
    styled = (
        view.style
        .format({k: v for k, v in _fmt.items() if k in view.columns}, na_rep="-")
        .format("{:,.0f}", subset=pd.IndexSlice[whole_rows, [c for c in ("현재가", "52주 최고") if c in view.columns]], na_rep="-")
        .format(_eok_num, subset=flow_cols, na_rep="-")
        .map(color_sign, subset=["등락률"] + flow_cols)
        .map(lambda _: "font-weight: 600", subset=["종목", "RS"])
        .map(_rs_color, subset=[c for c in ("RS", "RS(1M)", "RS(3M)", "RS(6M)") if c in view.columns])
        .map(_bo_color, subset=["돌파"])
        .map(_to_high_color, subset=["신고가까지"])
        .map(_x_color, subset=[c for c in ("평소 대비",) if c in view.columns])
        .map(lambda v: f"color: {UP}; font-weight: 700" if isinstance(v, str) else "",
             subset=[c for c in ("52주 신고가(일·주·월)", "역대 신고가(일·주·월)") if c in view.columns])
        .apply(mark_new_high, axis=1)
    )
    codes_in_view = list(f["code"])

    def _pick_row():
        sel = st.session_state.get("list_table")
        rows = getattr(getattr(sel, "selection", None), "rows", None) or (sel or {}).get("selection", {}).get("rows", [])
        if rows and rows[0] < len(codes_in_view):
            st.session_state["detail_code"] = codes_in_view[rows[0]]
            st.session_state["chart_code"] = codes_in_view[rows[0]]

    st.caption("👆 종목 줄을 누르면 아래 '종목 자세히 보기'에 캔들 차트가 떠요.")
    st.dataframe(
        styled,
        hide_index=True,
        key="list_table", on_select=_pick_row, selection_mode="single-row",
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
            "종목": st.column_config.Column(pinned=True),
            "돌파": st.column_config.Column(help=BO_FULL_HELP),
            "52주 신고가(일·주·월)": st.column_config.Column(
                help="오늘 일봉·이번 주 주봉·이번 달 월봉 중 고가가 그 봉 직전 52주 최고가를 넘은 봉"),
            "역대 신고가(일·주·월)": st.column_config.Column(
                help="오늘 일봉·이번 주 주봉·이번 달 월봉 중 고가가 상장 이후 전체 최고가(월봉 기준)를 넘은 봉"),
            "RS": st.column_config.Column(help=RS_HELP),
            "RS(1M)": st.column_config.Column(help="단기 RS. 최근 1개월(21거래일) 수익률 순위(1~99)"),
            "RS(3M)": st.column_config.Column(help="최근 3개월(63거래일) 수익률 순위(1~99)"),
            "RS(6M)": st.column_config.Column(help="최근 6개월(126거래일) 수익률 순위(1~99)"),
            "ATR%(20일)": st.column_config.Column(help="최근 20거래일 평균 진폭 ÷ 현재가. 손절폭 잡을 때 참고(8%보다 크면 ATR로 확대)"),
            "설명": st.column_config.TextColumn(width="large"),
            **{c: st.column_config.Column(help=FLOW_HELP) for c in flow_cols},
        },
    )


def _n(v) -> str:
    return "-" if pd.isna(v) else f"{v:.0f}"


def _pct(v) -> str:
    return "-" if pd.isna(v) else f"{v:.1f}%"


CARD_STEP = 60   # 휴대폰 카드는 한 번에 60장씩(수백 장을 30초마다 다시 그리면 휴대폰이 버벅여요)


def _more_cards(limit: int):
    st.session_state["card_limit"] = limit + CARD_STEP


M_SORTS = ["신고가 가까운 순", "첫 돌파 우선", "RS 높은 순", "등락률 순"]


def _m_sorted(f: pd.DataFrame, how: str) -> pd.DataFrame:
    if how == "첫 돌파 우선":
        k = f.assign(_a=(f["bo_status"] != "유지").astype(int),
                     _b=pd.to_numeric(f.get("bo_nth"), errors="coerce").fillna(99),
                     _c=pd.to_numeric(f["bo_days"], errors="coerce").fillna(999))
        return k.sort_values(["_a", "_b", "_c", "to_high"]).drop(columns=["_a", "_b", "_c"])
    if how == "RS 높은 순":
        return f.sort_values("rs", ascending=False, na_position="last")
    if how == "등락률 순":
        return f.sort_values("change", ascending=False, na_position="last")
    return f.sort_values("to_high", ascending=True, na_position="last")


def _kflow(v) -> str:
    if v is None or pd.isna(v):
        return "-"
    color = UP if v > 0 else (DOWN if v < 0 else "#51616C")
    return f'<b style="color:{color}">{_eok_num(v)}</b>'


def render_cards(f: pd.DataFrame, n_hot: int, n_near: int, n_aligned: int, fins=None):
    """휴대폰 화면용 체크 리스트. 한 장에 꼭 볼 것만: 신고가까지 · 돌파 상태 · RS를 같은 자리에 크게."""
    kpis = (
        f'<div class="m-kpis"><div><span>종목</span><b>{len(f)}</b></div>'
        f'<div><span>최근 {recent_days}일 신고가</span><b>{n_hot}</b></div>'
        f'<div><span>10% 이내</span><b>{n_near}</b></div>'
        f'<div><span>돌파 유지</span><b>{int((f["bo_status"] == "유지").sum())}</b></div></div>'
    )
    st.markdown(kpis, unsafe_allow_html=True)
    how = st.radio("정렬", M_SORTS, horizontal=True, key="m_sort", label_visibility="collapsed")
    shown = _m_sorted(f[f["price"].notna()], how)
    limit = st.session_state.get("card_limit", CARD_STEP)
    total = len(shown)
    yr = data.now_kst().year
    cards = []
    for r in shown.head(limit).itertuples():
        hot = pd.notna(r.days_since_high) and r.days_since_high <= recent_days
        if pd.isna(r.change) or r.change == 0:
            chg = f'<span class="k-chg">{"-" if pd.isna(r.change) else "0.00%"}</span>'
        else:
            chg = f'<span class="k-chg" style="color:{UP if r.change > 0 else DOWN}">{r.change:+.2f}%</span>'
        th = r.to_high
        th_cls = "" if pd.isna(th) else ("hot" if th <= 3 else ("near" if th <= 10 else ""))
        bo = _bo_full(r)
        bo_cls = "first" if bo.startswith("⭐") else ("brk" if "번째 돌파" in bo else "off")
        bo_show = bo.replace("미돌파 · ", "") if bo != "-" else "돌파 전"
        crown = ' <span class="k-crown">👑 대장</span>' if (r.lead_rank == 1 and r.lead_ok) else ""
        sub = [html.escape(r.group)]
        if pd.notna(r.cap_krw):
            sub.append(f"{data.format_krw(r.cap_krw)}원")
        if pd.notna(r.lead_rank):
            sub.append(f"섹터 {int(r.lead_rank)}/{int(r.lead_n)}")
        if fins is not None and data.is_kr(r.code):
            og = data.op_growth(fins.get(r.code), yr)
            color = UP if og["op_up"] else ("#6B7A84" if og["op_up"] is None else DOWN)
            sub.append(f'<b style="color:{color}">{og["op_txt"]}</b>')
        if r.aligned:
            sub.append("정배열")
        ath = _nh_text(r._asdict(), "ath")
        if ath:
            sub.append(f'<b style="color:{UP}">역대 신고가 {ath}</b>')
        flow = ""
        if show_flow and pd.notna(getattr(r, "flow_date", None)):
            flow = (f'<div class="k-flow">외 {_kflow(r.flow_외국인)} · 기 {_kflow(r.flow_기관)}'
                    f'<span>5일 외 {_kflow(r.flow5_외국인)} · 기 {_kflow(r.flow5_기관)}</span></div>')
        cards.append(
            f'<a class="kcard{" hot" if hot else ""}" target="_self" href="?chart={r.code}">'
            f'<div class="k-top"><span class="k-name">{html.escape(r.name)}{crown}</span>'
            f'<span class="k-px">{fmt_price(r.price, r.currency)}{"" if r.currency == "KRW" else " " + r.currency} {chg}</span></div>'
            f'<div class="k-keys">'
            f'<div><small>신고가까지</small><b class="{th_cls}">{"-" if pd.isna(th) else f"{th:+.1f}%"}</b></div>'
            f'<div><small>돌파</small><b class="{bo_cls}">{html.escape(bo_show)}</b></div>'
            f'<div><small>RS</small><b class="{"rs" if pd.notna(r.rs) and r.rs >= 80 else ""}">{_n(r.rs)}</b></div>'
            f'</div>'
            f'<div class="k-sub">{" · ".join(sub)}</div>{flow}</a>'
        )
    st.markdown('<div class="kcards">' + "".join(cards) + "</div>", unsafe_allow_html=True)
    if total > limit:
        st.button(f"카드 더 보기 ({limit}/{total})", key="card_more", on_click=_more_cards, args=(limit,))
    st.caption("노란 카드 = 최근 신고가. 신고가까지: 빨강 3% 이내 · 주황 10% 이내. "
               "⭐ 첫 돌파 = 52주 안 첫 돌파(며칠째인지 함께). 카드를 누르면 종목 화면이 열려요. 수급은 억원.")


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


@st.cache_data(ttl=300, show_spinner=False, max_entries=300)
def load_chart_all(code: str) -> dict:
    """(속도) 일·주·월봉을 한 번에 동시에 받아 둬요. 봉 종류를 바꿔도 다시 기다리지 않아요."""
    from concurrent.futures import ThreadPoolExecutor
    tfs = list(data.CHART_TF)
    with ThreadPoolExecutor(max_workers=3) as pool:
        return dict(zip(tfs, pool.map(lambda tf: data.fetch_chart(code, tf), tfs)))


def load_chart(code: str, tf: str) -> pd.DataFrame:
    return load_chart_all(code).get(tf, data.empty_frame())


MA_SET = {"일봉": ((5, "5일"), (20, "20일"), (60, "60일"), (120, "120일")),
          "주봉": ((5, "5주"), (10, "10주"), (30, "30주"), (52, "52주")),
          "월봉": ((3, "3개월"), (6, "6개월"), (12, "12개월"), (24, "24개월"))}
MA_COLORS = ("#E0672B", "#2E9D5B", "#7A4FD1", "#8A96A0")
PERIODS = {"일봉": {"1개월": 21, "3개월": 63, "6개월": 126, "1년": 250, "2년": 500, "전체": None},
           "주봉": {"1년": 52, "2년": 104, "5년": 260, "전체": None},
           "월봉": {"5년": 60, "10년": 120, "전체": None}}


def candle_fig(b: pd.DataFrame, tf: str, n: int | None, show_ma: bool, show_vol: bool, line: float | None = None,
               currency: str = "KRW", show_tv: bool = False):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    b = b.copy().reset_index(drop=True)
    for k, _ in MA_SET[tf]:
        b[f"ma{k}"] = b["close"].rolling(k).mean()
    if n:
        b = b.tail(n)
    x = pd.to_datetime(b["date"]).dt.strftime("%Y-%m" if tf == "월봉" else "%Y-%m-%d")
    panels = ["price"] + (["vol"] if show_vol else []) + (["tv"] if show_tv else [])
    heights = {1: [1.0], 2: [0.78, 0.22], 3: [0.68, 0.16, 0.16]}[len(panels)]
    fig = make_subplots(rows=len(panels), cols=1, shared_xaxes=True, vertical_spacing=0.025, row_heights=heights)
    up_mask = [c >= o for o, c in zip(b["open"], b["close"])]
    fmt = ",.0f" if currency in ("KRW", "JPY") else ",.2f"
    fig.add_trace(go.Candlestick(
        x=x, open=b["open"], high=b["high"], low=b["low"], close=b["close"], name="봉", showlegend=False,
        increasing=dict(line=dict(color=UP, width=1), fillcolor=UP),
        decreasing=dict(line=dict(color=DOWN, width=1), fillcolor=DOWN),
        hoverlabel=dict(namelength=0)), row=1, col=1)
    if show_ma:
        for (k, lab), c in zip(MA_SET[tf], MA_COLORS):
            fig.add_trace(go.Scatter(x=x, y=b[f"ma{k}"], name=lab, mode="lines", line=dict(color=c, width=1.3),
                                     hovertemplate=f"{lab} %{{y:{fmt}}}<extra></extra>"), row=1, col=1)
    if line and tf != "월봉":
        fig.add_hline(y=line, line=dict(color="#F2B134", width=1.4, dash="dash"), row=1, col=1,
                      annotation_text=f"52주 최고 {line:{fmt}}", annotation_position="top left",
                      annotation_font=dict(size=11, color="#B7861D"))
    colors = [UP if u else DOWN for u in up_mask]
    fig.update_yaxes(side="right", gridcolor="#EEF1F3", zeroline=False, tickfont=dict(size=12))
    fig.update_yaxes(tickformat=fmt, row=1, col=1)
    if show_vol:
        r = panels.index("vol") + 1
        fig.add_trace(go.Bar(x=x, y=b["volume"], marker_color=colors, opacity=0.55, name="거래량", showlegend=False,
                             hovertemplate="거래량 %{y:,.0f}<extra></extra>"), row=r, col=1)
        fig.update_yaxes(title_text="거래량", title_font=dict(size=12, color="#7A8A94"), tickformat="~s", row=r, col=1)
    if show_tv:
        r = panels.index("tv") + 1
        # 거래대금 ≈ 평균가((고+저+종)/3) × 거래량. 국내는 억원, 해외는 백만(현지 통화)
        typ = (b["high"] + b["low"] + b["close"]) / 3
        krw = currency == "KRW"
        tv = typ * b["volume"] / (1e8 if krw else 1e6)
        unit = "억" if krw else f"M {UNIT.get(currency, currency)}"
        fig.add_trace(go.Bar(x=x, y=tv, marker_color=colors, opacity=0.75, name="거래대금", showlegend=False,
                             hovertemplate=f"거래대금 %{{y:,.0f}}{unit}<extra></extra>"), row=r, col=1)
        fig.update_yaxes(title_text=f"거래대금({unit})", title_font=dict(size=12, color="#7A8A94"), tickformat=",.0f",
                         row=r, col=1)
    fig.update_xaxes(type="category", nticks=8, showgrid=False, rangeslider_visible=False, tickangle=0,
                     tickfont=dict(size=12), showspikes=True, spikemode="across", spikethickness=1, spikecolor="#9AA9B3")
    fig.update_layout(height={1: 640, 2: 780, 3: 900}[len(panels)], margin=dict(l=4, r=4, t=36, b=6),
                      hovermode="x unified", dragmode="pan", plot_bgcolor="#FFFFFF", paper_bgcolor="#FFFFFF",
                      hoverlabel=dict(font_size=13),
                      legend=dict(orientation="h", y=1.05, x=0, font=dict(size=13)),
                      font=dict(family="Pretendard, Malgun Gothic, sans-serif", size=13))
    return fig


def candle_chart(code: str, currency: str = "KRW", quote: dict | None = None, high52: float | None = None,
                 key: str = "cc"):
    """일봉·주봉·월봉 캔들 + 이동평균 + 거래량. 국내 종목은 실시간 시세로 마지막 봉을 갱신해요."""
    with st.container(key=f"{key}_bar"):
        c1, c2, c3 = st.columns([1.0, 2.0, 1.6], gap="medium")
        tf = c1.segmented_control("봉 종류", list(data.CHART_TF), default="일봉", required=True, key=f"{key}_tf",
                                  width="stretch") or "일봉"
        periods = PERIODS[tf]
        default_p = {"일봉": "6개월", "주봉": "2년", "월봉": "10년"}[tf]
        per = c2.segmented_control("기간", list(periods), default=default_p, required=True, key=f"{key}_per_{tf}",
                                   width="stretch") or default_p
        opts = c3.segmented_control("표시", ["이동평균", "거래량", "거래대금"], selection_mode="multi",
                                    default=["이동평균", "거래량", "거래대금"], key=f"{key}_opts2", width="stretch") or []
    with st.spinner("차트를 불러오는 중이에요."):
        bars = load_chart(code, tf)
    if bars is None or bars.empty:
        st.info("이 종목의 봉 데이터를 받지 못했어요. 잠시 뒤 다시 시도해 보세요.")
        return
    bars = data.chart_with_live(bars, quote, tf)
    fig = candle_fig(bars, tf, periods[per], "이동평균" in opts, "거래량" in opts, high52, currency, "거래대금" in opts)
    st.plotly_chart(fig, key=f"{key}_fig", config={"displaylogo": False, "scrollZoom": True,
                                                   "modeBarButtonsToRemove": ["select2d", "lasso2d", "autoScale2d"]})
    st.caption("드래그로 옮기고, 휠·두 손가락으로 확대해요. 더블클릭하면 원래대로. 거래대금은 평균가×거래량으로 계산한 추정치예요. "
               + ("마지막 봉은 실시간 시세로 갱신돼요." if quote else "해외 종목은 야후 일봉 기준이에요."))


@st.cache_data(ttl=3600, show_spinner=False)
def naver_search(q: str) -> list[tuple[str, str]]:
    try:
        return data.search_stock(q)[:12]
    except Exception:
        return []


def _rep_status():
    key, token = _secret("ANTHROPIC_API_KEY"), _secret("GITHUB_TOKEN")
    repo = _secret("GITHUB_REPO", "kimgun124-boop/very")
    return {"ai": bool(key), "key": key, "model": _secret("ANTHROPIC_MODEL", reports.DEFAULT_MODEL),
            "gh": bool(token), "token": token, "repo": repo, "branch": _secret("GITHUB_BRANCH") or None,
            "pw": _secret("APP_PASSWORD")}


def _persist(store: dict, stt: dict) -> tuple[bool, str]:
    reports.save_local(store)
    if stt["gh"]:
        return reports.save_github(store, stt["token"], stt["repo"], stt["branch"])
    return True, "이 서버에만 임시로 저장했어요(앱을 다시 켜면 사라질 수 있어요)."


@st.fragment
def render_reports_tab():
    """📥 리포트: 앱에서 직접 리포트를 넣으면 종목을 뽑아 보드에 붙여요."""
    stt = _rep_status()
    board = {s["code"]: s for s in STOCKS}
    by_name = {s["name"]: s for s in STOCKS}

    st.markdown('<div class="sec-h"><b>📥 리포트 넣기</b><span>PDF·캡처 이미지·글을 넣으면 종목을 뽑아 보드에 붙여요</span></div>',
                unsafe_allow_html=True)
    badges = [("AI 분석", stt["ai"], "켜짐" if stt["ai"] else "꺼짐 · 이름 맞추기로 분석"),
              ("영구 저장", stt["gh"], "GitHub 저장소" if stt["gh"] else "꺼짐 · 앱 재시작 시 사라질 수 있음"),
              ("비밀번호", bool(stt["pw"]), "설정됨" if stt["pw"] else "없음 · 누구나 추가 가능")]
    st.markdown('<div class="rp-badges">' + "".join(
        f'<span class="rp-badge {"on" if ok else "off"}"><b>{lab}</b> {txt}</span>' for lab, ok, txt in badges) + "</div>",
        unsafe_allow_html=True)
    with st.expander("⚙️ 처음 한 번 설정하기 (AI 분석 · 영구 저장 · 비밀번호)", expanded=not (stt["ai"] and stt["gh"])):
        st.markdown(
            "Streamlit 앱 오른쪽 아래 **Manage app → ⋮ → Settings → Secrets** 칸에 아래처럼 넣고 저장하세요.\n\n"
            "```toml\nANTHROPIC_API_KEY = \"sk-ant-...\"   # AI 분석(이미지·스캔 PDF도 읽음)\n"
            "GITHUB_TOKEN = \"github_pat_...\"     # 넣은 리포트를 저장소에 영구 저장\n"
            "GITHUB_REPO = \"kimgun124-boop/very\"\n"
            "APP_PASSWORD = \"원하는 비밀번호\"      # 나만 리포트를 넣을 수 있게\n```\n"
            "- **ANTHROPIC_API_KEY**: [Claude Console](https://console.anthropic.com)에서 발급해요. "
            "Claude 구독과 별도로 쓴 만큼 요금이 나가요. 없으면 '이름 맞추기'로만 분석해요(글자로 된 PDF·글만).\n"
            "- **GITHUB_TOKEN**: GitHub → Settings → Developer settings → Fine-grained tokens → "
            "저장소 very만 고르고 **Contents: Read and write** 권한으로 만들어요. 없으면 앱을 다시 켤 때 넣은 리포트가 사라질 수 있어요.\n"
            "- **APP_PASSWORD**: 앱 주소를 아는 누구나 리포트를 넣거나 API 요금을 쓰지 못하게 막아요. 꼭 넣는 걸 권해요.")

    if stt["pw"] and not st.session_state.get("rep_ok"):
        pw = st.text_input("비밀번호", type="password", key="rep_pw")
        if pw and pw == stt["pw"]:
            st.session_state["rep_ok"] = True
            st.rerun(scope="fragment")
        elif pw:
            st.error("비밀번호가 달라요.")
        _render_saved_reports(stt, editable=False)
        return

    # ① 넣기
    with st.container(border=True, key="rp_box_in"):
        st.markdown("**① 리포트 넣기**")
        files = st.file_uploader("파일", type=["pdf", "png", "jpg", "jpeg", "webp", "txt", "md", "html"],
                                 accept_multiple_files=True, key="rep_files",
                                 help="PDF 여러 개, 캡처 이미지 여러 장을 한꺼번에 넣어도 돼요.")
        text = st.text_area("또는 글 붙여넣기", height=110, key="rep_text",
                            placeholder="리포트 본문, 텔레그램 요약, 기사 내용 등을 붙여넣어도 돼요.")
        methods = (["🤖 AI로 분석"] if stt["ai"] else []) + ["🔎 이름 맞추기"]
        c1, c2 = st.columns([2, 1])
        method = c1.segmented_control("분석 방법", methods, default=methods[0], required=True, key="rep_method")
        go = c2.button("🔍 분석하기", type="primary", width="stretch", disabled=not (files or text.strip()))
        if not stt["ai"]:
            st.caption("AI 키가 없어서 본문에 나온 상장사 이름을 찾아요. 이미지·스캔 PDF는 AI 분석에서만 읽을 수 있어요.")
    if go:
        payload = [(f.name, f.getvalue()) for f in (files or [])]
        if method.startswith("🤖"):
            with st.spinner("AI가 리포트를 읽고 종목을 뽑는 중이에요. 분량에 따라 30초~2분 걸려요."):
                res, err = reports.ai_extract(stt["key"], payload, text, stt["model"])
            if err:
                st.error(err)
                return
            meta = {k: res.get(k, "") for k in ("title", "broker", "date", "summary")}
            rows = res.get("stocks") or []
        else:
            with st.spinner("본문에서 상장사 이름을 찾는 중이에요."):
                body = "\n".join(reports.file_text(n, raw) for n, raw in payload) + "\n" + text
                if not body.strip():
                    st.error("글자를 읽지 못했어요. 이미지·스캔 PDF는 AI 분석이 필요해요.")
                    return
                rows = reports.match_stocks(body, STOCKS, reports.krx_listing())
                first = (files[0].name.rsplit(".", 1)[0] if files else body.strip().split("\n")[0])[:30]
                meta = {"title": first, "broker": "", "date": now_date(), "summary": ""}
        draft = []
        need = [r.get("name") for r in rows if not r.get("code") and r.get("name") and r.get("name") not in by_name
                and (r.get("market") in (None, "", "KR"))]
        found, _miss = data.resolve_codes(tuple(need)) if need else ({}, {})
        for r in rows:
            name = str(r.get("name") or "").strip()
            code = str(r.get("code") or "").strip().upper() or found.get(name, "") or (by_name.get(name) or {}).get("code", "")
            if re.fullmatch(r"\d{1,6}", code):
                code = code.zfill(6)
            on = board.get(code)
            hint = r.get("sector_hint") or ""
            sector = on["sector"] if on else (hint if hint in SECTOR_ORDER else "기타(리포트 스크린)")
            draft.append({"넣기": r.get("importance", "상") != "하" or bool(on), "종목명": on["name"] if on else name,
                          "코드": code, "산업": sector, "세부 분류": on["group"] if on else (r.get("group") or "리포트 추가"),
                          "한 줄 설명": on["desc"] if on else (r.get("desc") or ""), "리포트 포인트": r.get("point") or "",
                          "보드에 있음": bool(on)})
        st.session_state["rep_draft"] = {"meta": meta, "rows": draft,
                                         "source": ", ".join(f.name for f in files or []) or "붙여넣은 글"}

    # ② 확인·고치기 → 반영
    dr = st.session_state.get("rep_draft")
    if dr:
        with st.container(border=True, key="rp_box_edit"):
            st.markdown(f"**② 뽑은 종목 확인하기** — {len(dr['rows'])}개. 틀린 건 고치고, 뺄 종목은 '넣기'를 끄세요.")
            c1, c2, c3 = st.columns([2.2, 1, 1])
            title = c1.text_input("리포트 제목", dr["meta"].get("title", ""), key="rep_title")
            broker = c2.text_input("증권사·출처", dr["meta"].get("broker", ""), key="rep_broker")
            rdate = c3.text_input("날짜", dr["meta"].get("date", ""), key="rep_date", placeholder="26.09.28")
            summary = st.text_area("핵심 요약", dr["meta"].get("summary", ""), height=80, key="rep_summary")
            edited = st.data_editor(
                pd.DataFrame(dr["rows"], columns=["넣기", "종목명", "코드", "산업", "세부 분류", "한 줄 설명", "리포트 포인트", "보드에 있음"]),
                key="rep_editor", hide_index=True, num_rows="dynamic", width="stretch",
                column_config={
                    "넣기": st.column_config.CheckboxColumn(width="small"),
                    "코드": st.column_config.TextColumn(help="국내 6자리 코드, 해외는 야후 티커(NVDA, 6857.T)", width="small"),
                    "산업": st.column_config.SelectboxColumn(options=SECTOR_ORDER, required=True),
                    "한 줄 설명": st.column_config.TextColumn(width="large"),
                    "리포트 포인트": st.column_config.TextColumn(width="large"),
                    "보드에 있음": st.column_config.CheckboxColumn(disabled=True, width="small"),
                })
            pick = edited[edited["넣기"] == True]  # noqa: E712
            no_code = pick[pick["코드"].fillna("").astype(str).str.strip() == ""]
            if len(no_code):
                st.warning("코드가 비어 있는 종목은 빠져요: " + ", ".join(no_code["종목명"].astype(str)) +
                           " — 코드를 채우면 들어가요(차트 탭에서 이름으로 검색하면 코드를 알 수 있어요).")
            n_new = int((~pick["보드에 있음"].fillna(False).astype(bool)).sum()) - len(no_code)
            c1, c2 = st.columns([3, 1])
            c1.caption(f"넣을 종목 {len(pick) - len(no_code)}개 (새 종목 {max(n_new, 0)}개 · 이미 있는 종목은 태그와 메모만 붙어요)")
            if c2.button("✅ 앱에 반영", type="primary", width="stretch", disabled=not title.strip()):
                stocks_out = [{"name": str(r["종목명"]).strip(), "code": str(r["코드"]).strip(), "sector": r["산업"],
                               "group": str(r["세부 분류"] or "리포트 추가").strip(), "desc": str(r["한 줄 설명"] or "").strip(),
                               "point": str(r["리포트 포인트"] or "").strip()}
                              for _, r in pick.iterrows() if str(r["코드"] or "").strip()]
                store = reports.load_local()
                store["reports"].append(reports.new_report(title, broker, rdate, summary, stocks_out, dr["source"]))
                ok, msg = _persist(store, stt)
                (st.success if ok else st.error)(msg + (" 보드에 반영하는 중이에요…" if ok else ""))
                if ok:
                    st.session_state.pop("rep_draft", None)
                    st.session_state["rep_saved_msg"] = f"'{title}' 리포트 {len(stocks_out)}종목을 보드에 넣었어요. " \
                                                        "왼쪽 '리포트 태그'에서 골라 볼 수 있어요."
                    time.sleep(0.6)
                    st.rerun(scope="app")
            if st.button("취소", key="rep_cancel"):
                st.session_state.pop("rep_draft", None)
                st.rerun(scope="fragment")
    if st.session_state.get("rep_saved_msg"):
        st.success(st.session_state.pop("rep_saved_msg"))
    _render_saved_reports(stt, editable=True)


# ─────────────────────────── 💼 내 보유 ───────────────────────────
@st.cache_data(ttl=600, show_spinner=False, max_entries=200)
def load_evidence(code: str, name: str) -> dict:
    if data.MOCK:
        return holdings.mock_gather(code, name)
    return holdings.gather(code, name, data.is_kr(code))


def _persist_holdings(store: dict, stt: dict) -> tuple[bool, str]:
    holdings.save_local(store)
    if stt["gh"]:
        return reports.save_github(store, stt["token"], stt["repo"], stt["branch"], path="user_holdings.json")
    return True, "이 서버에만 임시로 저장했어요(앱을 다시 켜면 사라질 수 있어요)."


def _hold_context(df: pd.DataFrame) -> dict:
    hist = load_indexes()
    parts, oks = [], []
    for name, sym in data.MARKETS.items():
        sm = data.index_summary(hist.get(sym, (data.empty_frame(), None))[0])
        ok = None if not sm or sm.get("above60") is None else bool(sm["above60"])
        oks.append(ok)
        parts.append(f"{name} " + ("-" if ok is None else f"60일선 {'위' if ok else '아래'} {sm['dist60']:+.1f}%"))
    sm_money = data.sector_money(df, "group") if "tv_live" in df else pd.DataFrame()
    return {"market_ok": None if None in oks else all(oks), "market_text": " · ".join(parts),
            "idx_rs": {n: data.index_rs(hist.get(sym, (data.empty_frame(), None))[0], df).get("rs")
                       for n, sym in data.MARKETS.items()},
            "leaders": {g for g, _ in data.leading_groups(df, recent_days, min_count)},
            "sector_x": dict(zip(sm_money["sector"], sm_money["x"])) if len(sm_money) else {}}


def _hold_metrics(code: str, df: pd.DataFrame, quotes: dict) -> tuple[dict, pd.DataFrame, dict | None]:
    """보드에 있으면 보드 지표를, 없으면 일봉으로 바로 계산해요."""
    quote = quotes.get(code)
    if quote is None and data.is_kr(code):
        quote = load_quotes((code,))[0].get(code)
    bars = load_chart(code, "일봉")
    bars_live = data.chart_with_live(bars, quote if data.is_kr(code) else None, "일봉")
    hit = df[df["code"] == code]
    if len(hit):
        return hit.iloc[0].to_dict(), bars_live, quote
    m = data.compute_metrics(bars, quote) if bars is not None and not bars.empty else {}
    return m, bars_live, quote


LEVEL_COLOR = {"red": ("#FDECEC", UP), "orange": ("#FDF1E7", "#D2691E"), "yellow": ("#FFF8E1", "#B7861D"),
               "green": ("#EEF8F2", "#1E7A45"), "blue": ("#EEF4FC", DOWN), "gray": ("#F1F4F6", "#51616C")}


@st.fragment
def render_holdings_tab(df: pd.DataFrame, quotes: dict):
    """💼 내 보유: 평단가를 넣으면 내 원칙으로 점검하고, 뉴스·리포트 근거를 모아 보여줘요."""
    stt = _rep_status()
    _sec_h("💼 내 보유 종목", "평단가를 넣으면 내 매매 원칙으로 점검해요 · 매수·매도 추천이 아니라 원칙 점검표예요")
    if stt["pw"] and not st.session_state.get("rep_ok"):
        pw = st.text_input("비밀번호(보유 종목은 비밀번호를 넣어야 보여요)", type="password", key="hold_pw")
        if pw and pw == stt["pw"]:
            st.session_state["rep_ok"] = True
            st.rerun(scope="fragment")
        elif pw:
            st.error("비밀번호가 달라요.")
        return
    if not stt["pw"]:
        st.warning("비밀번호(APP_PASSWORD)가 없어서 앱 주소를 아는 누구나 보유 종목을 볼 수 있어요. "
                   "📥 리포트 탭의 '처음 한 번 설정하기'대로 Secrets에 비밀번호를 넣어 주세요.")
    store = holdings.load()
    board_labels = {s["code"]: s["name"] for s in STOCKS}

    with st.expander("➕ 보유 종목 추가", expanded=not store["holdings"]):
        c1, c2 = st.columns([1.4, 1])
        q = c1.text_input("종목 이름이나 코드", key="hold_q", placeholder="예: 인텍플러스, 064290")
        cands = []
        if q.strip():
            ql = q.strip().lower()
            cands = [(c, n) for c, n in board_labels.items() if ql in n.lower() or c.lower().startswith(ql)][:10]
            if data.is_kr(q.strip()) and len(q.strip()) == 6 and q.strip() not in board_labels:
                cands.insert(0, (q.strip(), q.strip()))
            if len(cands) < 5:
                cands += [x for x in naver_search(q.strip()) if x[0] not in {c for c, _ in cands}][:8]
        pick = c2.selectbox("찾은 종목", cands, format_func=lambda t: f"{t[1]} ({t[0]})", key="hold_pick",
                            placeholder="먼저 왼쪽에 검색", index=0 if cands else None)
        c1, c2, c3, c4 = st.columns(4)
        avg = c1.number_input("평단가", min_value=0.0, step=100.0, format="%.2f", key="hold_avg")
        qty = c2.number_input("수량(선택)", min_value=0.0, step=1.0, format="%.0f", key="hold_qty")
        bdate = c3.date_input("매수일", value=data.now_kst().date(), key="hold_date")
        stop = c4.number_input("손절 %(비우면 8% 또는 ATR)", min_value=0.0, max_value=50.0, step=0.5, value=0.0, key="hold_stop")
        c1, c2 = st.columns([3, 1])
        memo = c1.text_input("메모(선택)", key="hold_memo", placeholder="예: 후공정 섹터 2번째 돌파 진입")
        half = c1.checkbox("이미 절반 익절함(손절선을 본전으로)", key="hold_half")
        if c2.button("저장", type="primary", width="stretch", disabled=not (pick and avg > 0)):
            store["holdings"].append(holdings.new_holding(pick[0], pick[1], avg, qty, str(bdate), stop or None, half, memo))
            ok, msg = _persist_holdings(store, stt)
            (st.success if ok else st.error)(msg)
            st.rerun(scope="fragment")

    if not store["holdings"]:
        st.info("아직 넣은 보유 종목이 없어요. 위에서 종목과 평단가를 넣어 주세요.")
        return

    ctx = _hold_context(df)
    rows = []
    for h in store["holdings"]:
        m, bars, quote = _hold_metrics(h["code"], df, quotes)
        rows.append((h, m, bars, holdings.judge(h, m, bars, ctx)))

    # 요약
    tot_cost = sum(h["avg"] * h["qty"] for h, *_ in rows if h.get("qty"))
    tot_val = sum((_m.get("price") or 0) * h["qty"] for h, _m, *_ in rows if h.get("qty"))
    cnt = {}
    for *_, j in rows:
        cnt[j["verdict"]] = cnt.get(j["verdict"], 0) + 1
    c = st.columns(4)
    c[0].metric("보유 종목", f"{len(rows)}개")
    if tot_cost:
        c[1].metric("평가손익", f"{tot_val - tot_cost:+,.0f}원", f"{(tot_val / tot_cost - 1) * 100:+.2f}%")
    c[2].metric("손절·정리 신호", f"{sum(v for k, v in cnt.items() if k.startswith(('🛑', '📉', '⚠️')))}개")
    c[3].metric("시장", "60일선 위" if ctx["market_ok"] else ("60일선 아래" if ctx["market_ok"] is False else "-"),
                ctx["market_text"], delta_color="off")

    order = {"red": 0, "orange": 1, "blue": 2, "yellow": 3, "green": 4, "gray": 5}
    for h, m, bars, j in sorted(rows, key=lambda x: order.get(x[3]["level"], 9)):
        bg, fg = LEVEL_COLOR.get(j["level"], LEVEL_COLOR["gray"])
        cur = m.get("currency") or ("KRW" if data.is_kr(h["code"]) else "USD")
        price = m.get("price")
        pnl_col = UP if (j["pnl"] or 0) > 0 else DOWN
        with st.container(border=True, key=f"hold_card_{h['id']}"):
            st.markdown(
                f'<div class="hd-top"><div><b class="hd-name">{html.escape(h["name"])}</b>'
                f'<span class="hd-code">{h["code"]}</span>'
                f'<span class="hd-px">{fmt_price(price, cur)}</span>'
                f'<span class="hd-pnl" style="color:{pnl_col}">{(j["pnl"] or 0):+.2f}% · {(j["r"] or 0):+.2f}R</span></div>'
                f'<span class="hd-verdict" style="background:{bg};color:{fg};border-color:{fg}">{j["verdict"]}</span></div>'
                f'<div class="hd-sub">평단 {fmt_price(h["avg"], cur)}'
                + (f' · {h["qty"]:,.0f}주 · 평가손익 {(price - h["avg"]) * h["qty"]:+,.0f}' if h.get("qty") and price else "")
                + f' · 손절선 {fmt_price(j["stop_price"], cur)}(여유 {(j["stop_dist"] or 0):.1f}%)'
                + f' · 3R 목표 {fmt_price(j["target3r"], cur)} · 매수일 {h.get("buy_date", "-")}'
                + (f' · {html.escape(h["memo"])}' if h.get("memo") else "") + "</div>",
                unsafe_allow_html=True)
            icon = {"good": "✅", "warn": "⚠️", "bad": "🛑", "info": "ℹ️"}
            st.markdown('<div class="hd-checks">' + "".join(
                f'<div class="hd-chk {st_}"><span>{icon[st_]} {html.escape(item)}</span><em>{html.escape(txt)}</em></div>'
                for st_, item, txt in j["checks"]) + "</div>", unsafe_allow_html=True)
            st.markdown('<div class="hd-act"><b>원칙대로라면</b><ul>' + "".join(
                f"<li>{html.escape(a)}</li>" for a in j["actions"]) + "</ul></div>", unsafe_allow_html=True)

            t_news, t_chart = st.tabs(["📰 뉴스·리포트 근거", "🕯️ 차트"])
            with t_news:
                ev = load_evidence(h["code"], h["name"])
                if stt["ai"]:
                    k = f"hold_ai_{h['id']}"
                    if st.button("🤖 뉴스를 원칙과 연결해 요약", key=f"{k}_btn"):
                        with st.spinner("뉴스·리포트를 읽는 중이에요."):
                            txt, err = holdings.ai_news_summary(stt["key"], stt["model"], h["name"], j,
                                                                ev["news"] + ev["research"])
                        st.session_state[k] = txt or err
                    if st.session_state.get(k):
                        st.info(st.session_state[k])
                c1, c2 = st.columns([1.6, 1])
                with c1:
                    st.markdown("**최근 뉴스** (네이버 증권 · 구글 뉴스 모음)")
                    if ev["news"]:
                        st.markdown('<div class="hd-news">' + "".join(
                            f'<a href="{html.escape(x["url"])}" target="_blank"><span>{html.escape(x["title"])}</span>'
                            f'<em>{html.escape(x["source"])} · {html.escape(x["time"])} · {x["portal"]}</em></a>'
                            for x in ev["news"][:12]) + "</div>", unsafe_allow_html=True)
                    else:
                        st.caption("뉴스를 받지 못했어요.")
                with c2:
                    st.markdown("**증권사 리포트**")
                    if ev["research"]:
                        st.markdown('<div class="hd-news">' + "".join(
                            f'<a href="{html.escape(x["url"])}" target="_blank"><span>{html.escape(x["title"])}</span>'
                            f'<em>{html.escape(x["source"])} · {html.escape(x["time"])}</em></a>'
                            for x in ev["research"]) + "</div>", unsafe_allow_html=True)
                    else:
                        st.caption("최근 리포트가 없거나 받지 못했어요.")
                st.caption("뉴스는 10분마다 새로 모아요. 제목만으로는 판단이 틀릴 수 있으니 원문을 확인하세요.")
            with t_chart:
                candle_chart(h["code"], cur, quotes.get(h["code"]) if data.is_kr(h["code"]) else None,
                             m.get("high52"), key=f"hold_ch_{h['id']}")

    with st.expander("✏️ 보유 종목 고치기 · 지우기"):
        ed = st.data_editor(pd.DataFrame([{
            "종목명": h["name"], "코드": h["code"], "평단가": h["avg"], "수량": h.get("qty") or 0.0,
            "매수일": h.get("buy_date", ""), "손절%": h.get("stop_pct"), "절반 익절함": bool(h.get("half_taken")),
            "메모": h.get("memo", ""), "_id": h["id"]} for h in store["holdings"]]),
            key="hold_editor", hide_index=True, num_rows="dynamic", width="stretch",
            column_config={"_id": None, "코드": st.column_config.TextColumn(disabled=True),
                           "종목명": st.column_config.TextColumn(disabled=True),
                           "손절%": st.column_config.NumberColumn(help="비우면 8% 또는 ATR")})
        st.caption("줄을 지우려면 왼쪽 칸을 고르고 휴지통을 누른 뒤 저장하세요. 새 종목은 위 '보유 종목 추가'로 넣어요.")
        if st.button("변경 저장", key="hold_save"):
            by_id = {h["id"]: h for h in store["holdings"]}
            new = []
            for _, r in ed.iterrows():
                h = by_id.get(r.get("_id"))
                if not h:
                    continue
                sp = r.get("손절%")
                h.update(avg=float(r["평단가"]), qty=float(r["수량"] or 0), buy_date=str(r["매수일"] or ""),
                         stop_pct=float(sp) if sp == sp and sp not in (None, "", 0) else None,
                         half_taken=bool(r["절반 익절함"]), memo=str(r["메모"] or ""))
                new.append(h)
            store["holdings"] = new
            ok, msg = _persist_holdings(store, stt)
            (st.success if ok else st.error)(msg)
            st.rerun(scope="fragment")
    if stt["gh"]:
        st.caption("보유 종목은 GitHub 저장소의 user_holdings.json에 저장돼요. 저장소가 공개(Public)면 누구나 볼 수 있으니 "
                   "GitHub에서 저장소를 비공개(Private)로 바꿔 두세요.")


def now_date() -> str:
    return data.now_kst().strftime("%y.%m.%d")


def _render_saved_reports(stt: dict, editable: bool):
    store = reports.load_local()
    reps = list(reversed(store.get("reports", [])))
    st.markdown(f'<div class="sec-h"><b>📚 넣은 리포트</b><span>{len(reps)}개</span></div>', unsafe_allow_html=True)
    if not reps:
        st.caption("아직 앱에서 넣은 리포트가 없어요.")
        return
    for rep in reps:
        with st.expander(f"{reports.tag_name(rep)} · {len(rep.get('stocks', []))}종목 · {rep.get('added_at', '')}"):
            if rep.get("summary"):
                st.markdown(rep["summary"])
            st.dataframe(pd.DataFrame(rep.get("stocks", [])).rename(columns={
                "name": "종목명", "code": "코드", "sector": "산업", "group": "세부 분류", "desc": "한 줄 설명", "point": "리포트 포인트"}),
                hide_index=True, width="stretch")
            if editable and st.button("🗑️ 이 리포트 지우기", key=f"rep_del_{rep['id']}"):
                store["reports"] = [r for r in store["reports"] if r.get("id") != rep["id"]]
                ok, msg = _persist(store, stt)
                (st.success if ok else st.error)(msg)
                if ok:
                    time.sleep(0.6)
                    st.rerun(scope="app")


@st.fragment
def render_chart_tab(df: pd.DataFrame, quotes: dict):
    """(속도) 이 탭 안에서 검색·버튼을 눌러도 이 칸만 다시 그려요. 예전엔 보드 전체(600종목 계산·모든 탭)를 다시 돌려서 느렸어요."""
    _render_chart_tab(df, quotes)


def _render_chart_tab(df: pd.DataFrame, quotes: dict):
    """🕯️ 차트: 종목을 검색해서 일봉·주봉·월봉 캔들로 봐요."""
    board = df.drop_duplicates("code")
    labels = {r.code: f"{r.name} · {r.code} · {'한국' if data.is_kr(r.code) else r.market}" for r in board.itertuples()}
    codes = list(labels)
    mine = [c for c in codes if _mkt(c) == market]
    ordered = mine + [c for c in codes if c not in mine]
    names = st.session_state.setdefault("chart_names", {})
    want = st.session_state.get("chart_code")
    if want in ordered and st.session_state.get("chart_pick") != want:
        st.session_state["chart_pick"] = want          # 표·카드에서 고른 종목으로 맞춰요
    if st.session_state.get("chart_pick") not in ordered:
        st.session_state.pop("chart_pick", None)

    with st.container(key="chart_search"):
        c1, c2 = st.columns([1.35, 1], gap="medium")
        q = c1.text_input("🔍 종목 검색", placeholder="종목 이름이나 코드를 치고 엔터 — 예: 삼성, 토마토, 035720",
                          key="chart_q").strip()
        c2.selectbox("📋 보드 종목에서 고르기", ordered, format_func=labels.get, key="chart_pick",
                     on_change=lambda: st.session_state.update(chart_code=st.session_state["chart_pick"]))
        if q:
            ql = q.lower()
            res = [(r.code, r.name) for r in board.itertuples()
                   if ql in str(r.name).lower() or str(r.code).lower().startswith(ql)]
            on_board = {c for c, _ in res}
            if data.is_kr(q) and len(q) == 6 and q not in on_board:
                res.insert(0, (q, q))
            for c, nm in naver_search(q):
                if c not in on_board and c not in {x for x, _ in res}:
                    res.append((c, nm))
            res = res[:18]
            if res:
                names.update({c: nm for c, nm in res})
                opts = [c for c, _ in res]
                st.pills(f"관련 종목 {len(res)}개", opts, key=f"chart_res_{q}",
                         format_func=lambda c: f"{'★ ' if c in on_board else ''}{names.get(c, c)}",
                         on_change=lambda k=f"chart_res_{q}": st.session_state.update(
                             chart_code=st.session_state[k]) if st.session_state.get(k) else None,
                         help="★ = 보드에 넣어 둔 종목. 누르면 아래에 차트가 떠요.")
            else:
                st.caption("검색 결과가 없어요. 이름을 조금 짧게 쓰거나 6자리 코드를 넣어 보세요.")
    code = st.session_state.get("chart_code") or st.session_state.get("chart_pick") or (ordered[0] if ordered else None)
    if code is None:
        return
    name, currency, row = (labels.get(code) or "").split(" · ")[0] or names.get(code, code), "KRW", None
    st.session_state["chart_code"] = code
    hit = board[board["code"] == code]
    if len(hit):
        row = hit.iloc[0]
        currency = row["currency"]
    quote = quotes.get(code)
    if quote is None and data.is_kr(code) and row is None:
        got, _ = load_quotes((code,))
        quote = got.get(code)
    unit = UNIT.get(currency, currency)
    price = (quote or {}).get("price") or (row["price"] if row is not None else None)
    prev = (quote or {}).get("prev")
    chg = (price / prev - 1) * 100 if price and prev else (row["change"] if row is not None else None)
    col = UP if (chg or 0) > 0 else (DOWN if (chg or 0) < 0 else "#51616C")
    sub = []
    if row is not None:
        if pd.notna(row.get("to_high")):
            sub.append(f"52주 최고까지 {row['to_high']:+.1f}%")
        if pd.notna(row.get("rs")):
            sub.append(f"RS {row['rs']:.0f}")
        sub.append(str(row["group"]))
    st.markdown(
        f'<div class="ch-head"><b>{html.escape(str(name))}</b><span class="ch-code">{html.escape(code)}</span>'
        f'<span class="ch-px" style="color:{col}">{fmt_price(price, currency)}{unit}'
        f'{f" <small>{chg:+.2f}%</small>" if chg is not None and pd.notna(chg) else ""}</span>'
        f'<span class="ch-sub">{" · ".join(html.escape(x) for x in sub)}</span></div>', unsafe_allow_html=True)
    candle_chart(code, currency, quote if data.is_kr(code) else None,
                 row["high52"] if row is not None and pd.notna(row.get("high52")) else None, key="tabchart")


@st.fragment
def render_detail(f: pd.DataFrame, histories: dict, trends: dict):
    """(속도) 종목을 바꾸거나 차트 버튼을 눌러도 이 칸만 다시 그려요."""
    _render_detail(f, histories, trends)


def _render_detail(f: pd.DataFrame, histories: dict, trends: dict):
    options = f[f["price"].notna()]
    if options.empty:
        return
    st.subheader("종목 자세히 보기")
    labels = {r.code: f"{r.name} ({r.code})" for r in options.itertuples()}
    if st.session_state.get("detail_code") not in labels:
        st.session_state.pop("detail_code", None)
    code = st.selectbox("종목", list(labels), format_func=labels.get, label_visibility="collapsed",
                        key="detail_code")
    row = options[options["code"] == code].iloc[0]

    c1, c2, c3, c4 = st.columns(4)
    unit = UNIT.get(row.currency, row.currency)
    c1.metric("현재가", f"{fmt_price(row.price, row.currency)}{unit}",
              f"{row.change:+.2f}%" if pd.notna(row.change) else None, delta_color="off")
    c2.metric("52주 최고", f"{fmt_price(row.high52, row.currency)}{unit}", f"{row.gap:.1f}%", delta_color="off")
    c3.metric("신고가까지", f"{row.to_high:+.1f}%",
              "오늘 신고가" if row.days_since_high == 0 else
              (f"고점 후 {row.days_since_high:.0f}거래일" if pd.notna(row.days_since_high) else "고점 날짜 확인 필요"),
              delta_color="off")
    c4.metric("52주 최저", f"{fmt_price(row.low52, row.currency)}{unit}")
    if getattr(row, "h52_fixed", False) is True:
        st.caption(f"⚠️ 52주 최고·최저를 네이버 공식값으로 고쳤어요. 일봉 계산값은 최고 "
                   f"{fmt_price(row.high52_calc, row.currency)}{unit} · 최저 {fmt_price(row.low52_calc, row.currency)}{unit}였어요. "
                   "이 종목은 일봉 기준 돌파 판정(매수 후보)을 보류해요.")

    d1, d2, d3, d4 = st.columns(4)
    d1.metric("종합 RS", _n(row.rs), f"3M {_n(row.rs_3m)} · 6M {_n(row.rs_6m)}", delta_color="off", help=RS_HELP)
    d2.metric("단기 RS(1M)", _n(row.rs_1m),
              f"1개월 {row.ret_1m:+.1f}%" if pd.notna(row.ret_1m) else None, delta_color="off")
    d3.metric("ATR%(20일)", _pct(row.atr_pct), "손절폭은 8%와 ATR 중 큰 값", delta_color="off")
    if isinstance(row.bo_status, str):
        if row.bo_status == "유지":
            sub = (f"{row.bo_date:%m/%d} 돌파 · 직전 52주 최고 {fmt_price(row.bo_level, row.currency)} "
                   f"대비 {row.bo_vs:+.1f}%")
        else:
            sub = (f"52주 최고 {fmt_price(row.bo_level, row.currency)}({row.bo_date:%m/%d}) "
                   f"대비 {row.bo_vs:+.1f}%")
        d4.metric("52주 최고가 기준", f"{row.bo_status} {int(row.bo_days)}일", sub, delta_color="off")
    else:
        d4.metric("52주 최고가 기준", "-", "일봉이 부족해요", delta_color="off")
    nh52, ath = _nh_text(row, "nh52"), _nh_text(row, "ath")
    ath_note = (f"역대 최고가 {fmt_price(row.ath_price, row.currency)}{unit}" if pd.notna(row.ath_price) else "")
    st.markdown(f"**신고가 봉** — 52주: {nh52 or '없음'} · 역대: {ath or ('없음' if row.ath_ok else '월봉 못 받음')}"
                + (f"  ·  {ath_note}" if ath_note else ""))
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
        candle_chart(code, row.currency, _QUOTES.get(code) if data.is_kr(code) else None,
                     row.high52 if pd.notna(row.high52) else None, key="detailchart")
        if not hist.empty:
            hist_rows = row.get("bo_history")
            hist_rows = hist_rows if isinstance(hist_rows, list) else []
            if hist_rows:
                st.caption("직전 52주 최고가 종가 돌파 기록 (그 아래에서 마감한 날이 이탈일)")
                st.dataframe(pd.DataFrame(hist_rows).astype({"이탈일": str})
                             .style.format({"돌파한 직전 52주 최고가": "{:,.0f}"}), hide_index=True)
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


# ─────────────────────────── 매수 후보 ───────────────────────────
BUY_TIER_STYLE = {"매수 가능": ("#1B8A4B", "✅"), "종가 확인": ("#A87400", "⏳"),
                  "시장 대기": ("#C0262E", "⛔"), "1개 미충족": ("#51616C", "⚠️")}


def _buy_params() -> dict:
    """원칙은 고정값, 숫자를 정해 주지 않은 기준만 바꿀 수 있게."""
    d = data.BUY_DEFAULTS
    with st.expander("⚙️ 기준 · 계좌 금액"):
        st.caption("고정 원칙: 지수 60일선 위 · 주도섹터의 대장주 · 52주 신고가 돌파(첫 돌파 우선) · RS 70↑ & 지수 RS보다 위 · "
                   "정배열(강한 종목 면제) · ADX 20↑ · 영업이익·EPS 정배열 · 손절 8%(또는 ATR) · 위험 1.5% · 최대 8종목 · 3R 절반 익절")
        st.caption("아래 숫자는 원칙에 없어 정해 둔 기본값이에요.")
        c1, c2, c3 = st.columns(3)
        fresh = c1.number_input("돌파 후 며칠 이내", 1, 20, d["fresh_days"], key="buy_fresh",
                                help="돌파 유지 1일째 = 돌파한 날. 너무 오래된 돌파는 타점이 지났다고 봐요.")
        ext = c2.number_input("20일선 이격 최대(%)", 5.0, 50.0, d["max_ext"], step=1.0, key="buy_ext",
                              help="원칙: 이평선에서 너무 뜬(단기 과열) 종목은 피하기")
        cap = c3.number_input("최소 시가총액(억원)", 0.0, 100000.0, d["min_cap"], step=500.0, key="buy_cap",
                              help="원칙: 너무 작은 종목·테마성 소형주 피하기")
        c4, c5, c6 = st.columns(3)
        vol = c4.number_input("돌파일 거래량(50일 평균의 몇 배)", 1.0, 5.0, d["vol_mult"], step=0.1, key="buy_vol",
                              help="원칙: 3stage 돌파는 큰 거래량")
        dcr = c5.number_input("돌파일 DCR 최소(%)", 0.0, 100.0, d["dcr_min"], step=5.0, key="buy_dcr",
                              help="원칙: 강한 종가(DCR 100%에 가까울수록 고가 근처 마감)")
        top_n = st.number_input("섹터 몇 위까지 주도주로 볼까요", 1, 3, d["top_n"], key="buy_topn",
                                help="원칙은 1위(대장주)만. 대장이 아직 안 움직일 때 2위까지 보고 싶으면 2")
        first_only = st.toggle("52주 안 첫 돌파만 보기", value=d["first_only"], key="buy_first",
                               help="끄면 첫 돌파를 맨 위에 두고 2번째 이상 돌파도 보여줘요. 켜면 첫 돌파만 통과.")
        equity = c6.number_input("계좌 평가금액(원, 수량 계산용)", 0, 10_000_000_000, 0, step=1_000_000,
                                 key="buy_equity", help="0이면 수량 계산을 안 해요. 이 값은 저장되지 않아요.")
    return {**d, "top_n": int(top_n), "first_only": bool(first_only), "fresh_days": int(fresh), "max_ext": float(ext), "min_cap": float(cap),
            "vol_mult": float(vol), "dcr_min": float(dcr), "earn_years": earn_years, "equity": float(equity or 0)}


# ─────────────────────────── 차기 주도섹터 · 섹터 안 급상승 엔진 ───────────────────────────
def load_trends_all():
    """국내 전 종목 외국인·기관 수급(최근 20거래일). 처음엔 기다리지 않고 뒤에서 받아요."""
    return swr(("trends_all", KR_CODES), 600, lambda: data.fetch_stock_trends(KR_CODES), first_wait=False)


def load_fins_all():
    """국내 전 종목 연간 영업이익(실적+전망). 12시간 기억, 처음엔 뒤에서 받아요."""
    return swr(("fins_all", KR_CODES), 43200, lambda: data.fetch_financials_many(KR_CODES, workers=12),
               first_wait=False)


def _ok_mark(v) -> str:
    return "✅" if v is True else ("❌" if v is False else "❔")


def _pct_txt(v) -> str:
    return "-" if v is None or pd.isna(v) else f"{v:+.1f}%"


TIER_STYLE = {"차기 주도 후보": ("🔭 후보", "#D6333B"), "주도 유지": ("👑 주도 유지", "#F2B134"),
              "관찰": ("👀 관찰", "#7FA7C9"), "해당 없음": ("· 미달", "#C9D2D8")}


def render_next(df: pd.DataFrame, fins, trends, leaders: set):
    _sec_h("🔭 차기 주도섹터", "이익증가율 가속 · 기관/외국인 수급 · 신고가 근처 종목 다수")
    d = data.NEXT_DEFAULTS
    with st.expander("⚙️ 기준"):
        c1, c2, c3 = st.columns(3)
        near_pct = c1.number_input("52주 고가 대비 % 이내", 3.0, 30.0, d["near_pct"], step=1.0, key="nx_near")
        near_rs = c2.number_input("그중 RS 최소", 0, 99, d["near_rs"], key="nx_rs")
        min_near = c3.number_input("몇 종목 이상", 1, 10, d["min_near"], key="nx_min")
        st.caption("① 분류 영업이익 합: 올해(E) 증가율 > 작년 증가율 & > 0 · ② 외국인+기관 20일·5일 모두 순매수 "
                   "(연기금·투신은 기관 합계에 포함) · ③ 위 조건의 종목 수. 3개 통과 = 후보, 2개 = 관찰. "
                   "가능성 = 이익 가속 40 · 시총 대비 수급 30 · 신고가 근처 비율 30 (섹터끼리 백분위, 비중은 제가 정한 값)")
    if fins is None or trends is None:
        st.info("업종 실적·수급을 받는 중이에요(처음 1~2분).")
    res = data.next_leader_sectors(df, fins, trends, leaders,
                                   {"near_pct": near_pct, "near_rs": near_rs, "min_near": min_near}, include_all=True)
    if res.empty:
        st.info("섹터를 판정할 데이터가 아직 없어요.")
        return
    cnt = res["tier"].value_counts()
    c1, c2, c3 = st.columns(3)
    c1.metric("🔭 차기 주도 후보", f"{cnt.get('차기 주도 후보', 0)}")
    c2.metric("👑 주도 유지", f"{cnt.get('주도 유지', 0)}")
    c3.metric("👀 관찰", f"{cnt.get('관찰', 0)}")

    top = res.dropna(subset=["score"]).sort_values("score", ascending=False).head(15)
    if not top.empty:
        dom = list(TIER_STYLE)
        chart = alt.Chart(top.assign(tier_lab=top["tier"].map(lambda x: TIER_STYLE[x][0]))).mark_bar(cornerRadius=3).encode(
            y=alt.Y("group:N", sort="-x", title=None, axis=alt.Axis(labelLimit=180)),
            x=alt.X("score:Q", title="가능성", scale=alt.Scale(domain=[0, 100])),
            color=alt.Color("tier:N", scale=alt.Scale(domain=dom, range=[TIER_STYLE[k][1] for k in dom]),
                            legend=alt.Legend(orient="top", title=None, labelExpr=
                                              "datum.label == '차기 주도 후보' ? '🔭 후보' : datum.label == '주도 유지' ? "
                                              "'👑 주도 유지' : datum.label == '관찰' ? '👀 관찰' : '· 미달'")),
            tooltip=[alt.Tooltip("group:N", title="섹터"), alt.Tooltip("tier_lab:N", title="상태"),
                     alt.Tooltip("score:Q", title="가능성", format=".0f"), alt.Tooltip("n_near:Q", title="신고가 근처")],
        ).properties(height=max(180, 26 * len(top)), width="container")
        st.altair_chart(chart)

    show_all = st.toggle("미달 섹터도 표에 보기", value=False, key="nx_all")
    full = res
    if not show_all:
        res = res[res["tier"] != "해당 없음"]
    if not res.empty:
        y = res["years"].iloc[0]
        view = pd.DataFrame({
            "상태": res["tier"].map(lambda x: TIER_STYLE[x][0]), "섹터": res["group"], "가능성": res["score"],
            f"① 이익({y[2]}E)": res.apply(lambda r: f"{_ok_mark(r['earn_ok'])} {_pct_txt(r['g_prev'])} → {_pct_txt(r['g_now'])}", axis=1),
            "② 외+기 20일(억)": res.apply(lambda r: (r["f20"] or 0) + (r["i20"] or 0) if r["flow_ok"] is not None else None, axis=1),
            "②": res["flow_ok"].map(_ok_mark) + res["both20"].map(lambda b: " ⭐" if b else ""),
            "③ 신고가 근처": res.apply(lambda r: f"{_ok_mark(r['near_ok'])} {r['n_near']}개", axis=1),
        })
        st.dataframe(
            view.style.format({"② 외+기 20일(억)": _eok_num}, na_rep="-")
            .map(lambda v: "" if pd.isna(v) or v == 0 else f"color: {UP if v > 0 else DOWN}; font-weight: 700",
                 subset=["② 외+기 20일(억)"]),
            hide_index=True, height=min(520, 36 * (len(view) + 1) + 4),
            column_config={"섹터": st.column_config.Column(pinned=True),
                           "가능성": st.column_config.ProgressColumn("가능성", min_value=0, max_value=100, format="%d")},
        )
    render_next_drill(df, full, fins, trends)


def render_next_drill(df: pd.DataFrame, res: pd.DataFrame, fins, trends):
    """섹터 하나를 골라 종목별로 들여다보기."""
    _sec_h("🔍 섹터 들여다보기")
    opts = res["group"].tolist()
    if not opts:
        return
    g = st.selectbox("섹터", opts, key="nx_drill",
                     format_func=lambda x: f"{x} (가능성 {res.set_index('group').loc[x, 'score']:.0f})"
                     if pd.notna(res.set_index("group").loc[x, "score"]) else x)
    m = data.momentum_engine(df, trends, fresh_days=recent_days)
    sub = m[m["group"] == g].copy()
    if sub.empty:
        return
    y = data.now_kst().year
    def og(code):
        f = (fins or {}).get(code)
        yo = data._year_op(f) if f is not None else {}
        a, b = yo.get(y - 1), yo.get(y)
        return (b / a - 1) * 100 if a and a > 0 and b is not None else None
    sub["og"] = sub["code"].map(og)
    sub = sub.sort_values("mom", ascending=False)
    view = pd.DataFrame({
        "종목": sub["name"] + sub["lead_rank"].map(lambda x: " 👑" if x == 1 else "").where(sub["lead_ok"] == True, ""),  # noqa: E712
        "급상승 점수": sub["mom"], "현재가": sub["price"], "등락률": sub["change"],
        "52주 고가 대비": sub["gap"], "RS": sub["rs"], "1개월 RS": sub["rs_1m"],
        "외국인 20일(억)": sub["f20"], "기관 20일(억)": sub["i20"],
        f"영업이익 {y}E 증가율": sub["og"], "신고가까지": sub["to_high"], "돌파": sub.apply(_bo_full, axis=1),
    })
    st.dataframe(
        view.style.format({"급상승 점수": "{:.0f}", "현재가": "{:,.0f}", "등락률": "{:+.2f}%", "52주 고가 대비": "{:+.1f}%",
                           "RS": "{:.0f}", "1개월 RS": "{:.0f}", "외국인 20일(억)": _eok_num, "기관 20일(억)": _eok_num,
                           f"영업이익 {y}E 증가율": "{:+.1f}%", "신고가까지": "{:+.1f}%"}, na_rep="-")
        .map(_bo_color, subset=["돌파"]).map(_to_high_color, subset=["신고가까지"])
        .map(lambda v: "" if pd.isna(v) or v == 0 else f"color: {UP if v > 0 else DOWN}",
             subset=["등락률", "외국인 20일(억)", "기관 20일(억)", f"영업이익 {y}E 증가율"]),
        hide_index=True, height=min(520, 36 * (len(view) + 1) + 4),
    )


def _sec_h(title: str, note: str = ""):
    st.markdown(f'<div class="sec-h"><b>{title}</b><span>{html.escape(note)}</span></div>', unsafe_allow_html=True)


def _score_bars(v: pd.DataFrame):
    """급상승 점수를 구성(수급·RS·캔들·저항 돌파)별로 쌓은 막대."""
    w = data.MOM_WEIGHTS
    parts = {"수급": ("s_flow", w["flow"]), "RS": ("s_rs", w["rs"]), "캔들": ("s_candle", w["candle"]),
             "저항 돌파": ("s_break", w["break"])}
    rows = []
    for _, r in v.iterrows():
        label = r["name"] + (" 👑" if (r["lead_rank"] == 1 and r["lead_ok"]) else "")
        for k, (col, wt) in parts.items():
            val = r[col]
            rows.append({"종목": label, "구성": k, "점수": 0 if pd.isna(val) else val * wt, "order": -r["mom"]})
    d = pd.DataFrame(rows)
    order = list(dict.fromkeys(d.sort_values("order")["종목"]))
    c = alt.Chart(d).mark_bar(cornerRadius=2).encode(
        y=alt.Y("종목:N", sort=order, title=None, axis=alt.Axis(labelLimit=140)),
        x=alt.X("점수:Q", title=None, scale=alt.Scale(domain=[0, 100])),
        color=alt.Color("구성:N", sort=list(parts), scale=alt.Scale(domain=list(parts),
                        range=["#D6333B", "#F2B134", "#2E6B6F", "#7FA7C9"]), legend=alt.Legend(orient="top", title=None)),
        order=alt.Order("구성:N"),
        tooltip=["종목:N", "구성:N", alt.Tooltip("점수:Q", format=".0f")],
    ).properties(height=max(160, 24 * len(order)), width="container")
    st.altair_chart(c)


def render_money(df: pd.DataFrame):
    """💰 거래대금: 지금 어느 섹터에 돈(거래대금)이 몰리는지 + 종목별 실시간 거래량."""
    d = df[df["code"].map(_mkt) == market].copy()
    if not {"tv_live", "tv_x", "vol_live", "vol_src"} <= set(d.columns):
        st.warning("거래량 자료를 아직 못 불러왔어요. 오른쪽 아래 'Manage app' → 'Reboot app'을 한 번 눌러 주세요.")
        return
    for c in ("tv_live", "tv_x", "vol_live", "change", "price"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d[d["tv_live"].notna()]
    if d.empty:
        st.info("거래량 자료가 아직 없어요. 잠시 뒤 새로고침해 보세요.")
        return
    live = (d["vol_src"] == "실시간").any()
    frac = data.session_frac()
    when = (f"실시간 · 장 진행 {frac * 100:.0f}%" if live and frac < 1 else "실시간(오늘 누적)") if live else "최근 거래일 기준"
    st.markdown(f'<div class="sec-h"><b>💰 지금 돈이 몰리는 섹터</b><span>{when} · 보드에 넣은 {market_label} 종목 기준</span></div>',
                unsafe_allow_html=True)

    by = st.segmented_control("묶는 기준", ["세부 분류", "산업"], default="세부 분류", required=True,
                              key="money_by", label_visibility="collapsed") or "세부 분류"
    sm = data.sector_money(d, "group" if by == "세부 분류" else "sector")
    total = d["tv_live"].sum()
    has = d["tv_x"].notna() & (d["tv_x"] > 0)
    base = (d.loc[has, "tv_live"] / d.loc[has, "tv_x"]).sum() if has.any() else None
    tv_b = d.loc[has, "tv_live"].sum()
    hot = sm[(sm["x"] >= 1.3) & (sm["n"] >= 2)].sort_values("x", ascending=False)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("보드 전체 거래대금", data.format_krw(total) + ("원" if market == "KR" else ""))
    c2.metric("평소 대비", f"{tv_b / base:.2f}배" if base else "-", help="직전 20거래일 평균 거래대금을 지금 시각까지로 환산해 비교")
    c3.metric("거래대금 1위 섹터", sm.iloc[0]["sector"] if len(sm) else "-",
              f"비중 {sm.iloc[0]['share']:.1f}%" if len(sm) else None, delta_color="off")
    c4.metric("평소보다 뜨거운 섹터", hot.iloc[0]["sector"] if len(hot) else "없음",
              f"{hot.iloc[0]['x']:.1f}배" if len(hot) else None, delta_color="off")

    top = sm.head(15).copy()
    top["label"] = top["sector"].astype(str).str.replace("(해외)", "", regex=False)
    top["tv_eok"] = top["tv"] / 1e8
    top["heat"] = top["x"].apply(lambda v: "평소의 2배 이상" if v == v and v >= 2 else
                                 ("1.3~2배" if v == v and v >= 1.3 else ("평소 수준" if v == v and v >= 0.7 else "평소보다 적음")))
    chart = (
        alt.Chart(top)
        .mark_bar(cornerRadiusEnd=4)
        .encode(
            y=alt.Y("label:N", sort="-x", title=None, axis=alt.Axis(labelLimit=200, labelOverlap=False)),
            x=alt.X("tv_eok:Q", title="거래대금(억)" if market == "KR" else "거래대금(현지 통화, 억)"),
            color=alt.Color("heat:N", title="평소 대비",
                            scale=alt.Scale(domain=["평소의 2배 이상", "1.3~2배", "평소 수준", "평소보다 적음"],
                                            range=[UP, "#F08A8F", "#9FB0BA", "#7FA7DE"]),
                            legend=alt.Legend(orient="top")),
            tooltip=[alt.Tooltip("label:N", title="섹터"), alt.Tooltip("tv_eok:Q", title="거래대금(억)", format=",.0f"),
                     alt.Tooltip("share:Q", title="비중(%)", format=".1f"), alt.Tooltip("x:Q", title="평소 대비(배)", format=".2f"),
                     alt.Tooltip("chg:Q", title="등락(거래대금 가중, %)", format="+.2f"), alt.Tooltip("top:N", title="상위 종목")],
        )
        .properties(height=max(220, 26 * len(top)), width="container")
    )
    st.altair_chart(chart)

    view = pd.DataFrame({
        "섹터": sm["sector"], "거래대금": sm["tv"], "비중": sm["share"], "평소 대비": sm["x"],
        "등락(가중)": sm["chg"], "상승/하락": sm.apply(lambda r: f"{r['up']} / {r['down']}", axis=1),
        "종목 수": sm["n"], "거래대금 상위 종목": sm["top"],
    })
    st.dataframe(
        view.style.format({"거래대금": data.format_krw, "비중": "{:.1f}%", "평소 대비": "{:.2f}배", "등락(가중)": "{:+.2f}%"},
                          na_rep="-")
        .map(_x_color, subset=["평소 대비"])
        .map(lambda v: "" if pd.isna(v) or v == 0 else f"color: {UP if v > 0 else DOWN}; font-weight: 600",
             subset=["등락(가중)"]),
        hide_index=True, height=min(420, 36 * (len(view) + 1) + 4),
        column_config={"섹터": st.column_config.Column(pinned=True)})
    st.caption("평소 대비 = 지금까지 거래대금 ÷ (직전 20거래일 평균 거래대금 × 장 진행 비율). 1.3배 이상이면 평소보다 돈이 더 도는 섹터예요. "
               "장 초반(9시~9시 30분)에는 거래가 몰려서 배수가 크게 나오기 쉬워요. 거래대금만으로는 사는 돈인지 파는 돈인지 모르니 등락과 같이 보세요.")

    st.markdown('<div class="sec-h"><b>📊 종목별 실시간 거래량</b><span>거래대금 많은 순</span></div>', unsafe_allow_html=True)
    c1, c2 = st.columns([2, 1])
    pick = c1.selectbox("섹터", ["전체", *sm["sector"].tolist()], key="money_pick", label_visibility="collapsed")
    n_show = c2.segmented_control("표시", [30, 100, "전체"], default=30, required=True, key="money_n",
                                  label_visibility="collapsed") or 30
    col = "group" if by == "세부 분류" else "sector"
    t = d if pick == "전체" else d[d[col] == pick]
    t = t.sort_values("tv_live", ascending=False)
    if n_show != "전체":
        t = t.head(int(n_show))
    tv_view = pd.DataFrame({
        "종목": t["name"], "분류": t["group"], "현재가": t["price"], "일간%": t["change"],
        "거래량": t["vol_live"], "거래대금": t["tv_live"], "평소 대비": t["tv_x"],
    })
    whole = tv_view.index[t["currency"].isin(["KRW", "JPY"]).values]
    st.dataframe(
        tv_view.style
        .format({"현재가": "{:,.2f}", "일간%": "{:+.2f}%", "거래량": fmt_vol, "거래대금": data.format_krw,
                 "평소 대비": "{:.1f}배"}, na_rep="-")
        .format("{:,.0f}", subset=pd.IndexSlice[whole, ["현재가"]], na_rep="-")
        .map(lambda v: "" if pd.isna(v) or v == 0 else f"color: {UP if v > 0 else DOWN}; font-weight: 600", subset=["일간%"])
        .map(_x_color, subset=["평소 대비"])
        .map(lambda _: "font-weight: 600", subset=["종목"]),
        hide_index=True, height=min(760, 36 * (len(tv_view) + 1) + 4),
        column_config={"종목": st.column_config.Column(pinned=True)})
    if market == "KR":
        st.caption("거래량은 네이버 실시간 누적(몇 초 지연)이에요. 자동 새로고침 주기마다 바뀌어요. 매매 전에는 HTS로 다시 확인하세요.")
    else:
        st.caption("해외 종목은 실시간 거래량을 받지 않아서 최근 거래일 일봉 기준이에요.")


def render_engine(df: pd.DataFrame, trends, leaders: set, fins=None):
    _sec_h("🚀 섹터 안에서 치고 올라오는 종목", "영업이익 증가 종목만")
    if fins is None:
        st.info("영업이익 확인 중이에요(처음 1~2분).")
        return
    m = data.momentum_engine(df, trends, fresh_days=recent_days, fins=fins, require_op=True)
    if m.empty:
        st.info("영업이익이 늘어나는 종목 중에 순위를 매길 종목이 없어요.")
        return
    groups = (m.groupby("group")["mom"].max().sort_values(ascending=False).index.tolist())
    lead_first = [g for g in groups if g in leaders] + [g for g in groups if g not in leaders]
    opts = ["섹터별 1위 모아보기"] + lead_first
    pick = st.selectbox("섹터", opts, key="eng_group", label_visibility="collapsed",
                        format_func=lambda g: g if g == opts[0] else (f"👑 {g}" if g in leaders else g))
    if pick == opts[0]:
        v = m[m["mom_rank"] == 1].sort_values("mom", ascending=False).head(15)
    else:
        v = m[m["group"] == pick].sort_values("mom", ascending=False).head(15)
    _score_bars(v)

    view = pd.DataFrame({
        "종목": v["name"] + v["lead_rank"].map(lambda x: " 👑" if x == 1 else "").where(v["lead_ok"] == True, ""),  # noqa: E712
        "점수": v["mom"], "등락률": v["change"], "신고가까지": v["to_high"], "돌파": v.apply(_bo_full, axis=1),
        "영업이익": v["op_txt"], "섹터": v["group"],
        "외+기 5일(억)": v["fi5"],
    })
    detail = st.toggle("세부 지표", key="eng_detail")
    if detail:
        view = view.assign(**{"수급": v["s_flow"].values, "RS점수": v["s_rs"].values, "캔들": v["s_candle"].values,
                              "저항": v["s_break"].values, "1개월 RS": v["rs_1m"].values, "종합 RS": v["rs"].values,
                              "오늘 거래량": v["vol_today"].values, "오늘 DCR": v["dcr_today"].values})
    fmt = {"점수": "{:.0f}", "등락률": "{:+.2f}%", "신고가까지": "{:+.1f}%", "외+기 5일(억)": _eok_num, "수급": "{:.0f}",
           "RS점수": "{:.0f}", "캔들": "{:.0f}", "저항": "{:.0f}", "1개월 RS": "{:.0f}", "종합 RS": "{:.0f}",
           "오늘 거래량": "{:.1f}배", "오늘 DCR": "{:.0f}%"}
    st.dataframe(
        view.style.format({k: f for k, f in fmt.items() if k in view.columns}, na_rep="-")
        .map(lambda x: "" if pd.isna(x) or x == 0 else f"color: {UP if x > 0 else DOWN}", subset=["등락률", "외+기 5일(억)"])
        .map(_bo_color, subset=["돌파"]).map(_to_high_color, subset=["신고가까지"]),
        hide_index=True, height=min(460, 36 * (len(view) + 1) + 4),
        column_config={"종목": st.column_config.Column(pinned=True),
                       "점수": st.column_config.ProgressColumn("점수", min_value=0, max_value=100, format="%.0f"),
                       "돌파": st.column_config.Column(help=BO_FULL_HELP)},
    )
    with st.expander("ℹ️ 점수 기준"):
        st.markdown("- 국내 보드 종목 전체 기준 백분위를 가중 평균: **수급 30% · RS 25% · 캔들 20% · 저항 돌파 25%** (비중은 제가 정한 값)\n"
                    "- 수급 = 외국인+기관 5일 순매수 ÷ 시총 · RS = 1개월 RS + RS 가속 · 캔들 = 오늘 거래량 배수 + 종가 위치(DCR) · "
                    f"저항 돌파 = 돌파 {recent_days}거래일 이내 만점, 아니면 52주 고가에 가까울수록\n"
                    "- 올해(E) 영업이익이 작년보다 늘지 않은 종목은 빼요. 매수 판단은 🎯 매수 후보 원칙으로.")


# ─────────────────────────── 🧭 시나리오 ───────────────────────────
SCN_COLOR = {1: "#1B8A4B", 2: "#C27C0E", 2.5: "#A87400", 3: "#1B6FD6", 4: "#C0262E", 0: "#51616C", None: "#51616C"}


def _index_ret(hist: dict, window: str) -> dict:
    out = {}
    for name, sym in data.MARKETS.items():
        h = hist.get(sym, (data.empty_frame(), None))[0]
        if h is None or h.empty or len(h) < 3:
            out[name] = None
            continue
        c = pd.Series(h["close"].to_numpy(dtype=float), index=pd.to_datetime(h["date"]).dt.normalize())
        last = c.index[-1]
        if window == "오늘":
            base = c.iloc[-2]
        elif window == "이번 주":
            before = c[c.index < last - pd.Timedelta(days=last.weekday())]
            base = before.iloc[-1] if len(before) else c.iloc[0]
        else:
            base = c.iloc[max(0, len(c) - 1 - int(window.replace("거래일", "")))]
        out[name] = (c.iloc[-1] / base - 1) * 100
    return out


def _scn_action(sid, hold: str, a: str, b: str) -> str:
    """보유 상태에 맞춘 행동. 기본(A 보유)은 효석 메모 그대로."""
    base = data.SCN_INFO[sid][2].replace("{A}", a).replace("{B}", b)
    if hold == "A 보유":
        return base
    if hold == "없음":
        return {1: f"{a} 쪽에서 매수 후보 원칙 통과 종목만", 2: f"{b} 쪽 원칙 통과 종목부터", 2.5: f"{b} 쪽 원칙 통과 종목 일부, {a} 쪽은 관찰",
                3: f"{a}·{b} 모두 원칙 통과 종목", 4: "신규 매수 쉬기(현금)", 0: "다음 신호 대기"}[sid]
    if hold == "B 보유":
        return {1: f"{b} 보유분은 5일선·50일선 점검(시간이 필요한 구간), {a} 원칙 통과 종목 검토",
                2: f"{b} 보유 유지(유리)", 2.5: f"{b} 보유 유지, {a} 흐름 확인", 3: f"{b} 유지(유리) + {a} 원칙 통과 종목",
                4: "현금 확대", 0: "다음 신호 대기"}[sid]
    return {1: f"{a} 유지, {b} 비중 점검", 2: f"{a} 일부 줄이고 {b} 늘리기", 2.5: f"{a} 급하게 줄이지 말고 {b} 흐름 확인",
            3: "둘 다 유지(유리)", 4: "현금 확대", 0: "다음 신호 대기"}[sid]


VERDICT_COLOR = {"섹터 확장": "#1B6FD6", "로테이션 확인": "#D9822B", "기존 리더 유지": "#1B8A4B",
                 "돈이 빠지는 중": "#C0262E", "확인 불가": "#8A979F"}
ZONES = [   # (x1, x2, y1, y2 — 기준 t의 배수, 'L'=끝), 이름, 색
    ("t", "L", "t", "L", "3 둘 다 감", "#1B6FD6"),
    ("-t", "t", "t", "L", "3쪽", "#6FA3E8"),
    ("-ct", "-t", "t", "L", "2↔3", "#E3B341"),
    ("-L", "-ct", "t", "L", "2 로테이션", "#D9822B"),
    ("t", "L", "-L", "t", "1 A만 감", "#1B8A4B"),
    ("-t", "t", "-L", "t", "관망", "#B9C4CC"),
    ("-L", "-t", "-L", "t", "4 현금", "#C0262E"),
]


def _quadrant_chart(ra: float, rb: float, t: float, crash: float, a_name: str, b_name: str, trail: pd.DataFrame):
    """가로 = A 수익률, 세로 = B 수익률. 칸 색 = 시나리오, 큰 점 = 지금, 선 = 최근 5거래일 누적 궤적."""
    vals = [abs(ra), abs(rb), crash * t * 1.3]
    if not trail.empty:
        vals += [trail["a"].abs().max(), trail["b"].abs().max()]
    L = max(vals) * 1.15
    num = {"t": t, "-t": -t, "ct": crash * t, "-ct": -crash * t, "L": L, "-L": -L}
    z = pd.DataFrame([{"x1": num[a], "x2": num[b], "y1": num[c], "y2": num[d], "zone": nm.replace("A", a_name),
                       "color": col, "cx": (num[a] + num[b]) / 2, "cy": (num[c] + num[d]) / 2}
                      for a, b, c, d, nm, col in ZONES])
    sx = alt.Scale(domain=[-L, L], nice=False)
    sy = alt.Scale(domain=[-L, L], nice=False)
    rects = alt.Chart(z).mark_rect(opacity=0.16).encode(
        x=alt.X("x1:Q", scale=sx, title=f"{a_name} 수익률(%) →"), x2="x2:Q",
        y=alt.Y("y1:Q", scale=sy, title=f"{b_name} 수익률(%) →"), y2="y2:Q",
        color=alt.Color("color:N", scale=None), tooltip=[alt.Tooltip("zone:N", title="시나리오")])
    labels = alt.Chart(z).mark_text(fontSize=12, fontWeight="bold", opacity=0.7).encode(
        x="cx:Q", y="cy:Q", text="zone:N", color=alt.Color("color:N", scale=None))
    zero = alt.Chart(pd.DataFrame({"v": [0]}))
    axes = zero.mark_rule(color="#8A979F", strokeDash=[3, 3]).encode(x="v:Q") + \
        zero.mark_rule(color="#8A979F", strokeDash=[3, 3]).encode(y="v:Q")
    layers = [rects, labels, axes]
    if not trail.empty:
        tr = trail.reset_index(drop=True).assign(i=lambda d: range(len(d)))
        layers.append(alt.Chart(tr).mark_line(color="#51616C", strokeWidth=1.5, point=alt.OverlayMarkDef(size=35, color="#51616C"))
                      .encode(x="a:Q", y="b:Q", order="i:Q",
                              tooltip=[alt.Tooltip("day:N", title="날짜"), alt.Tooltip("a:Q", title=a_name, format="+.2f"),
                                       alt.Tooltip("b:Q", title=b_name, format="+.2f")]))
        layers.append(alt.Chart(tr).mark_text(dy=-9, fontSize=10, color="#51616C").encode(x="a:Q", y="b:Q", text="day:N"))
    now = pd.DataFrame({"a": [ra], "b": [rb], "t": ["지금"]})
    layers.append(alt.Chart(now).mark_point(size=420, filled=True, color="#16212B", stroke="#FFFFFF", strokeWidth=2)
                  .encode(x="a:Q", y="b:Q", tooltip=[alt.Tooltip("a:Q", title=a_name, format="+.2f"),
                                                    alt.Tooltip("b:Q", title=b_name, format="+.2f")]))
    layers.append(alt.Chart(now).mark_text(dy=-18, fontSize=13, fontWeight="bold", color="#16212B")
                  .encode(x="a:Q", y="b:Q", text="t:N"))
    st.altair_chart(alt.layer(*layers).properties(height=380, width="container"))


def _daily_bars(A: dict, B: dict, a_name: str, b_name: str):
    d = pd.DataFrame({a_name: A["daily"], b_name: B["daily"]}).dropna(how="all")
    if d.empty:
        return
    d.index = [x.strftime("%m/%d") for x in d.index]
    long = d.reset_index(names="날짜").melt("날짜", var_name="바스켓", value_name="수익률")
    c = alt.Chart(long).mark_bar(cornerRadius=2).encode(
        x=alt.X("날짜:N", title=None, axis=alt.Axis(labelAngle=0)),
        xOffset=alt.XOffset("바스켓:N", sort=[a_name, b_name]),
        y=alt.Y("수익률:Q", title="%"),
        color=alt.Color("바스켓:N", sort=[a_name, b_name], scale=alt.Scale(range=["#F2B134", "#2E6B6F"]),
                        legend=alt.Legend(orient="top", title=None)),
        tooltip=["날짜:N", "바스켓:N", alt.Tooltip("수익률:Q", format="+.2f")],
    ).properties(height=200, width="container")
    st.altair_chart(c)


def _tile(ok, label: str, value: str) -> str:
    cls = "ok" if ok is True else ("no" if ok is False else "na")
    ic = "✓" if ok is True else ("✕" if ok is False else "?")
    return (f'<div class="tile {cls}"><span class="t-ic">{ic}</span><div class="t-lb">{html.escape(label)}</div>'
            f'<div class="t-v">{html.escape(value)}</div></div>')


def render_money_confirm(df: pd.DataFrame, a_groups, b_groups, a_name: str, b_name: str, price_sid):
    c = data.rotation_confirm(df, a_groups, b_groups)
    title, _mean, act = c["verdict"]
    col = VERDICT_COLOR.get(title, "#8A979F")
    price_name = data.SCN_INFO[price_sid][0].replace("{A}", a_name).replace("{B}", b_name) if price_sid is not None else "-"
    price_map = {1: "기존 리더 유지", 2: "로테이션 확인", 2.5: "로테이션 확인", 3: "섹터 확장", 4: "돈이 빠지는 중"}
    mismatch = price_sid in price_map and price_map[price_sid] != title and title != "확인 불가"
    st.markdown(
        f'<div class="verdict" style="--vc:{col}"><div class="v-top"><span class="v-lab">돈의 방향</span>'
        f'<span class="v-price">가격 기준 {html.escape(price_name)}{" · ⚠ 돈과 다름" if mismatch else ""}</span></div>'
        f'<div class="v-title">{html.escape(title)}</div><div class="v-act">{html.escape(act.split(" · ")[0])}</div></div>',
        unsafe_allow_html=True)
    B, A = c["B"], c["A"]
    bp, bn = c["b_share"]
    b_tiles = [
        _tile(c["b_checks"][0][1], "거래대금", "-" if B["tv_ratio"] is None else f"{B['tv_ratio']:.2f}배"),
        _tile(c["b_checks"][1][1], "거래대금 비중", "-" if bn is None else f"{bp * 100:.1f}→{bn * 100:.1f}%"),
        _tile(c["b_checks"][2][1], "신규 돌파", f"{B['n_break']}개"),
        _tile(c["b_checks"][3][1], "RS 동반 상승", "-" if B["rs_up"] is None else f"{B['rs_up'] * 100:.0f}%"),
    ]
    n_def = len(c["defense"])
    a_tiles = [
        _tile(c["a_checks"][1][1], "리더 방어", f"{sum(x['ok'] for x in c['defense'])}/{n_def}" if n_def else "-"),
        _tile(c["a_checks"][0][1], "RS(1M) 중앙값", "-" if A["rs1m_med"] is None else f"{A['rs1m_med']:.0f}"),
    ]
    chips = []
    for x in c["defense"]:
        v20 = "" if x["vs20"] is None else f" {x['vs20']:+.0f}%"
        chips.append(f'<span class="lc {"ok" if x["ok"] else "no"}">{html.escape(x["name"])}{v20}</span>')
    st.markdown(
        f'<div class="tiles-wrap"><div><div class="tiles-h">{html.escape(b_name)} · 돈을 받아가나 <b>{c["b_ok"]}/4</b></div>'
        f'<div class="tiles">{"".join(b_tiles)}</div></div>'
        f'<div><div class="tiles-h">{html.escape(a_name)} · 리더가 버티나 <small>(20일선 대비)</small></div>'
        f'<div class="tiles t2">{"".join(a_tiles)}</div>'
        f'<div class="lead-chips">{"".join(chips)}</div></div></div>', unsafe_allow_html=True)


def render_money_radar(df: pd.DataFrame, leaders: set):
    st.markdown("#### 📡 섹터 간 돈의 이동")
    r, sm = data.sector_money_radar(df, leaders, {"fresh_days": recent_days})
    if r.empty:
        st.caption("거래대금 데이터가 아직 부족해요.")
        return
    allr = sm["all_tv_ratio"]
    flow_line = (f'<span class="mv out">{html.escape(", ".join(sm["losers"]) or "—")}</span>'
                 f'<span class="mv-arrow">→</span>'
                 f'<span class="mv in">{html.escape(", ".join(sm["gainers"]) or "받는 쪽 없음")}</span>')
    total = "" if allr is None else (f'<span class="mv-total">보드 전체 거래대금 {allr:.2f}배 '
                                     f'{"▲ 새 돈" if allr >= 1.1 else ("▼ 이탈" if allr <= 0.9 else "· 비슷")}</span>')
    st.markdown(f'<div class="mv-row">{flow_line}{total}</div>', unsafe_allow_html=True)
    kind = r["state"].map(lambda x: "돈 들어옴" if x in ("💰 돈 들어오는 중", "🔥 리더 강화") else
                          ("돈 빠짐" if x in ("🧊 돈 빠지는 중", "📉 리더 약화(돈 빠짐)") else
                           ("주도 유지" if x == "👑 리더 유지" else "변화 없음")))
    d = r.assign(kind=kind, rsu=r["rs_up"].fillna(0) * 100,
                 label=r.apply(lambda x: ("👑 " if x["leading"] else "") + x["group"], axis=1))
    dom = ["돈 들어옴", "돈 빠짐", "주도 유지", "변화 없음"]
    base = alt.Chart(d).encode(
        x=alt.X("share_chg:Q", title="거래대금 비중 변화(%p) →"),
        y=alt.Y("rsu:Q", title="RS 동반 상승(%) →", scale=alt.Scale(domain=[0, 100])))
    pts = base.mark_circle(opacity=0.8, stroke="#FFFFFF", strokeWidth=1).encode(
        size=alt.Size("share_now:Q", legend=None, scale=alt.Scale(range=[40, 900])),
        color=alt.Color("kind:N", scale=alt.Scale(domain=dom, range=["#D6333B", "#1F66C9", "#F2B134", "#C9D2D8"]),
                        legend=alt.Legend(orient="top", title=None)),
        tooltip=[alt.Tooltip("group:N", title="섹터"), alt.Tooltip("state:N", title="상태"),
                 alt.Tooltip("share_prev:Q", title="이전 비중%", format=".2f"),
                 alt.Tooltip("share_now:Q", title="최근 비중%", format=".2f"),
                 alt.Tooltip("n_break:Q", title="신규 돌파"), alt.Tooltip("rsu:Q", title="RS 동반%", format=".0f")])
    txt = base.transform_filter(alt.datum.kind != "변화 없음").mark_text(dy=-12, fontSize=11, fontWeight="bold") \
        .encode(text="label:N", color=alt.Color("kind:N", scale=alt.Scale(domain=dom, range=["#B0242B", "#1A56A8", "#A87400", "#8A979F"]),
                                               legend=None))
    rules = alt.Chart(pd.DataFrame({"x": [0]})).mark_rule(color="#8A979F", strokeDash=[3, 3]).encode(x="x:Q") + \
        alt.Chart(pd.DataFrame({"y": [50]})).mark_rule(color="#8A979F", strokeDash=[3, 3]).encode(y="y:Q")
    st.altair_chart((rules + pts + txt).properties(height=380, width="container"))
    with st.expander("섹터별 숫자 보기"):
        view = pd.DataFrame({"상태": r["state"], "섹터": r["group"],
                             "비중": r.apply(lambda x: f"{x['share_prev']:.1f}→{x['share_now']:.1f}%", axis=1),
                             "변화(%p)": r["share_chg"], "거래대금": r["tv_ratio"], "신규 돌파": r["n_break"],
                             "RS 동반": r["rs_up"]})
        st.dataframe(view.style.format({"변화(%p)": "{:+.2f}", "거래대금": "{:.2f}배", "RS 동반": "{:.0%}"}, na_rep="-")
                     .map(lambda x: "" if pd.isna(x) or x == 0 else f"color: {UP if x > 0 else DOWN}", subset=["변화(%p)"]),
                     hide_index=True, column_config={"섹터": st.column_config.Column(pinned=True)})


def _mini_list(rows: list[str]) -> None:
    st.markdown('<div class="mini">' + "".join(rows) + "</div>", unsafe_allow_html=True)


def _mini_row(name: str, right: str, sub: str) -> str:
    return (f'<div class="mini-row"><div><b>{html.escape(name)}</b><small>{sub}</small></div>'
            f'<span>{right}</span></div>')


def render_scenario(df: pd.DataFrame, histories: dict, trends, leaders: set, fins=None):
    groups_all = sorted(df["group"].dropna().unique().tolist())
    pr0 = data.SCENARIO_PRESETS["반도체 후공정 vs 전공정"]
    with st.expander("⚙️ 바스켓 · 기간 · 기준", expanded=False):
        preset = st.selectbox("프리셋", list(data.SCENARIO_PRESETS), key="scn_preset")
        pr = data.SCENARIO_PRESETS[preset]
        c1, c2 = st.columns(2)
        a_name = c1.text_input("A (지금 들고 있는/주도 쪽)", pr["a_name"], key="scn_an")
        b_name = c2.text_input("B (다음 후보 쪽)", pr["b_name"], key="scn_bn")
        a_groups = c1.multiselect("A 분류", groups_all, [g for g in pr["a_groups"] if g in groups_all], key="scn_ag")
        b_groups = c2.multiselect("B 분류", groups_all, [g for g in pr["b_groups"] if g in groups_all], key="scn_bg")
        c3, c4, c5, c6 = st.columns(4)
        window = c3.selectbox("기간", list(data.SCN_WINDOWS), index=1, key="scn_win")
        t = c4.number_input("간다/빠진다 기준(%)", 0.3, 15.0, data.SCN_WINDOWS[window], step=0.5, key=f"scn_t_{window}")
        crash = c5.number_input("과하게 빠짐(기준 ×)", 1.5, 5.0, 2.5, step=0.5, key="scn_crash")
        hold = c6.selectbox("지금 보유", ["A 보유", "B 보유", "둘 다", "없음"], key="scn_hold")
        st.caption(f"칸 = 시나리오 · 큰 점 = {window} 수익률(시총가중) · 선 = 최근 5거래일 누적 궤적. "
                   f"±{t:g}% 넘으면 간다/빠진다, A가 −{crash * t:g}% 아래면 과하게 빠짐. 행동은 돈의 방향 판정을 따라요.")
    if not a_groups or not b_groups:
        st.info("A·B 분류를 하나 이상씩 골라 주세요.")
        return
    A = data.basket_stats(df, histories, a_groups, window, trends)
    B = data.basket_stats(df, histories, b_groups, window, trends)
    cls = data.classify_scenario(A["ret_cap"], B["ret_cap"], t, crash)
    if cls["id"] is None:
        st.warning("바스켓 가격 데이터가 부족해요.")
        return

    render_money_confirm(df, a_groups, b_groups, a_name, b_name, cls["id"])

    idx = _index_ret(load_indexes(), window)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric(a_name, _pct_txt(A["ret_cap"]), f"상승 {A['breadth'] or 0:.0f}%", delta_color="off")
    m2.metric(b_name, _pct_txt(B["ret_cap"]), f"상승 {B['breadth'] or 0:.0f}%", delta_color="off")
    m3.metric("코스피", _pct_txt(idx.get("코스피")))
    m4.metric("코스닥", _pct_txt(idx.get("코스닥")))

    ca, cb = st.columns([1.35, 1])
    with ca:
        a_d, b_d = A["daily"], B["daily"]
        days = [d for d in a_d.index if d in b_d.index]
        trail = pd.DataFrame({"day": [d.strftime("%m/%d") for d in days],
                              "a": ((1 + a_d.loc[days] / 100).cumprod() - 1).values * 100,
                              "b": ((1 + b_d.loc[days] / 100).cumprod() - 1).values * 100}) if days else pd.DataFrame()
        _quadrant_chart(A["ret_cap"], B["ret_cap"], t, crash, a_name, b_name, trail)
    with cb:
        st.markdown("**하루씩**")
        _daily_bars(A, B, a_name, b_name)
        paths = [p_ for p_ in data.scenario_paths(A["ret_cap"], B["ret_cap"], t, crash) if p_["dist"] > 1e-6][:2]
        if paths:
            st.markdown("**다음으로 가까운 시나리오**")
            rows = []
            for p_ in paths:
                nm = data.SCN_INFO[p_["id"]][0].replace("{A}", a_name).replace("{B}", b_name)
                mv = " · ".join(f"{lab} {x:+.1f}%p" for lab, x in ((a_name, p_["move_a"]), (b_name, p_["move_b"])) if abs(x) > 1e-6)
                rows.append(_mini_row(nm, "", mv))
            _mini_list(rows)

    m = data.momentum_engine(df, trends, fresh_days=recent_days, fins=fins, require_op=fins is not None)
    cA, cB = st.columns(2)
    with cA:
        st.markdown(f"**{a_name} 대장 · 끌고 가기**")
        rows = []
        for r in data.sector_leaders(df, a_groups).itertuples():
            g = r.ma5_vs_50
            sig = "-" if pd.isna(g) else ("🔴 정리" if g <= 0 else ("🟡 근접" if g < 3 else "🟢 보유"))
            rows.append(_mini_row(r.name, sig, f"{html.escape(r.group)} · {_bo_full(r)}"))
        if rows:
            _mini_list(rows)
        else:
            st.caption("대장주 없음")
    with cB:
        st.markdown(f"**{b_name} 담을 후보**")
        if fins is None:
            st.caption("영업이익 확인 중…")
        else:
            rows = []
            for r in m[m["group"].isin(b_groups)].sort_values("mom", ascending=False).head(5).itertuples():
                th = "-" if pd.isna(r.to_high) else f"{r.to_high:+.1f}%"
                rows.append(_mini_row(r.name + (" 👑" if (r.lead_rank == 1 and r.lead_ok) else ""),
                                      f"신고가까지 {th}", f"{_bo_full(r)} · {r.op_txt}"))
            if rows:
                _mini_list(rows)
            else:
                st.caption("조건에 맞는 종목 없음")
    st.divider()
    render_money_radar(df, leaders)


def _hold_text(r) -> str:
    """원칙: 3R 절반 익절 뒤 나머지는 일봉 5일선이 50일선에 닿으면 정리. 그 전까지 끌고 간다."""
    g = r.get("ma5_vs_50")
    if g is None or pd.isna(g):
        return "-"
    if g <= 0:
        return f"🔴 5일선이 50일선 닿음({g:+.1f}%) — 나머지 정리 신호"
    if g < 3:
        return f"🟡 5일선이 50일선 +{g:.1f}% — 가까워짐"
    return f"🟢 끌고 가기(5일선 50일선 +{g:.1f}%)"


def _hold_short(r) -> str:
    g = r.get("ma5_vs_50")
    if g is None or pd.isna(g):
        return "-"
    return f"🔴 정리 신호 {g:+.1f}%" if g <= 0 else (f"🟡 근접 {g:+.1f}%" if g < 3 else f"🟢 보유 {g:+.1f}%")


def render_leaders(df: pd.DataFrame, leaders: set):
    """주도섹터마다 대장주 1위와 끌고 가기 점검(5일선 vs 50일선)."""
    if not leaders:
        return
    lead = data.sector_leaders(df, leaders)
    if lead.empty:
        return
    _sec_h("👑 주도섹터 대장주 · 끌고 가기", "5일선이 50일선에 닿으면 나머지 정리")
    view = pd.DataFrame({
        "대장주": lead["name"], "섹터": lead["group"], "현재가": lead["price"], "등락률": lead["change"],
        "신고가까지": lead["to_high"], "돌파": lead.apply(_bo_full, axis=1), "RS": lead["rs"],
        "끌고 가기": lead.apply(_hold_short, axis=1),
    })
    st.dataframe(
        view.style.format({"현재가": "{:,.0f}", "등락률": "{:+.2f}%", "신고가까지": "{:+.1f}%", "RS": "{:.0f}"}, na_rep="-")
        .map(lambda v: "" if pd.isna(v) or v == 0 else f"color: {UP if v > 0 else DOWN}", subset=["등락률"])
        .map(_bo_color, subset=["돌파"]).map(_to_high_color, subset=["신고가까지"]),
        hide_index=True, height=min(360, 36 * (len(view) + 1) + 4),
        column_config={"대장주": st.column_config.Column(pinned=True), "돌파": st.column_config.Column(help=BO_FULL_HELP)},
    )


def render_buy(df: pd.DataFrame, quotes: dict):
    """내 매매 원칙을 모두 통과한 국내 종목. 산업·태그 필터와 관계없이 보드 전체에서 찾아요."""
    _sec_h("🎯 매수 후보", "내 원칙을 모두 통과한 국내 종목만")
    p = _buy_params()
    hist = load_indexes()
    parts, oks = [], []
    for name, sym in data.MARKETS.items():
        sm = data.index_summary(hist.get(sym, (data.empty_frame(), None))[0])
        ok = None if not sm or sm.get("above60") is None else bool(sm["above60"])
        oks.append(ok)
        parts.append(f"{name} " + ("-" if ok is None else f"60일선 {'위' if ok else '아래'} {sm['dist60']:+.1f}%"))
    market_ok = None if None in oks else all(oks)
    idx_rs = {name: data.index_rs(hist.get(sym, (data.empty_frame(), None))[0], df).get("rs")
              for name, sym in data.MARKETS.items()}
    market_text = " · ".join(parts)
    leaders = {g for g, _ in data.leading_groups(df, recent_days, min_count)}
    intraday = data.market_status(quotes).startswith("장중")

    pre = data.buy_screen(df, market_ok, market_text, leaders, p, fins=None, intraday=intraday, idx_rs=idx_rs)
    res = pd.DataFrame()
    if not pre.empty:
        codes = tuple(pre["code"])
        with st.spinner("후보 실적 확인 중…"):
            fins = data.fetch_financials_many(codes)
        res = data.buy_screen(df[df["code"].isin(codes)], market_ok, market_text, leaders, p, fins=fins,
                              intraday=intraday, idx_rs=idx_rs)
    counts = res["tier"].value_counts() if not res.empty else pd.Series(dtype=int)
    idx_txt = " · ".join(f"{n} {'✓' if o else ('✕' if o is False else '?')}" for n, o in zip(data.MARKETS, oks))
    tiles = [
        _tile(market_ok, "시장 60일선", idx_txt),
        _tile(bool(leaders), "주도섹터", f"{len(leaders)}개"),
        _tile(True if counts.get("매수 가능", 0) else None, "✅ 매수 가능", f"{counts.get('매수 가능', 0)}"),
        _tile(None, "⏳ 종가 확인 · ⚠️ 1개 미충족", f"{counts.get('종가 확인', 0)} · {counts.get('1개 미충족', 0)}"),
    ]
    st.markdown(f'<div class="tiles" style="margin-bottom:.6rem">{"".join(tiles)}</div>', unsafe_allow_html=True)
    if market_ok is False:
        st.error("⛔ 매수 쉬는 구간 — 코스피·코스닥 둘 다 60일선 위일 때만. 아래는 참고용이에요.")

    render_leaders(df, leaders)
    if res.empty:
        st.caption("원칙에 맞는 종목도, 하나만 빠지는 종목도 지금은 없어요.")
        return
    trends = data.fetch_stock_trends(tuple(res["code"]))
    labels = dict(data.BUY_RULES)
    rows = []
    for x in res.itertuples():
        r, c = x.row, x.checks
        plan = data.position_plan(float(r["price"]), r.get("atr_pct"), p["equity"], p)
        sm = data.trend_summary(trends.get(x.code))
        rows.append({
            "상태": f"{BUY_TIER_STYLE[x.tier][1]} {x.tier}", "종목": x.name,
            "섹터 순위": c["top"][1].split(" (")[0].split("/")[0],
            "현재가": r["price"], "신고가까지": r.get("to_high"), "돌파": _bo_full(r.to_dict()),
            "손절가": plan["stop_price"], "3R 목표가": plan["target_price"],
            "수량": plan["shares"], "매수금액": plan["amount"],
            "미충족": ", ".join(labels[k].split("(")[0] for k in x.fails) or "-",
            "등락률": r["change"], "RS": r.get("rs"), "ADX": r.get("adx"), "20일선 이격": r.get("dist_ma20"),
            "돌파일 거래량": r.get("bo_vol_ratio"), "DCR": r.get("bo_dcr"), "손절폭": plan["stop_pct"],
            "외국인 5일(억)": sm["flow5_외국인"], "기관 5일(억)": sm["flow5_기관"], "끌고 가기": _hold_short(r),
            "분류": x.group, "메모": "HTF — 리스크 타이트" if r.get("htf") else "",
        })
    view = pd.DataFrame(rows)
    base_cols = ["상태", "종목", "섹터 순위", "현재가", "신고가까지", "돌파", "손절가", "3R 목표가"] + \
        (["수량", "매수금액"] if p["equity"] else []) + ["미충족"]
    if not st.toggle("지표 자세히", key="buy_detail"):
        view = view[base_cols]
    elif not p["equity"]:
        view = view.drop(columns=["수량", "매수금액"])
    fmt = {"현재가": "{:,.0f}", "등락률": "{:+.2f}%", "신고가까지": "{:+.1f}%", "RS": "{:.0f}", "ADX": "{:.0f}",
           "20일선 이격": "{:+.1f}%", "돌파일 거래량": "{:.1f}배", "DCR": "{:.0f}%", "손절가": "{:,.0f}",
           "손절폭": "{:.1f}%", "3R 목표가": "{:,.0f}", "수량": "{:,.0f}주", "매수금액": "{:,.0f}",
           "외국인 5일(억)": _eok_num, "기관 5일(억)": _eok_num}
    sty = view.style.format({k: v for k, v in fmt.items() if k in view.columns}, na_rep="-") \
        .map(lambda v: f"color: {BUY_TIER_STYLE[v.split(' ', 1)[1]][0]}; font-weight: 700", subset=["상태"]) \
        .map(_bo_color, subset=["돌파"]).map(_to_high_color, subset=["신고가까지"])
    for col in ("등락률", "외국인 5일(억)", "기관 5일(억)"):
        if col in view.columns:
            sty = sty.map(lambda v: "" if pd.isna(v) or v == 0 else f"color: {UP if v > 0 else DOWN}", subset=[col])
    st.dataframe(sty, hide_index=True, height=min(520, 36 * (len(view) + 1) + 4),
                 column_config={"종목": st.column_config.Column(pinned=True),
                                "돌파": st.column_config.Column(help=BO_FULL_HELP)})

    good = res[res["tier"].isin(["매수 가능", "종가 확인", "시장 대기"])]
    for x in good.itertuples():
        with st.expander(f"{BUY_TIER_STYLE[x.tier][1]} {x.name} · 조건 체크"):
            st.markdown('<div class="tiles">' + "".join(
                _tile(x.checks[k][0], lab.split("(")[0], x.checks[k][1][:22]) for k, lab in data.BUY_RULES) + "</div>",
                unsafe_allow_html=True)
            if x.tier == "종가 확인":
                st.caption("오늘 장중 돌파 — 종가가 직전 52주 최고가 위에서 마감하는지 확인.")
    with st.expander("ℹ️ 표 읽는 법"):
        st.markdown("- ⏳ 종가 확인 = 오늘 장중 돌파 · ⛔ 시장 대기 = 종목은 통과, 시장 미충족 · ⚠️ 1개 미충족 = 관심 종목(매수 대상 아님)\n"
                    "- 손절가 = 현재가 기준 1R(8%, ATR이 8% 이상이면 ATR) · 수량 = 계좌 1.5% 위험 · 3R 목표가에서 절반 익절\n"
                    "- 해외 종목은 제외 · 종목별 수급의 연기금·투신은 기관 합계에 포함 · 마지막 확인은 HTS 차트로")


def render_checks(df: pd.DataFrame, quote_error: str | None, shares_store: dict | None = None):
    failed = df[df["price"].isna()]
    mismatch = df[df["name_ok"] == False]  # noqa: E712
    cap_missing = int(df["cap_krw"].isna().sum())
    fixed = df[df["h52_fixed"] == True] if "h52_fixed" in df else df.iloc[0:0]  # noqa: E712
    if not fixed.empty:
        with st.expander(f"🔧 52주 최고·최저 교정 {len(fixed)}종목 — 네이버 공식값으로 고쳐서 보여주고 있어요"):
            view = pd.DataFrame({
                "종목": fixed["name"], "코드": fixed["code"],
                "앱 계산 최고": fixed["high52_calc"], "네이버 최고": fixed["high52"],
                "차이(%)": pd.to_numeric(fixed["h52_diff"], errors="coerce").round(1),
                "앱 계산 최저": fixed["low52_calc"], "네이버 최저": fixed["low52"],
            })
            st.dataframe(view, hide_index=True)
            st.caption("일봉 데이터로 계산한 값이 네이버 공식 52주 최고·최저와 0.3% 넘게 다른 종목이에요. "
                       "주로 유·무상증자·액면분할로 과거 주가가 수정된 종목이에요. 표·괴리율·신고가까지는 공식값으로 보여주고, "
                       "일봉 기준 돌파 판정이 틀릴 수 있어서 🎯 매수 후보에서는 '판정 보류'로 빼요. 매매 전에는 HTS 차트로 확인하세요.")
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


# ─────────────────────────── 🏁 신고가 후보 ───────────────────────────
NHC_CSS = """
<style>
.nh-head { display: flex; align-items: baseline; justify-content: space-between; gap: 0.6rem; flex-wrap: wrap; margin: 0.2rem 0 0.7rem; }
.nh-head b { font-size: 1.35rem; font-weight: 800; color: #16212B; letter-spacing: -0.02em; }
.nh-head span { font-size: 0.8rem; color: #7A8A94; }
.nh-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0.9rem; margin-bottom: 1.3rem; }
.nh-box { background: #FFFFFF; border: 1px solid #E3E8EB; border-radius: 12px; overflow: hidden; }
.nh-box-h { background: #F7F9FA; border-bottom: 1px solid #EEF1F3; padding: 0.75rem 1rem 0.65rem; }
.nh-box-t { display: flex; justify-content: space-between; align-items: center; font-size: 1.05rem; font-weight: 800; color: #16212B; }
.nh-box-t .cnt { font-size: 1.2rem; font-weight: 800; font-variant-numeric: tabular-nums; }
.nh-box-d { font-size: 0.78rem; color: #6B7A84; margin-top: 0.15rem; }
.nh-box-b { padding: 0.6rem 0.7rem 0.7rem; display: flex; flex-direction: column; gap: 0.5rem; max-height: 470px; overflow-y: auto; }
.nh-empty { text-align: center; color: #6B7A84; font-size: 0.85rem; padding: 1.6rem 0; }
.stApp a.nh-card { display: block; border: 1px solid #E3E8EB; border-radius: 10px; padding: 0.6rem 0.7rem 0.55rem;
  text-decoration: none; color: #16212B; background: #FFFFFF; }
.stApp a.nh-card:hover { border-color: #B9C7CF; }
.nh-c-top { display: flex; justify-content: space-between; align-items: center; gap: 0.5rem; }
.nh-c-name { font-weight: 800; font-size: 1rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.nh-c-chg { font-weight: 800; font-variant-numeric: tabular-nums; white-space: nowrap; }
.nh-c-sub { font-size: 0.75rem; color: #6B7A84; margin: 0.1rem 0 0.45rem; }
.nh-c-keys { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0.35rem; }
.nh-c-keys div { background: #F2F5F7; border-radius: 7px; padding: 0.3rem 0.5rem; font-size: 0.68rem; color: #6B7A84; min-width: 0;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.nh-c-keys b { display: block; font-size: 0.95rem; font-weight: 800; color: #16212B; white-space: nowrap; font-variant-numeric: tabular-nums; }
.nh-c-keys b small { font-size: 0.7rem; font-weight: 600; color: #6B7A84; margin-left: 0.1rem; }
.nh-pill { display: inline-flex; align-items: center; gap: 0.3rem; font-size: 0.72rem; font-weight: 700; padding: 0.15rem 0.5rem;
  border-radius: 6px; white-space: nowrap; }
.nh-pill::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.nh-pill.s-돌파 { background: #FCE4E5; color: #C0262E; }
.nh-pill.s-터치 { background: #FFF1C9; color: #9A6A00; }
.nh-pill.s-임박 { background: #FFE8D6; color: #C2570E; }
.nh-pill.s-근접 { background: #E3F4EA; color: #1B6B3E; }
.nh-pill.s-관찰 { background: #EEF1F3; color: #51616C; }
.nh-tbl-wrap { background: #FFFFFF; border: 1px solid #E3E8EB; border-radius: 12px; overflow-x: auto; }
table.nh-tbl { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
.nh-tbl th { background: #F7F9FA; font-size: 0.78rem; font-weight: 700; color: #51616C; padding: 0.6rem 0.6rem; text-align: right;
  white-space: nowrap; border-bottom: 1px solid #E3E8EB; }
.nh-tbl td { padding: 0.55rem 0.6rem; border-top: 1px solid #F0F3F5; text-align: right; white-space: nowrap; font-size: 0.86rem; vertical-align: middle; }
.nh-tbl tr:first-child td { border-top: none; }
.nh-tbl th.l, .nh-tbl td.l { text-align: left; } .nh-tbl th.c, .nh-tbl td.c { text-align: center; }
.nh-tbl td.rk { color: #6B7A84; }
.nh-tbl td.nm a { color: #16212B; font-weight: 800; text-decoration: none; font-size: 0.93rem; }
.nh-tbl td small { display: block; font-size: 0.72rem; color: #7A8A94; font-weight: 500; margin-top: 0.05rem; }
.nh-tbl td.sp { min-width: 170px; }
.nh-tbl .sp-lab { font-size: 0.8rem; margin-bottom: 0.1rem; }
.nh-tbl .sp-lab b { font-weight: 800; } .nh-tbl .sp-lab span { color: #7A8A94; font-size: 0.74rem; margin-left: 0.15rem; }
.nh-tbl td b.px { font-weight: 800; color: #16212B; }
.nh-tbl td b.rs { font-weight: 800; font-size: 0.95rem; color: #16212B; }
.vs-tag { display: inline-block; font-size: 0.68rem; font-weight: 700; padding: 0.05rem 0.4rem; border-radius: 6px; margin-left: 0.3rem; }
.vs-tag.first { background: #FCE4E5; color: #C0262E; } .vs-tag.multi { background: #FFE8D6; color: #C2570E; }
.vs-tag.dn { background: #E4EDFA; color: #1F66C9; }
@media (max-width: 640px) {
  .nh-grid { grid-template-columns: 1fr; gap: 0.6rem; }
  .nh-box-b { max-height: 430px; }
  .nh-tbl .hide-m { display: none; }
  .nh-tbl td, .nh-tbl th { padding: 0.45rem 0.4rem; font-size: 0.8rem; }
  .nh-tbl td.sp { min-width: 130px; }
}
</style>
"""

NHC_SECTIONS = [
    ("신선 후보", "✳️", "#1B8A4B", "최근 돌파 이력이 없고 거래대금과 RS가 살아난 후보예요."),
    ("돌파권", "🎯", "#C27C0E", "신고가 기준가까지 가까워 관찰 우선순위가 높아요."),
    ("돌파 중", "📈", "#D6333B", "현재가가 기준가(직전 52주 최고가) 위에 있어요."),
    ("터치 후 밀림", "〰️", "#1F66C9", "오늘 신고가를 터치했지만 기준 아래로 밀린 재관찰 후보예요."),
]
NHC_LABEL = {"돌파": "돌파성공", "터치": "터치 후 밀림", "임박": "임박", "근접": "근접", "관찰": "관찰"}


def _spark_svg(vals, ref: float | None, up: bool, w: int = 160, hgt: int = 34) -> str:
    """종가 스파크라인 + 신고가 기준선(점선). 끝점: 돌파 초록, 아니면 빨강."""
    vals = [v for v in (vals or []) if v == v]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals + ([ref] if ref else [])), max(vals + ([ref] if ref else []))
    rng = (hi - lo) or 1.0
    pad = 3
    def y(v):
        return pad + (hgt - 2 * pad) * (1 - (v - lo) / rng)
    step = (w - 2 * pad) / (len(vals) - 1)
    pts = " ".join(f"{pad + i * step:.1f},{y(v):.1f}" for i, v in enumerate(vals))
    end_x, end_y = pad + (len(vals) - 1) * step, y(vals[-1])
    parts = [f'<svg width="{w}" height="{hgt}" viewBox="0 0 {w} {hgt}" style="display:block">']
    if ref:
        ry = y(ref)
        fill = "#E3F4EA" if up else "#FCE9EA"
        parts.append(f'<rect x="0" y="0" width="{w}" height="{max(ry, 0):.1f}" fill="{fill}" opacity="0.55"/>')
        parts.append(f'<line x1="0" x2="{w}" y1="{ry:.1f}" y2="{ry:.1f}" stroke="#E08A8F" stroke-width="1" stroke-dasharray="3 3"/>')
    parts.append(f'<polyline points="{pts}" fill="none" stroke="#5B6770" stroke-width="1.3" stroke-linejoin="round"/>')
    parts.append(f'<circle cx="{end_x:.1f}" cy="{end_y:.1f}" r="2.8" fill="{"#1FA75A" if up else "#D6333B"}"/>')
    parts.append("</svg>")
    return "".join(parts)


def _chg_html(v) -> str:
    if v is None or pd.isna(v):
        return "-"
    color = UP if v > 0 else DOWN if v < 0 else "#51616C"
    return f'<span style="color:{color}">{v:+.2f}%</span>'


def _tv_eok(v) -> str:
    if v is None or pd.isna(v):
        return "-"
    e = v / 1e8
    return f"{e / 1e4:,.2f}조" if e >= 1e4 else f"{e:,.0f}억"


def _dist_html(r, big: bool = False) -> tuple[str, str]:
    """('10.40%', '돌파') / ('3.83%', '남음')"""
    if r["nh_state"] == "돌파":
        return f'<b style="color:{UP}">{r["nh_brk"]:.2f}%</b>', "돌파"
    return f'<b>{max(r["nh_dist"], 0):.2f}%</b>', "남음"


def _nh_card(r) -> str:
    brk = r["nh_state"] == "돌파"
    head, lab = ("고가에서", "돌파") if brk else ("고가까지", "남음")
    num = f'<span style="color:{UP}">{r["nh_brk"]:.2f}%</span>' if brk else f'{max(r["nh_dist"], 0):.2f}%'
    tvr = "-" if pd.isna(r["tv_ratio"]) else f"{r['tv_ratio']:.1f}배"
    rs = "-" if pd.isna(r["rs"]) else f"{r['rs']:.0f}"
    rs1 = "-" if pd.isna(r["rs_1m"]) else f"{r['rs_1m']:.0f}"
    return (
        f'<a class="nh-card" href="?chart={html.escape(str(r["code"]))}" target="_self">'
        f'<div class="nh-c-top"><span class="nh-c-name">{html.escape(str(r["name"]))} '
        f'<span class="nh-pill s-{r["nh_state"]}">{NHC_LABEL[r["nh_state"]]}</span></span>'
        f'<span class="nh-c-chg">{_chg_html(r["change"])}</span></div>'
        f'<div class="nh-c-sub">{r["code"]} · {html.escape(str(r["group"]))}</div>'
        f'<div class="nh-c-keys"><div>{head}<b>{num}<small>{lab}</small></b></div>'
        f'<div>거래대금 20일 평균대비<b>{tvr}</b></div><div>RS / RS1M<b>{rs} / {rs1}</b></div></div></a>'
    )


def _nh_params() -> dict:
    d = data.NHC_DEFAULTS
    with st.expander("⚙️ 후보 기준 바꾸기"):
        c1, c2, c3 = st.columns(3)
        p = {
            "imminent": c1.slider("임박: 기준가까지 %", 0.5, 5.0, float(d["imminent"]), 0.5, key="nhc_imm"),
            "near": c2.slider("근접: 기준가까지 %", 3.0, 15.0, float(d["near"]), 0.5, key="nhc_near"),
            "watch": c3.slider("관찰: 기준가까지 %", 8.0, 30.0, float(d["watch"]), 1.0, key="nhc_watch"),
        }
        c4, c5, c6 = st.columns(3)
        p["tv_min"] = c4.slider("돌파권·신선: 거래대금 20일 평균대비(배)", 1.0, 5.0, float(d["tv_min"]), 0.1, key="nhc_tv")
        p["rs_min"] = c5.slider("돌파권·신선: 종합 RS 이상", 50, 99, int(d["rs_min"]), 1, key="nhc_rs")
        p["fresh_days"] = c6.slider("신선: 최근 며칠간 신고가 없음(거래일)", 20, 120, int(d["fresh_days"]), 5, key="nhc_fresh")
    return p


def render_nh_candidates(df: pd.DataFrame, histories: dict, quotes: dict):
    st.markdown(NHC_CSS, unsafe_allow_html=True)
    st.markdown(f'<div class="nh-head"><b>🏁 신고가 후보</b><span>기준: {data.now_kst():%y.%m.%d %H:%M} · '
                f'{data.market_status(quotes)} · 국내 종목 전체</span></div>', unsafe_allow_html=True)
    with st.expander("읽는 법"):
        st.markdown(
            "- **기준가** = 오늘을 뺀 직전 52주(250거래일) 장중 최고가. 오늘 값은 실시간 시세로 계산해요.\n"
            "- **돌파성공** 현재가 > 기준가 · **터치 후 밀림** 오늘 고가는 기준가를 넘었지만 현재가는 아래 · "
            "**임박/근접/관찰** 기준가까지 남은 % (기본 2% / 7% / 15%)\n"
            "- **거래대금 20일 평균대비** = 오늘 거래대금 ÷ 직전 20거래일 평균 거래대금 (장중엔 쌓이는 중이라 작게 나와요)\n"
            "- **돌파권** = 임박·근접 중 거래대금 1.5배 이상 + RS 80 이상 · "
            "**신선 후보** = 돌파·터치·임박·근접 중 같은 조건 + 최근 60거래일 동안 52주 신고가를 쓴 적 없음\n"
            "- 카드를 누르면 네이버 증권이 열려요. 매수 판단은 🎯 매수 후보 탭의 원칙으로 한 번 더 걸러요.")
    p = _nh_params()
    cand = data.nh_candidates(df, histories, quotes, p)
    if cand.empty:
        st.info("지금 조건에 맞는 신고가 후보가 없어요.")
        return

    masks = {"신선 후보": cand["fresh"], "돌파권": cand["section"] == "돌파권",
             "돌파 중": cand["nh_state"] == "돌파", "터치 후 밀림": cand["nh_state"] == "터치"}
    boxes = []
    for sec, icon, color, desc in NHC_SECTIONS:
        sub = cand[masks[sec]]
        body = "".join(_nh_card(r) for _, r in sub.head(12).iterrows()) or '<div class="nh-empty">해당 종목이 없습니다.</div>'
        more = f'<div class="nh-empty" style="padding:0.3rem 0">외 {len(sub) - 12}개는 아래 리스트에서</div>' if len(sub) > 12 else ""
        boxes.append(
            f'<div class="nh-box"><div class="nh-box-h"><div class="nh-box-t"><span>{icon} {sec}</span>'
            f'<span class="cnt" style="color:{color}">{len(sub)}</span></div><div class="nh-box-d">{desc}</div></div>'
            f'<div class="nh-box-b">{body}{more}</div></div>')
    st.markdown(f'<div class="nh-grid">{"".join(boxes)}</div>', unsafe_allow_html=True)

    # ── 상세 후보 리스트 ──
    st.markdown('<div class="nh-head"><b style="font-size:1.15rem">상세 후보 리스트</b></div>', unsafe_allow_html=True)
    counts = cand["nh_state"].value_counts()
    opts = ["전체"] + list(data.NHC_STATES)
    c1, c2, c3 = st.columns([4, 1.2, 1.4])
    pick = c1.segmented_control("상태", opts, default="전체", key="nhc_pick", label_visibility="collapsed",
                                format_func=lambda s: f"{s} {len(cand) if s == '전체' else int(counts.get(s, 0))}")
    sort_by = c2.selectbox("정렬", ["고가까지", "등락률", "거래대금 배수", "RS", "ATR%"], key="nhc_sort",
                           label_visibility="collapsed")
    q = c3.text_input("종목·코드 검색", key="nhc_q", placeholder="🔍 종목·코드 검색", label_visibility="collapsed")
    v = cand if pick in (None, "전체") else cand[cand["nh_state"] == pick]
    if q:
        v = v[v["name"].str.contains(q, case=False, na=False) | v["code"].str.contains(q, na=False)]
    key = {"등락률": "change", "거래대금 배수": "tv_ratio", "RS": "rs", "ATR%": "atr_pct"}.get(sort_by)
    if key:
        v = v.sort_values(key, ascending=False, na_position="last")
    if v.empty:
        st.info("해당 종목이 없어요.")
        return
    rows = []
    for i, (_, r) in enumerate(v.head(150).iterrows(), 1):
        val, lab = _dist_html(r)
        brk = r["nh_state"] == "돌파"
        rows.append(
            f'<tr><td class="c rk hide-m">{i}</td>'
            f'<td class="l nm"><a href="?chart={html.escape(str(r["code"]))}" target="_self">{html.escape(str(r["name"]))}</a>'
            f'<small>{r["code"]}</small></td>'
            f'<td class="l hide-m">{html.escape(str(r["group"]))}</td>'
            f'<td class="c"><span class="nh-pill s-{r["nh_state"]}">{NHC_LABEL[r["nh_state"]]}</span></td>'
            f'<td class="sp"><div class="sp-lab">{val}<span>{lab}</span></div>'
            f'{_spark_svg(r["spark"], r["ref_high"], brk)}</td>'
            f'<td class="hide-m">{_pct(r["atr_pct"])}</td>'
            f'<td><b class="px">{r["price"]:,.0f}원</b><small>{r["ref_high"]:,.0f}원</small></td>'
            f'<td><b>{_chg_html(r["change"])}</b></td>'
            f'<td><b class="px">{_tv_eok(r["tv_today"])}</b><small>20평균 '
            f'{"-" if pd.isna(r["tv_ratio"]) else format(r["tv_ratio"], ".1f") + "배"}</small></td>'
            f'<td><b class="rs">{_n(r["rs"])}</b><small>1M {_n(r["rs_1m"])}</small></td></tr>')
    head = ('<tr><th class="c hide-m">순위</th><th class="l">종목</th><th class="l hide-m">섹터</th><th class="c">상태</th>'
            '<th>고가까지</th><th class="hide-m">ATR%</th><th>현재가 / 52주고가</th><th>등락률</th><th>거래대금</th><th>RS</th></tr>')
    st.markdown(f'<div class="nh-tbl-wrap"><table class="nh-tbl">{head}{"".join(rows)}</table></div>',
                unsafe_allow_html=True)
    if len(v) > 150:
        st.caption(f"앞 150개만 보여줘요(전체 {len(v)}개). 상태 필터나 검색으로 좁혀 보세요.")
    st.caption("52주고가 = 오늘을 뺀 직전 250거래일 최고가(돌파 판정 기준가) · 스파크라인은 최근 120거래일 종가, 점선이 기준가")


# ─────────────────────────── 💥 거래량 폭발 ───────────────────────────
def _vol_svg(vol, close, split: int | None, recent: int, mult_line: float | None = None, w: int = 220, hgt: int = 44) -> str:
    """거래량 막대(최근 구간 빨강, 조용한 구간 회색 배경) + 종가 선."""
    vol = [x or 0 for x in (vol or [])]
    close = [x for x in (close or [])]
    n = len(vol)
    if n < 10:
        return ""
    vmax = max(vol) or 1
    bw = w / n
    parts = [f'<svg width="{w}" height="{hgt}" viewBox="0 0 {w} {hgt}" style="display:block">']
    if split is not None:
        s = max(0, split)
        parts.append(f'<rect x="{s * bw:.1f}" y="0" width="{(n - recent - s) * bw:.1f}" height="{hgt}" fill="#EEF1F3"/>')
    for i, x in enumerate(vol):
        bh = (hgt - 2) * x / vmax
        col = "#D6333B" if i >= n - recent else "#9AA9B3"
        parts.append(f'<rect x="{i * bw:.2f}" y="{hgt - bh:.1f}" width="{max(bw - 0.4, 0.6):.2f}" height="{bh:.1f}" fill="{col}"/>')
    cl = [c for c in close if c == c]
    if len(cl) >= 2:
        lo, hi = min(cl), max(cl)
        rng = (hi - lo) or 1
        pts = " ".join(f"{(i + 0.5) * bw:.1f},{2 + (hgt * 0.55) * (1 - (c - lo) / rng):.1f}"
                       for i, c in enumerate(close) if c == c)
        parts.append(f'<polyline points="{pts}" fill="none" stroke="#16212B" stroke-width="1.1" opacity="0.8"/>')
    parts.append("</svg>")
    return "".join(parts)


def _vs_chart(hist: pd.DataFrame, name: str, recent: int, quiet: int):
    """일진전기 화면처럼: 위 캔들 + 이동평균, 아래 거래량(상승 빨강·하락 파랑)."""
    h = hist.tail(250).copy()
    h["date"] = pd.to_datetime(h["date"])
    for n_ in (5, 20, 60, 120):
        h[f"MA{n_}"] = hist["close"].rolling(n_).mean().tail(250).values
    h["up"] = h["close"] >= h["open"]
    base = alt.Chart(h).encode(x=alt.X("date:T", title=None, axis=alt.Axis(format="%y.%m", labelAngle=0, grid=False)))
    color = alt.condition("datum.up", alt.value(UP), alt.value(DOWN))
    wick = base.mark_rule().encode(y=alt.Y("low:Q", title=None, scale=alt.Scale(zero=False)), y2="high:Q", color=color)
    body = base.mark_bar(width=2.4).encode(y="open:Q", y2="close:Q", color=color,
                                           tooltip=[alt.Tooltip("date:T", format="%Y-%m-%d"), alt.Tooltip("close:Q", format=",.0f"),
                                                    alt.Tooltip("volume:Q", format=",.0f")])
    ma = alt.Chart(h.melt("date", value_vars=["MA5", "MA20", "MA60", "MA120"], var_name="선", value_name="값").dropna()) \
        .mark_line(strokeWidth=1.2).encode(
            x="date:T", y="값:Q",
            color=alt.Color("선:N", scale=alt.Scale(domain=["MA5", "MA20", "MA60", "MA120"],
                                                    range=["#1FA75A", "#D6333B", "#F29A18", "#8E5CC4"]),
                            legend=alt.Legend(orient="top", title=None)))
    top = (wick + body + ma).properties(height=240, width="container", title=name)
    qstart = h["date"].iloc[max(0, len(h) - recent - quiet)]
    rstart = h["date"].iloc[max(0, len(h) - recent)]
    band = alt.Chart(pd.DataFrame({"s": [qstart], "e": [rstart]})).mark_rect(color="#EEF1F3", opacity=0.9) \
        .encode(x="s:T", x2="e:T")
    vol = base.mark_bar(width=2.4).encode(y=alt.Y("volume:Q", title=None, axis=alt.Axis(format="~s")), color=color)
    bottom = (band + vol).properties(height=110, width="container")
    st.altair_chart(top)
    st.altair_chart(bottom)


def render_volume_surge(df: pd.DataFrame, histories: dict, quotes: dict):
    st.markdown(NHC_CSS, unsafe_allow_html=True)
    st.markdown('<div class="nh-head"><b>💥 거래량 폭발</b><span>몇 달 조용하던 거래량이 갑자기 터진 국내 종목</span></div>',
                unsafe_allow_html=True)
    d = data.VS_DEFAULTS
    c1, c2, c3, c4 = st.columns(4)
    quiet = c1.segmented_control("조용했던 기간", [60, 90, 120], default=int(d["quiet"]), key="vs_quiet",
                                 format_func=lambda x: f"{x}일")
    recent = c2.segmented_control("최근 며칠 안에 터졌나", [1, 3, 5, 10], default=int(d["recent"]), key="vs_recent",
                                  format_func=lambda x: "오늘" if x == 1 else f"{x}일")
    mult = c3.slider("폭발 배수(평소 거래량의 몇 배)", 2.0, 10.0, float(d["mult"]), 0.5, key="vs_mult")
    min_tv = c4.number_input("폭발일 거래대금 최소(억)", 0, 5000, int(d["min_tv"] / 1e8), 5, key="vs_mintv")
    with st.expander("⚙️ 조용함 기준 · 추가 필터"):
        e1, e2, e3 = st.columns(3)
        quiet_cap = e1.slider("조용함: 5일 평균 거래량이 평소의 몇 배를 안 넘었나", 1.3, 4.0, float(d["quiet_cap"]), 0.1,
                              key="vs_qcap", help="낮출수록 더 '말라 있던' 종목만 남아요. 하루 이틀 튄 날은 5일 평균이라 괜찮아요.")
        only_quiet = e1.toggle("조용함 조건 적용", value=True, key="vs_onlyq")
        only_up = e2.toggle("폭발일이 양봉인 종목만", value=True, key="vs_up",
                            help="거래량이 터진 날 종가가 시가 위(빨간 봉)인 경우만. 대량 음봉(투매)은 빼요.")
        only_above = e2.toggle("최근 수익률 플러스만", value=False, key="vs_pos")
        max_to_high = e3.slider("신고가까지 % 이내만 (100=전체)", 5, 100, 100, 5, key="vs_toh")
    quiet, recent = quiet or int(d["quiet"]), recent or int(d["recent"])
    p = {"quiet": quiet, "recent": recent, "mult": mult, "quiet_cap": quiet_cap, "min_tv": min_tv * 1e8}
    res = data.volume_surges(df, histories, quotes, p, only_quiet=only_quiet)
    if not res.empty:
        if only_up:
            res = res[res["vs_peak_up"] == True]  # noqa: E712
        if only_above:
            res = res[pd.to_numeric(res["vs_ret"], errors="coerce") > 0]
        if max_to_high < 100:
            res = res[pd.to_numeric(res["to_high"], errors="coerce") <= max_to_high]
    if res.empty:
        st.info("지금 조건에 맞는 종목이 없어요. 폭발 배수를 낮추거나 기간을 늘려 보세요.")
        return
    today = data.now_kst().date()
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("찾은 종목", f"{len(res)}")
    k2.metric("오늘 터짐", f"{int((res['vs_since'] == 0).sum())}")
    k3.metric("2일 이상 연속·반복", f"{int((res['vs_days'] >= 2).sum())}")
    k4.metric("신고가 10% 이내", f"{int((pd.to_numeric(res['to_high'], errors='coerce') <= 10).sum())}")

    sort_by = st.segmented_control("정렬", ["폭발 배수", "최근 수익률", "말라 있던 정도", "RS", "신고가까지"],
                                   default="폭발 배수", key="vs_sort", label_visibility="collapsed")
    key, asc = {"최근 수익률": ("vs_ret", False), "말라 있던 정도": ("vs_dry", True), "RS": ("rs", False),
                "신고가까지": ("to_high", True)}.get(sort_by or "", ("vs_mult", False))
    res = res.sort_values(key, ascending=asc, na_position="last").reset_index(drop=True)

    rows = []
    for i, r in res.head(120).iterrows():
        tag = ""
        if r["vs_days"] >= 2:
            tag = f'<span class="vs-tag multi">{int(r["vs_days"])}일 폭발</span>'
        elif r["vs_days"] == 1:
            tag = '<span class="vs-tag first">1회 폭발</span>'
        if r["vs_peak_up"] is False:
            tag += '<span class="vs-tag dn">음봉</span>'
        pk = r["vs_peak_date"]
        pk_txt = "오늘" if pk == today else f"{pk:%m/%d}"
        dry = "-" if pd.isna(r["vs_dry"]) else f"{r['vs_dry']:.2f}"
        rows.append(
            f'<tr><td class="c rk hide-m">{i + 1}</td>'
            f'<td class="l nm"><a href="?chart={html.escape(str(r["code"]))}" target="_self">{html.escape(str(r["name"]))}</a>{tag}'
            f'<small>{r["code"]} · {html.escape(str(r["group"]))}</small></td>'
            f'<td class="sp">{_vol_svg(r["vs_vol"], r["vs_close"], r["vs_split"], recent)}</td>'
            f'<td><b class="rs" style="color:{UP}">{r["vs_mult"]:.1f}배</b><small>{pk_txt} · 평균 {r["vs_avg_mult"]:.1f}배</small></td>'
            f'<td><b class="px">{_tv_eok(r["vs_peak_tv"])}</b><small>폭발일 거래대금</small></td>'
            f'<td>{_chg_html(r["vs_ret"])}<small>오늘 {_chg_html(r["change"])}</small></td>'
            f'<td class="hide-m"><b>{dry}</b><small>횡보폭 {_pct(r["vs_range"])}</small></td>'
            f'<td><b>{"-" if pd.isna(r["to_high"]) else format(r["to_high"], ".1f") + "%"}</b>'
            f'<small>{"정배열" if r.get("aligned") == True else ""}</small></td>'  # noqa: E712
            f'<td><b class="rs">{_n(r["rs"])}</b><small>1M {_n(r["rs_1m"])}</small></td></tr>')
    head = ('<tr><th class="c hide-m">순위</th><th class="l">종목</th>'
            f'<th class="l">거래량 {len(res.iloc[0]["vs_vol"] or [])}일 (회색=조용한 {quiet}일)</th>'
            f'<th>폭발 배수</th><th>거래대금</th><th>최근 {recent}일</th><th class="hide-m">거래 수준</th>'
            '<th>신고가까지</th><th>RS</th></tr>')
    st.markdown(f'<div class="nh-tbl-wrap"><table class="nh-tbl">{head}{"".join(rows)}</table></div>',
                unsafe_allow_html=True)
    st.caption(f"폭발 배수 = 최근 {recent}거래일 중 가장 큰 거래량 ÷ 그 앞 {quiet}거래일 거래량 중앙값 · "
               "평균 = 최근 구간 평균 배수 · 거래 수준 = 조용한 기간 중앙값 ÷ 그 앞 1년 중앙값(1보다 작을수록 평소보다 말라 있었음) · "
               "횡보폭 = 조용한 기간 종가 최고/최저 차이")

    labels = {r.code: f"{r.name} ({r.code}) · {r.vs_mult:.1f}배" for r in res.head(120).itertuples()}
    code = st.selectbox("차트로 보기", list(labels), format_func=labels.get, key="vs_chart_code")
    hist = (histories.get(code) or (None, data.empty_frame(), None))[1]
    if hist is not None and not hist.empty:
        _vs_chart(hist, labels[code], recent, quiet)
    with st.expander("ℹ️ 이 탭을 쓰는 법"):
        st.markdown(
            "- 일진전기처럼 **몇 달 동안 거래량이 말라 있다가** 최근 며칠 사이 평소의 몇 배가 터진 종목을 찾아요.\n"
            "- 조용함 조건: 조용한 기간 동안 **5일 평균 거래량**이 평소(중앙값)의 N배를 넘은 적이 없어야 해요. "
            "하루 튄 날은 괜찮지만, 며칠씩 거래가 붙었던 종목은 빠져요.\n"
            "- 거래량 폭발 자체는 매수 신호가 아니에요. 폭발 뒤 **고가 근처에서 버티는지(눌림 거래량 감소)**, "
            "신고가 돌파로 이어지는지를 🏁 신고가 후보 · 🎯 매수 후보 탭과 같이 보세요.\n"
            "- 보드에 넣은 종목(stocks.py) 안에서만 찾아요. 오늘 봉은 실시간 누적 거래량이라 장중엔 계속 커져요.")


_QUOTES: dict = {}


def render_board():
    histories = {**load_histories(KR_CODES), **load_histories_overseas(OS_CODES)}
    quotes, quote_error = load_quotes(KR_CODES)
    _QUOTES.clear()
    _QUOTES.update(quotes)
    shares_store = load_shares()
    df = data.build_table(STOCKS, histories, quotes, shares_store["data"], load_fx(), bo_mode=bo_mode,
                          monthlies=load_monthlies(ALL_CODES), official52=load_official_52w(KR_CODES))
    df["cap_krw"] = pd.to_numeric(df["cap_krw"], errors="coerce")
    df["cap_local"] = pd.to_numeric(df["cap_local"], errors="coerce")

    interval = REFRESH[refresh_label]
    refresh_text = f"{refresh_label}마다 새로 불러와요." if interval else "자동 새로고침은 꺼져 있어요."
    st.markdown(
        f'<div class="status">{data.now_kst():%m/%d %H:%M:%S} · {data.market_status(quotes)} · {refresh_text}</div>',
        unsafe_allow_html=True,
    )
    if data.MOCK:
        st.info("가짜 데이터로 보여주는 테스트 모드예요. 실제 시세를 보려면 STOCK_MOCK 설정 없이 실행하세요.")

    leaders_now = {g for g, _ in data.leading_groups(df, recent_days, min_count)}
    trends_all, fins_all = load_trends_all(), load_fins_all()
    render_market(df)
    render_radar(df[df["code"].map(_mkt) == market])
    with st.container(key="main_tabs"):
        t_list, t_hold, t_chart, t_rep, t_nh, t_vs, t_buy, t_money, t_eng, t_next, t_scn = st.tabs(
            ["📋 리스트", "💼 내 보유", "🕯️ 차트", "📥 리포트", "🏁 신고가 후보", "💥 거래량 폭발", "🎯 매수 후보",
             "💰 거래대금", "🚀 급상승", "🔭 차기 주도", "🧭 시나리오"])
    with t_hold:
        render_holdings_tab(df, quotes)
    with t_rep:
        render_reports_tab()
    with t_chart:
        render_chart_tab(df, quotes)
    with t_money:
        render_money(df)
    with t_nh:
        render_nh_candidates(df, histories, quotes)
    with t_vs:
        render_volume_surge(df, histories, quotes)
    with t_buy:
        render_buy(df, quotes)
    with t_eng:
        render_engine(df, trends_all, leaders_now, fins_all)
    with t_next:
        render_next(df, fins_all, trends_all, leaders_now)
    with t_scn:
        render_scenario(df, histories, trends_all, leaders_now, fins_all)
    with t_list:
        render_list(df, histories, quote_error, shares_store, fins_all)


def render_list(df, histories, quote_error, shares_store, fins_all=None):
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
    n_first = int(((f["bo_status"] == "유지") & (pd.to_numeric(f["bo_nth"], errors="coerce") == 1)).sum())

    with st.container(key="desk_kpis"):
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("종목", f"{len(f)}")
        c2.metric(f"최근 {recent_days}일 신고가", f"{n_hot}")
        c3.metric("돌파 유지", f"{n_holding}", f"⭐ 첫 돌파 {n_first}", delta_color="off")
        c4.metric("신고가까지 10% 이내", f"{n_near}")
        c5.metric("정배열", f"{n_aligned}")

    if f.empty:
        st.info("조건에 맞는 종목이 없어요. 왼쪽 필터를 넓혀 보세요.")
    else:
        with st.container(key="desk_table"):
            render_table(f)
            st.caption("노란 줄 = 최근 신고가 · 신고가까지 빨강 3% 이내, 주황 10% 이내 · 수급은 억원(추정) · 해외는 현지 통화, 15분 지연")
        with st.container(key="mobile_view"):
            render_cards(f, n_hot, n_near, n_aligned, fins_all)
        render_detail(f, histories, trends)
    render_checks(df, quote_error, shares_store)


st.title("밸류체인 신고가 보드")
st.fragment(render_board, run_every=REFRESH[refresh_label])()
