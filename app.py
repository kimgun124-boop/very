"""밸류체인 신고가 보드.

실행: streamlit run app.py   (윈도우는 run.bat 더블클릭)
"""
from __future__ import annotations

import html

import pandas as pd
import streamlit as st

import data
from stocks import GROUP_ORDER, SECTOR_ORDER, STOCKS

st.set_page_config(page_title="밸류체인 신고가 보드", page_icon="📈", layout="wide")

UP, DOWN = "#D6333B", "#1F66C9"        # 한국식: 상승 빨강, 하락 파랑
NEW_HIGH_BG = "#FFF1C9"                # 신고가 행 강조
REFRESH = {"끄기": None, "30초": 30, "1분": 60, "5분": 300}
ALL_CODES = tuple(sorted({s["code"] for s in STOCKS}))

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
.chip-count { color: #F2B134; font-weight: 800; font-size: 1.35rem; font-variant-numeric: tabular-nums; }
.chip-count small { font-size: 0.72rem; font-weight: 600; margin-left: 0.1rem; }
.chip-names { color: #B9C7CF; font-size: 0.8rem; line-height: 1.45; margin-top: 0.2rem; }
.radar-empty { color: #9FB0BA; font-size: 0.9rem; }
</style>
""",
    unsafe_allow_html=True,
)


@st.cache_data(ttl=1800, show_spinner="1년치 일봉을 불러오는 중이에요. 처음 한 번만 몇 초 걸려요.")
def load_histories(codes: tuple[str, ...]):
    return data.fetch_histories(codes)


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
    query = st.text_input("종목 검색", placeholder="이름이나 코드")
    max_drop = st.slider("52주 최고가에서 몇 % 이내만 볼까요", 0, 90, 90, step=5,
                         help="10으로 두면 최고가 대비 -10% 이내 종목만 보여줘요. 90이면 전체.")
    only_aligned = st.toggle("정배열 종목만", help="현재가 > 20일선 > 60일선 > 120일선")

    st.divider()
    st.caption("주도섹터 기준")
    recent_days = st.slider("신고가로 인정할 기간(거래일)", 1, 20, 5)
    min_count = st.slider("분류 안 신고가 종목 수", 2, 5, 2)

    st.divider()
    refresh_label = st.radio("자동 새로고침", list(REFRESH), index=1, horizontal=True)
    if st.button("시세 지금 새로고침"):
        load_quotes.clear()
    if st.button("일봉까지 다시 받기", help="52주 최고가가 이상해 보일 때 눌러요."):
        load_histories.clear()
        load_quotes.clear()


# ─────────────────────────── 화면 조각 ───────────────────────────
def apply_filters(df: pd.DataFrame) -> pd.DataFrame:
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
    return f.sort_values("gap", ascending=False, na_position="last")


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


def render_table(f: pd.DataFrame):
    view = pd.DataFrame({
        "종목": f["name"],
        "코드": f["code"],
        "분류": f["group"],
        "현재가": f["price"],
        "등락률": f["change"],
        "52주 최고": f["high52"],
        "괴리율": f["gap"],
        "신고가까지": f["to_high"],
        "52주 위치": f["pos"],
        "신고가 후": f["days_since_high"],
        "정배열": f["aligned"],
        "설명": f["desc"],
    })

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
            "현재가": "{:,.0f}", "52주 최고": "{:,.0f}",
            "등락률": "{:+.2f}%", "괴리율": "{:.1f}%", "신고가까지": "{:+.1f}%",
            "신고가 후": "{:.0f}일", "52주 위치": "{:.0f}",
        }, na_rep="-")
        .map(color_sign, subset=["등락률"])
        .map(lambda _: "font-weight: 600", subset=["종목"])
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
            "정배열": st.column_config.CheckboxColumn(help="현재가 > 20일선 > 60일선 > 120일선"),
            "설명": st.column_config.TextColumn(width="large"),
        },
    )


def render_detail(f: pd.DataFrame, histories: dict):
    options = f[f["price"].notna()]
    if options.empty:
        return
    st.subheader("종목 자세히 보기")
    labels = {r.code: f"{r.name} ({r.code})" for r in options.itertuples()}
    code = st.selectbox("종목", list(labels), format_func=labels.get, label_visibility="collapsed",
                        key="detail_code")
    row = options[options["code"] == code].iloc[0]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("현재가", f"{row.price:,.0f}원",
              f"{row.change:+.2f}%" if pd.notna(row.change) else None, delta_color="off")
    c2.metric("52주 최고", f"{row.high52:,.0f}원", f"{row.gap:.1f}%", delta_color="off")
    c3.metric("신고가까지", f"{row.to_high:+.1f}%",
              "오늘 신고가" if row.days_since_high == 0 else f"고점 후 {row.days_since_high:.0f}거래일",
              delta_color="off")
    c4.metric("52주 최저", f"{row.low52:,.0f}원")
    st.write(f"**{row['group']}**  \n{row.desc}")

    hist = histories.get(code, (None, data.empty_frame(), None))[1]
    if not hist.empty:
        h = hist.tail(250).set_index("date")
        chart = pd.DataFrame({
            "종가": h["close"],
            "20일선": h["close"].rolling(20).mean(),
            "60일선": h["close"].rolling(60).mean(),
            "52주 최고": row.high52,
        })
        st.line_chart(chart, height=280, color=["#16212B", "#2E6B6F", "#9AA9B3", "#F2B134"])
    st.link_button("네이버 금융에서 보기", f"https://finance.naver.com/item/main.naver?code={code}")


def render_checks(df: pd.DataFrame, quote_error: str | None):
    failed = df[df["price"].isna()]
    mismatch = df[df["name_ok"] == False]  # noqa: E712
    if failed.empty and mismatch.empty and not quote_error:
        return
    with st.expander(f"데이터 점검 필요 {len(failed) + len(mismatch)}건"):
        if quote_error:
            st.write(f"실시간 시세: {quote_error}. 일봉 마지막 값으로 대신 보여주고 있어요.")
        for r in failed.itertuples():
            st.write(f"조회 실패: {r.name} ({r.code}) — {r.error}")
        for r in mismatch.itertuples():
            st.write(f"이름 불일치: 목록에는 {r.name}, 네이버에는 {r.naver_name} ({r.code})")
        st.caption("코드가 틀렸거나 사명이 바뀐 경우예요. verify.bat을 실행하면 올바른 코드를 찾아줘요.")


def render_board():
    histories = load_histories(ALL_CODES)
    quotes, quote_error = load_quotes(ALL_CODES)
    df = data.build_table(STOCKS, histories, quotes)

    interval = REFRESH[refresh_label]
    refresh_text = f"{refresh_label}마다 새로 불러와요." if interval else "자동 새로고침은 꺼져 있어요."
    st.markdown(
        f'<div class="status">{data.now_kst():%Y-%m-%d %H:%M:%S} 기준, {data.market_status(quotes)}이에요. '
        f'{refresh_text} 시세 출처는 네이버 금융이에요.</div>',
        unsafe_allow_html=True,
    )
    if data.MOCK:
        st.info("가짜 데이터로 보여주는 테스트 모드예요. 실제 시세를 보려면 STOCK_MOCK 설정 없이 실행하세요.")

    render_radar(df)
    f = apply_filters(df)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("보고 있는 종목", f"{len(f)}개")
    c2.metric(f"최근 {recent_days}거래일 신고가", f"{int((f['days_since_high'] <= recent_days).sum())}개")
    c3.metric("신고가까지 10% 이내", f"{int((f['to_high'] <= 10).sum())}개")
    c4.metric("정배열", f"{int((f['aligned'] == True).sum())}개")  # noqa: E712

    if f.empty:
        st.info("조건에 맞는 종목이 없어요. 왼쪽에서 산업이나 하락폭 조건을 넓혀 보세요.")
    else:
        render_table(f)
        st.caption("노란 줄은 설정한 기간 안에 52주 신고가를 쓴 종목이에요. 설명은 2023~24년 자료 기준 요약이라 "
                   "최신 사업 현황과 다를 수 있어요.")
        render_detail(f, histories)
    render_checks(df, quote_error)


st.title("밸류체인 신고가 보드")
st.fragment(render_board, run_every=REFRESH[refresh_label])()
