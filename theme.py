"""앱 겉모습(디자인)만 담당해요. 탭·사이드바·기능 구성은 app.py 그대로이고, 여기서는 색·카드·글꼴만 덮어써요.

흰 카드 + 연보라 포인트 + 부드러운 그림자 스타일(STUDY ALPHA 대시보드 느낌).
되돌리고 싶으면 app.py에서 `theme.apply()` 한 줄만 지우면 예전 모습으로 돌아가요.
"""
import streamlit as st

# ── 색 정의(한 곳에서만 바꾸면 전체가 바뀌어요) ──
PRI = "#5B47F5"        # 포인트 보라
PRI_INK = "#4532D9"    # 진한 보라(글자)
PRI_SOFT = "#EFEDFF"   # 연보라 바탕
BG = "#F5F6FB"         # 앱 배경
CARD = "#FFFFFF"
LINE = "#ECEEF5"       # 카드 테두리
LINE2 = "#F3F4F8"      # 표 안쪽 줄
INK = "#111827"        # 본문 글자
SUB = "#6B7280"        # 보조 글자
MUTED = "#9CA3AF"
G2 = "#F59E0B"         # 탭 묶음 2(종목 시그널)
G3 = "#3B82F6"         # 탭 묶음 3(섹터 흐름)

# 차트(plotly)에서 같이 쓰는 값
CHART_GRID = "#F0F1F6"
CHART_SPIKE = "#A59BFA"
CHART_FONT = "Pretendard Variable, Pretendard, Malgun Gothic, sans-serif"

LOGO_SVG = (
    f'<svg width="40" height="30" viewBox="0 0 40 30" fill="none">'
    f'<path d="M2 28 L14 8 L21 19 L26 12 L38 28 Z" fill="{PRI}"/>'
    f'<path d="M14 8 L21 19 L17.5 24 L9 16 Z" fill="#8B7CFF"/>'
    f'<path d="M26 12 L38 28 L30 28 L23 17 Z" fill="#3F2DD1"/></svg>'
)

