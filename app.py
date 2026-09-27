"""밸류체인 신고가 보드.

실행: streamlit run app.py   (윈도우는 run.bat 더블클릭)
"""
from __future__ import annotations

import html

import altair as alt
import pandas as pd
import streamlit as st

import importlib
import threading
import time

import data
import stocks as stock_list

st.set_page_config(page_title="밸류체인 신고가 보드", page_icon="📈", layout="wide")

REQUIRED = ("is_kr", "market_of", "quote_url", "INDEXES", "fetch_index_histories", "index_summary",
            "fetch_kr_shares", "fetch_fx", "format_krw", "fetch_investor_flows", "fetch_market_overview",
            "market_mood", "fetch_stock_trends", "trend_summary", "resolve_codes", "INST_DETAIL",
            "breakout_hold", "add_rs_ranks", "atr_pct", "BO_MODES",
            "fetch_financials", "fetch_financials_many", "earnings_trend",
            "fetch_monthlies", "newhigh_flags", "ma_signal", "index_rs",
            "buy_screen", "buy_checks", "position_plan", "BUY_RULES", "BUY_DEFAULTS", "EXTRA_KEYS",
            "add_leader_ranks", "sector_leaders", "momentum_engine", "next_leader_sectors", "NEXT_DEFAULTS",
            "SCENARIO_PRESETS", "SCN_WINDOWS", "op_growth", "rotation_confirm", "sector_money_radar", "money_stats", "SCN_INFO", "basket_stats", "classify_scenario", "scenario_paths")
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
                             help="직전 52주 최고가를 종가로 돌파한 뒤 한 번도 그 아래에서 마감하지 않은 종목만")
    nh_filter = st.selectbox("신고가 봉 필터", list(NH_FILTERS), index=0,
                             help="지금 봉(오늘 일봉·이번 주 주봉·이번 달 월봉)의 고가가 52주 또는 역대(상장 이후) 최고가를 넘은 종목만")
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

    bo_mode = "line"   # 돌파선 = 돌파한 날 넘어선 직전 52주 최고가

    st.divider()
    refresh_label = st.radio("자동 새로고침", list(REFRESH), index=1, horizontal=True)
    if st.button("시세 지금 새로고침"):
        load_quotes.clear()
    if st.button("일봉까지 다시 받기", help="52주 최고가가 이상해 보일 때 눌러요."):
        swr_clear("hist_kr", "hist_os", "monthly")
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


def render_market(board: pd.DataFrame | None = None):
    """맨 위 시장 요약: 시장신호(신호등·지수 RS) → 코스피·코스닥 카드 + 오늘의 시장 → 해외 지수·환율·유가."""
    overview = load_overview()
    hist = load_indexes()
    st.markdown(_signal_panel(hist, board), unsafe_allow_html=True)
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
        keep = ["종목", "현재가", "등락률", "신고가까지", "돌파", "RS", "섹터 순위", "분류", "시가총액(원)",
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
        .map(lambda v: f"color: {UP}; font-weight: 700" if isinstance(v, str) else "",
             subset=[c for c in ("52주 신고가(일·주·월)", "역대 신고가(일·주·월)") if c in view.columns])
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
            f'<a class="kcard{" hot" if hot else ""}" target="_blank" href="{r.url}">'
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
        if not hist.empty:
            h = hist.tail(250).set_index("date")
            chart = pd.DataFrame({
                "종가": h["close"],
                "20일선": h["close"].rolling(20).mean(),
                "60일선": h["close"].rolling(60).mean(),
                "52주 최고": row.high52,
            })
            colors = ["#16212B", "#2E6B6F", "#9AA9B3", "#F2B134"]
            if row.bo_status == "유지" and pd.notna(row.bo_level):
                chart["유지 기준(직전 52주 최고)"] = row.bo_level
                colors.append(UP)
            price_chart(chart, colors, height=280)
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
    if data.is_kr(code):
        st.link_button("네이버 증권에서 보기", f"https://m.stock.naver.com/domestic/stock/{code}/total")
    else:
        st.link_button("야후 파이낸스에서 보기", row.url)


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
    df = data.build_table(STOCKS, histories, quotes, shares_store["data"], load_fx(), bo_mode=bo_mode,
                          monthlies=load_monthlies(ALL_CODES))
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
    render_radar(df)
    t_list, t_buy, t_eng, t_next, t_scn = st.tabs(["📋 리스트", "🎯 매수 후보", "🚀 급상승", "🔭 차기 주도", "🧭 시나리오"])
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