CSS = f"""
<style>
:root {{
  --pri:{PRI}; --pri-ink:{PRI_INK}; --pri-soft:{PRI_SOFT}; --bg:{BG}; --card:{CARD}; --line:{LINE}; --line2:{LINE2};
  --ink:{INK}; --sub:{SUB}; --muted:{MUTED};
  --shadow: 0 1px 2px rgba(16,24,40,.04), 0 4px 18px rgba(91,71,245,.05);
  --shadow-lg: 0 2px 4px rgba(16,24,40,.04), 0 10px 30px rgba(91,71,245,.10);
  --r: 16px;
}}

/* ── 바탕 ── */
.stApp {{ background: var(--bg) !important; color: var(--ink); }}
[data-testid="stHeader"] {{ background: transparent !important; }}
.stApp .block-container {{ padding-top: 1.6rem; max-width: 1480px; }}
.stApp h1, .stApp h2, .stApp h3 {{ color: var(--ink); letter-spacing: -0.025em; }}

/* ── 상단 브랜드 머리글 ── */
.brand {{ display: flex; align-items: center; justify-content: space-between; gap: 1rem; flex-wrap: wrap;
  background: var(--card); border: 1px solid var(--line); border-radius: var(--r); box-shadow: var(--shadow);
  padding: 0.95rem 1.3rem; margin: 0 0 0.9rem; }}
.brand-l {{ display: flex; align-items: center; gap: 0.75rem; }}
.brand-t {{ font-size: 1.45rem; font-weight: 900; color: var(--ink); letter-spacing: -0.035em; line-height: 1.15; }}
.brand-s {{ font-size: 0.8rem; color: var(--sub); margin-top: 0.1rem; }}
.brand-r {{ display: flex; align-items: center; gap: 0.5rem; }}
.brand-tag {{ font-size: 0.78rem; font-weight: 700; color: var(--pri-ink); background: var(--pri-soft);
  border-radius: 999px; padding: 0.35rem 0.8rem; }}

/* 시간·장 상태 줄 → 알약 모양 */
.stApp .status {{ display: inline-flex; align-items: center; gap: 0.45rem; background: var(--card);
  border: 1px solid var(--line); border-radius: 999px; padding: 0.35rem 0.9rem; margin: 0 0 0.9rem;
  color: var(--sub); font-size: 0.82rem; box-shadow: var(--shadow); }}
.stApp .status::before {{ content: ""; width: 7px; height: 7px; border-radius: 50%; background: #22C55E;
  box-shadow: 0 0 0 3px #DCFCE7; }}

/* ── 사이드바: 흰 패널 ── */
[data-testid="stSidebar"] {{ background: var(--card) !important; border-right: 1px solid var(--line) !important; }}
.stApp .sb-title {{ color: var(--ink); border-bottom: none; padding-bottom: 0.3rem; font-size: 1.1rem; }}
.stApp .sb-title::after {{ content: ""; display: block; width: 28px; height: 3px; border-radius: 3px;
  background: var(--pri); margin-top: 0.45rem; }}
[data-testid="stSidebar"] label p {{ color: #4B5563 !important; }}
[data-testid="stSidebar"] [class*="st-key-sb_box_"] {{ background: #FAFAFD !important; border: 1px solid var(--line) !important;
  border-radius: 14px !important; }}
.stApp .sb-head {{ border-bottom-color: var(--line); }}
.stApp .sb-head span {{ color: var(--ink); }}
.stApp .sb-head em {{ color: var(--pri-ink); background: var(--pri-soft); border-radius: 999px; padding: 0.05rem 0.45rem; }}
.stApp .sb-cat {{ color: var(--muted); }}
.stApp .sb-cat::after {{ background: var(--line); }}
[data-testid="stSidebar"] [data-testid="stButtonGroup"] button {{ border-radius: 10px !important; border-color: var(--line) !important; }}
[data-testid="stSidebar"] [data-testid="stButtonGroup"] button[data-selected="true"] {{
  background: var(--pri) !important; border-color: var(--pri) !important; box-shadow: 0 2px 8px rgba(91,71,245,.25); }}
[data-testid="stSidebar"] .st-key-sb_box_sector button {{ background: var(--card) !important; border: 1px solid var(--line) !important;
  border-radius: 10px !important; }}
[data-testid="stSidebar"] .st-key-sb_box_sector button:hover {{ border-color: var(--pri) !important; }}
[data-testid="stSidebar"] .st-key-sb_box_sector button[kind="primary"] {{ background: var(--pri-soft) !important;
  border-color: var(--pri) !important; }}
[data-testid="stSidebar"] .st-key-sb_box_sector button[kind="primary"] p {{ color: var(--pri-ink) !important; font-weight: 800; }}
[data-testid="stSidebar"] [data-testid="stExpander"] details {{ background: var(--card) !important; }}

/* ── 공통 입력칸·버튼 ── */
.stApp [data-baseweb="select"] > div, .stApp [data-baseweb="input"], .stApp .stTextInput input,
.stApp .stNumberInput input, .stApp textarea {{ border-radius: 10px !important; }}
.stApp [data-baseweb="select"] > div:focus-within, .stApp [data-baseweb="input"]:focus-within {{
  border-color: var(--pri) !important; box-shadow: 0 0 0 3px rgba(91,71,245,.12) !important; }}
.stApp button[kind="primary"], .stApp [data-testid="stBaseButton-primary"] {{
  background: var(--pri) !important; border-color: var(--pri) !important; border-radius: 10px !important;
  box-shadow: 0 2px 8px rgba(91,71,245,.25); }}
.stApp button[kind="primary"]:hover, .stApp [data-testid="stBaseButton-primary"]:hover {{ background: var(--pri-ink) !important; }}
.stApp button[kind="secondary"], .stApp [data-testid="stBaseButton-secondary"] {{ border-radius: 10px !important; border-color: var(--line) !important; }}
.stApp [data-testid="stBaseButton-secondary"]:hover {{ border-color: var(--pri) !important; color: var(--pri-ink) !important; }}
.stApp [data-testid="stButtonGroup"] button[data-selected="true"] {{ background: var(--pri) !important; border-color: var(--pri) !important; }}
.stApp [data-testid="stButtonGroup"] button[data-selected="true"] p {{ color: #FFFFFF !important; }}
.stApp [data-testid="stButtonGroup"] button[data-selected="true"] + button[data-selected="true"] {{ border-left: 1px solid rgba(255,255,255,.4) !important; }}
.stApp [role="checkbox"][aria-checked="true"] > div:first-child,
.stApp [data-testid="stCheckbox"] label[data-baseweb="checkbox"] span[aria-checked="true"] {{ background: var(--pri) !important; border-color: var(--pri) !important; }}

/* ── 주도섹터 레이더: 어두운 상자 → 흰 카드 ── */
.stApp .radar {{ background: var(--card); border: 1px solid var(--line); border-radius: var(--r); box-shadow: var(--shadow);
  padding: 1.05rem 1.25rem 1.15rem; }}
.stApp .radar-title {{ color: var(--ink); font-size: 1.08rem; font-weight: 800; display: flex; align-items: center; gap: 0.45rem; }}
.stApp .radar-title::before {{ content: "🔥"; font-size: 1.05rem; }}
.stApp .radar-rule {{ color: var(--sub); }}
.stApp .chips {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 0.65rem; }}
.stApp .chip {{ max-width: none; min-width: 0; background: #FAFAFD; border: 1px solid var(--line); border-radius: 14px; transition: .15s; }}
.stApp .chip:hover {{ border-color: #CFC8FF; box-shadow: var(--shadow); }}
.stApp .chip-group {{ color: var(--ink); }}
.stApp .chip-count {{ color: var(--pri); }}
.stApp .chip-names {{ color: var(--sub); }}
.stApp .radar-empty {{ color: var(--sub); }}

/* ── 메인 탭: 3묶음 그대로, 카드형 알약으로 ── */
.st-key-main_tabs [role="tablist"] {{ gap: 0.35rem !important; border-bottom: none !important; padding: 0.35rem 0.1rem 0.7rem !important; }}
.st-key-main_tabs [data-baseweb="tab-border"], .st-key-main_tabs [data-testid="stTabs"] > div > div:has(> [role="tablist"]) + div:empty {{ display: none !important; }}
.st-key-main_tabs [role="tab"] {{ --gc: {PRI}; --gt: {PRI_SOFT};
  background: var(--card) !important; border: 1px solid var(--line) !important; border-radius: 12px !important;
  padding: 1.15rem 0.8rem 0.45rem !important; white-space: nowrap; box-shadow: 0 1px 2px rgba(16,24,40,.03); transition: .15s; }}
.st-key-main_tabs [role="tab"]:nth-child(n+5):nth-child(-n+7) {{ --gc: {G2}; --gt: #FEF3C7; }}
.st-key-main_tabs [role="tab"]:nth-child(n+8) {{ --gc: {G3}; --gt: #DBEAFE; }}
.st-key-main_tabs [role="tab"]:hover {{ border-color: var(--gc) !important; background: var(--gt) !important; }}
.st-key-main_tabs [role="tab"]::before {{ color: var(--gc); }}
.st-key-main_tabs [role="tab"] p {{ color: #374151 !important; font-weight: 700; }}
.st-key-main_tabs [role="tab"][aria-selected="true"] {{ background: var(--gc) !important; border-color: var(--gc) !important;
  box-shadow: 0 4px 14px color-mix(in srgb, var(--gc) 35%, transparent); }}
.st-key-main_tabs [role="tab"][aria-selected="true"] p,
.st-key-main_tabs [role="tab"][aria-selected="true"]::before {{ color: #FFFFFF !important; }}
/* 탭 안쪽 작은 탭: 보라 밑줄 */
.st-key-main_tabs [role="tabpanel"] [role="tab"], .st-key-main_tabs [role="tabpanel"] [role="tab"]:nth-child(n) {{
  background: transparent !important; border: none !important; border-bottom: 2px solid transparent !important;
  border-radius: 0 !important; box-shadow: none !important; padding: 0.45rem 0.85rem !important; }}
.st-key-main_tabs [role="tabpanel"] [role="tab"]:hover {{ background: transparent !important; }}
.st-key-main_tabs [role="tabpanel"] [role="tab"][aria-selected="true"] {{ background: transparent !important;
  border-bottom: 2px solid var(--pri) !important; box-shadow: none !important; }}
.st-key-main_tabs [role="tabpanel"] [role="tab"][aria-selected="true"] p {{ color: var(--pri-ink) !important; }}
.st-key-main_tabs [role="tabpanel"] [role="tablist"] {{ border-bottom: 1px solid var(--line) !important; padding: 0 !important; gap: 0.2rem !important; }}
/* 보유 종목 칩 탭 */
.st-key-main_tabs [role="tabpanel"] .st-key-hold_tabs [role="tab"],
.st-key-main_tabs [role="tabpanel"] .st-key-hold_tabs [role="tab"]:nth-child(n) {{
  border: 1px solid var(--line) !important; border-radius: 999px !important; background: var(--card) !important; }}
.st-key-main_tabs [role="tabpanel"] .st-key-hold_tabs [role="tab"][aria-selected="true"] {{
  background: var(--pri) !important; border-color: var(--pri) !important; }}
.st-key-main_tabs [role="tabpanel"] .st-key-hold_tabs [role="tab"][aria-selected="true"] p {{ color: #FFFFFF !important; }}
.st-key-main_tabs [role="tabpanel"] .st-key-hold_tabs [role="tablist"] {{ border-bottom: none !important; }}
/* 그 밖의 일반 탭 */
[data-testid="stTabs"] [role="tablist"] {{ border-bottom-color: var(--line); }}
[data-testid="stTabs"] [data-baseweb="tab-highlight"], [data-testid="stTabs"] .react-aria-SelectionIndicator {{ background: var(--pri) !important; }}

/* ── 숫자 카드(KPI) ── */
.stApp [data-testid="stMetric"] {{ background: var(--card); border: 1px solid var(--line); border-radius: var(--r);
  box-shadow: var(--shadow); padding: 0.85rem 1rem 0.8rem; }}
.stApp [data-testid="stMetricLabel"] p {{ color: var(--sub) !important; font-weight: 600; }}
.stApp [data-testid="stMetricValue"] {{ color: var(--ink); font-weight: 800 !important; letter-spacing: -0.02em; }}
.st-key-desk_kpis [data-testid="stMetric"] {{ position: relative; padding-left: 4.1rem; height: 100%; }}
.st-key-desk_kpis [data-testid="stColumn"] > div, .st-key-desk_kpis [data-testid="stColumn"] [data-testid="stElementContainer"],
.st-key-desk_kpis [data-testid="stColumn"] [data-testid="stVerticalBlock"] {{ height: 100%; }}
.st-key-desk_kpis [data-testid="stMetricDelta"] {{ background: var(--pri-soft) !important; color: var(--pri-ink) !important; }}
.st-key-desk_kpis [data-testid="stMetricDelta"] svg {{ display: none; }}
.st-key-desk_kpis [data-testid="stMetric"]::before {{ position: absolute; left: 0.95rem; top: 50%; transform: translateY(-50%);
  width: 2.5rem; height: 2.5rem; border-radius: 12px; display: flex; align-items: center; justify-content: center;
  font-size: 1.15rem; background: var(--pri-soft); }}
.st-key-desk_kpis [data-testid="stColumn"]:nth-child(1) [data-testid="stMetric"]::before {{ content: "📊"; }}
.st-key-desk_kpis [data-testid="stColumn"]:nth-child(2) [data-testid="stMetric"]::before {{ content: "🏔️"; background: #FEE2E2; }}
.st-key-desk_kpis [data-testid="stColumn"]:nth-child(3) [data-testid="stMetric"]::before {{ content: "🚀"; background: #FEF3C7; }}
.st-key-desk_kpis [data-testid="stColumn"]:nth-child(4) [data-testid="stMetric"]::before {{ content: "🎯"; background: #DBEAFE; }}
.st-key-desk_kpis [data-testid="stColumn"]:nth-child(5) [data-testid="stMetric"]::before {{ content: "📈"; background: #DCFCE7; }}

/* ── 표·접기 상자·글상자 ── */
.stApp [data-testid="stDataFrame"] {{ border: 1px solid var(--line); border-radius: 14px; overflow: hidden;
  box-shadow: var(--shadow); background: var(--card); }}
.stApp [data-testid="stExpander"] details {{ border: 1px solid var(--line) !important; border-radius: 14px !important;
  background: var(--card) !important; box-shadow: var(--shadow); }}
.stApp [data-testid="stExpander"] summary:hover p {{ color: var(--pri-ink) !important; }}
.stApp [data-testid="stAlert"] {{ border-radius: 14px; border: 1px solid var(--line); }}
.stApp [data-testid="stVerticalBlockBorderWrapper"]:has(> div > [data-testid="stVerticalBlock"]) {{ border-radius: var(--r); }}
.stApp [data-testid="stPlotlyChart"], .stApp [data-testid="stVegaLiteChart"], .stApp [data-testid="stArrowVegaLiteChart"] {{
  background: var(--card); border: 1px solid var(--line); border-radius: var(--r); box-shadow: var(--shadow);
  padding: 0.6rem 0.5rem 0.3rem; overflow: hidden; }}
[data-testid="stCaptionContainer"] p {{ color: var(--muted) !important; }}

/* ── 제목줄 ── */
.stApp .sec-h b, .stApp .nh-head b, .stApp .ath-head b {{ color: var(--ink); display: inline-flex; align-items: center; gap: 0.45rem; }}
.stApp .sec-h b::before, .stApp .nh-head b::before, .stApp .ath-head b::before {{ content: ""; width: 4px; height: 1.05em;
  border-radius: 3px; background: var(--pri); }}
.stApp h4 {{ color: var(--ink); }}

/* ── 흰 카드류: 모서리·그림자 통일 ── */
.stApp .ch-head, .stApp .tv-box, .stApp .ho-wrap, .stApp .pr-card, .stApp .verdict, .stApp .mini, .stApp .mv-row,
.stApp .nh-box, .stApp .nh-tbl-wrap, .stApp .ath-tbl, .stApp .dp-block,
.stApp [class*="st-key-"][class*="chart_bar"], .stApp .st-key-chart_search,
.stApp [class*="st-key-hold_card_"], .stApp [class*="st-key-rp_box_"], .stApp .st-key-lock_box {{
  border: 1px solid var(--line) !important; border-radius: var(--r) !important; box-shadow: var(--shadow); background: var(--card); }}
.stApp .tile, .stApp .hd-chk, .stApp .cap-row > div, .stApp .tv-chip {{ border-radius: 12px; border-color: var(--line); }}
.stApp .tv-chip, .stApp .cap-row > div, .stApp .ath-kpi div {{ background: #FAFAFD; }}
.stApp .tv-chip.tot {{ background: linear-gradient(135deg, {PRI} 0%, #7C6CFF 100%); border-color: {PRI};
  box-shadow: 0 6px 18px rgba(91,71,245,.28); }}
.stApp .tv-chip.tot .tv-chip-h, .stApp .tv-chip.tot .tv-chip-f {{ color: #E4E0FF; }}
.stApp .tv-bar {{ background: var(--line2); }}
.stApp .tv-bar b {{ background: var(--pri); }}
.stApp .nh-box-h, .stApp .ath-sec {{ background: #FAFAFD; border-color: var(--line); }}
.stApp .ath-th {{ background: #F7F7FB; color: var(--sub); }}
.stApp .ath-row:hover, .stApp a.nh-card:hover {{ background: #FAF9FF; border-color: #CFC8FF; }}
.stApp .nh-c-keys div, .stApp .k-keys div {{ background: #F6F6FB; }}
.stApp a.kcard {{ border-color: var(--line); border-radius: 14px; box-shadow: var(--shadow); }}
.stApp .hd-act {{ border-left-color: var(--pri); background: #FAF9FF; }}
.stApp .hd-news a:hover {{ border-color: var(--pri); }}
.stApp .dp-plan {{ border-left-color: var(--pri); background: #FAF9FF; }}
.stApp .rp-badge {{ border-color: var(--line); }}

/* ── 표(HTML) 머리줄 ── */
.stApp .tv-tbl th, .stApp .ho-tbl th {{ background: #F7F7FB; color: var(--sub); border-bottom: 1px solid var(--line); }}
.stApp .tv-tbl td, .stApp .ho-tbl td {{ border-bottom-color: var(--line2); }}
.stApp .tv-tbl tbody tr:nth-child(2n) td {{ border-bottom: 1px solid var(--line); }}
.stApp .tv-tbl tbody tr:hover td, .stApp .ho-tbl tbody tr:hover td {{ background: #FAF9FF; }}

/* ── 차트 막대(봉·기간·표시 선택) ── */
.stApp [class*="st-key-"][class*="chart_bar"] button[data-selected="true"],
.stApp .st-key-chart_search [data-testid="stButtonGroup"] button[data-selected="true"] {{
  background: var(--pri) !important; border-color: var(--pri) !important; }}

/* ── 잠금 화면 ── */
.stApp .gate-title, .stApp .lk-title {{ color: var(--ink); }}
.stApp .lk-code {{ border-color: var(--pri); background: #FAF9FF; }}

/* ── 코스피·코스닥 줄, 지수·수급·시그널 카드, 모바일 카드 ── */
.stApp .mk-row, .stApp .idx, .stApp .flow, .stApp .sig {{ border: 1px solid var(--line) !important; border-radius: var(--r) !important;
  box-shadow: var(--shadow); background: var(--card); }}
.stApp .mk {{ border-right-color: var(--line2); }}
.stApp .mk-bar, .stApp .c-bar, .stApp .mk-b60 .gauge {{ background: var(--line2); }}
.stApp .mk-b60 {{ background: #F7F7FB; }}
.stApp .mood {{ background: #FFFAF2; }}
.stApp .tl {{ background: #F3F4F8; border: 1px solid var(--line); }}
.stApp .tl i {{ background: #E3E5EE; }}
.stApp .tl i.G {{ background: #22C55E; }} .stApp .tl i.Y {{ background: #F59E0B; }} .stApp .tl i.R {{ background: #EF4444; }}
.stApp .m-kpis div {{ background: var(--card); border: 1px solid var(--line); box-shadow: var(--shadow); border-radius: 12px; }}
.stApp a.card {{ border-color: var(--line); border-radius: 14px; box-shadow: var(--shadow); }}
.stApp .c-bar i {{ background: var(--pri); }}
.stApp .c-tag.al, .stApp .idx-badge.on {{ background: var(--pri-soft); color: var(--pri-ink); }}
.stApp .idx-badge.neutral {{ background: #F3F4F8; color: var(--sub); }}

/* 스크롤바 */
.stApp ::-webkit-scrollbar {{ width: 8px; height: 8px; }}
.stApp ::-webkit-scrollbar-thumb {{ background: #D9DCE8; border-radius: 8px; }}

@media (max-width: 640px) {{
  .stApp .block-container {{ padding-top: 1rem; }}
  .brand {{ padding: 0.75rem 0.95rem; }}
  .brand-t {{ font-size: 1.15rem; }}
  .brand-s, .brand-r {{ display: none; }}
  .st-key-main_tabs [role="tab"] {{ padding: 1.05rem 0.7rem 0.4rem !important; }}
  .st-key-desk_kpis [data-testid="stMetric"] {{ padding-left: 1rem; }}
  .st-key-desk_kpis [data-testid="stMetric"]::before {{ display: none; }}
}}
</style>
"""


def apply():
    """디자인 CSS를 화면에 넣어요. app.py의 기존 스타일 뒤에 한 번 호출."""
    st.markdown(CSS, unsafe_allow_html=True)


def header(title: str, subtitle: str = "", tag: str = ""):
    """st.title 대신 쓰는 상단 머리글(로고 + 제목 + 설명)."""
    st.markdown(
        f'<div class="brand"><div class="brand-l">{LOGO_SVG}<div>'
        f'<div class="brand-t">{title}</div>'
        + (f'<div class="brand-s">{subtitle}</div>' if subtitle else "")
        + "</div></div>"
        + (f'<div class="brand-r"><span class="brand-tag">{tag}</span></div>' if tag else "")
        + "</div>",
        unsafe_allow_html=True,
    )


def style_fig(fig):
    """plotly 차트를 같은 톤(연한 격자·보라 십자선·흰 말풍선)으로 맞춰요."""
    fig.update_yaxes(gridcolor=CHART_GRID, linecolor=LINE)
    fig.update_xaxes(linecolor=LINE, spikecolor=CHART_SPIKE)
    fig.update_layout(font=dict(family=CHART_FONT, color="#374151"),
                      hoverlabel=dict(bgcolor="#FFFFFF", bordercolor=LINE, font=dict(color=INK)))
    return fig
