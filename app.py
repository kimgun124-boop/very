"""밸류체인 신고가 보드.

실행: streamlit run app.py   (윈도우는 run.bat 더블클릭)
"""
from __future__ import annotations

import html

import altair as alt
import pandas as pd
import streamlit as st

import data
from stocks import GROUP_ORDER, SECTOR_ORDER, STOCKS, TAGS

st.set_page_config(page_title="밸류체인 신고가 보드", page_icon="📈", layout="wide")

UP, DOWN = "#D6333B", "#1F66C9"        # 한국식: 상승 빨강, 하락 파랑
NEW_HIGH_BG = "#FFF1C9"                # 신고가 행 강조
REFRESH = {"끄기": None, "30초": 30, "1분": 60, "5분": 300}
ALL_CODES = tuple(sorted({s["code"] for s in STOCKS}))
KR_CODES = tuple(c for c in ALL_CODES if data.is_kr(c))
OS_CODES = tuple(c for c in ALL_CODES if not data.is_kr(c))
UNIT = {"KRW": "원", "USD": "달러", "JPY": "엔", "EUR": "유로", "AUD": "호주달러", "HKD": "홍콩달러", "TWD": "대만달러", "GBp": "펜스"}


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
.idx-row { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0.6rem; margin: 0 0 0.5rem; }
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
  .idx-row { gap: 0.35rem; }
  .idx { padding: 0.5rem 0.55rem; }
  .idx-name { font-size: 0.74rem; }
  .idx-name small { display: none; }
  .idx-val { font-size: 1.0rem; }
  .idx-chg { display: block; margin-left: 0; font-size: 0.76rem; }
  .idx-badge { font-size: 0.62rem; padding: 0.05rem 0.3rem; }
}
</style>
""",
    unsafe_allow_html=True,
)


@st.cache_data(ttl=1800, show_spinner="1년치 일봉을 불러오는 중이에요. 처음 한 번만 몇 초 걸려요.")
def load_histories(codes: tuple[str, ...]):
    return data.fetch_histories(codes)


@st.cache_data(ttl=300, show_spinner="해외 종목 일봉을 불러오는 중이에요.")
def load_histories_overseas(codes: tuple[str, ...]):
    return data.fetch_histories(codes, workers=4)


@st.cache_data(ttl=120, show_spinner=False)
def load_indexes():
    return data.fetch_index_histories()


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
        load_histories_overseas.clear()
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
    return f.sort_values("gap", ascending=False, na_position="last")


def render_market():
    """코스피·코스닥·나스닥 요약과 차트. 배지는 지수가 60일선 위인지 아래인지."""
    hist = load_indexes()
    chips = []
    for idx in data.INDEXES:
        df, _err = hist.get(idx["symbol"], (data.empty_frame(), None))
        sm = data.index_summary(df)
        if not sm:
            chips.append(f'<div class="idx"><div class="idx-name">{idx["name"]}</div>'
                         f'<div class="idx-err">불러오지 못했어요</div></div>')
            continue
        color = UP if sm["change"] > 0 else (DOWN if sm["change"] < 0 else "#51616C")
        if sm["above60"] is None:
            badge = ""
        elif sm["above60"]:
            badge = f'<span class="idx-badge on">60일선 위 {sm["dist60"]:+.1f}%</span>'
        else:
            badge = f'<span class="idx-badge off">60일선 아래 {sm["dist60"]:+.1f}%</span>'
        chips.append(
            f'<div class="idx"><div class="idx-name">{idx["name"]}<small>{sm["date"]:%m/%d}</small></div>'
            f'<span class="idx-val">{sm["last"]:,.2f}</span>'
            f'<span class="idx-chg" style="color:{color}">{sm["change"]:+.2f}%</span><br>{badge}</div>'
        )
    st.markdown('<div class="idx-row">' + "".join(chips) + "</div>", unsafe_allow_html=True)

    with st.expander("지수 차트 보기"):
        tabs = st.tabs([i["name"] for i in data.INDEXES])
        for tab, idx in zip(tabs, data.INDEXES):
            with tab:
                df, _err = hist.get(idx["symbol"], (data.empty_frame(), None))
                if df.empty:
                    st.caption("지수 데이터를 불러오지 못했어요. 잠시 뒤 다시 열어 보세요.")
                    continue
                h = df.set_index("date")["close"].astype(float)
                chart = pd.DataFrame({"지수": h, "20일선": h.rolling(20).mean(), "60일선": h.rolling(60).mean()}).tail(180)
                price_chart(chart, ["#16212B", "#2E6B6F", "#F2B134"], height=240)
        st.caption("코스피·코스닥은 네이버 금융, 나스닥은 야후 파이낸스(15분 안팎 지연) 일봉이에요.")


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
        "시장": f["market"],
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

    whole_rows = view.index[f["currency"].isin(["KRW", "JPY"]).values]

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
            "신고가 후": "{:.0f}일", "52주 위치": "{:.0f}",
        }, na_rep="-")
        .format("{:,.0f}", subset=pd.IndexSlice[whole_rows, ["현재가", "52주 최고"]], na_rep="-")
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
            f'<div class="c-desc">{html.escape(r.desc)}</div></a>'
        )
    st.markdown(kpis + '<div class="cards">' + "".join(cards) + "</div>", unsafe_allow_html=True)
    st.caption("카드를 누르면 종목 화면이 열려요. 국내는 네이버 증권, 해외는 야후 파이낸스예요.")


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
    unit = UNIT.get(row.currency, row.currency)
    c1.metric("현재가", f"{fmt_price(row.price, row.currency)}{unit}",
              f"{row.change:+.2f}%" if pd.notna(row.change) else None, delta_color="off")
    c2.metric("52주 최고", f"{fmt_price(row.high52, row.currency)}{unit}", f"{row.gap:.1f}%", delta_color="off")
    c3.metric("신고가까지", f"{row.to_high:+.1f}%",
              "오늘 신고가" if row.days_since_high == 0 else f"고점 후 {row.days_since_high:.0f}거래일",
              delta_color="off")
    c4.metric("52주 최저", f"{fmt_price(row.low52, row.currency)}{unit}")
    st.write(f"**{row['group']}**  \n{row.desc}")
    if row.tags:
        st.caption("리포트 태그: " + ", ".join(row.tags))

    hist = histories.get(code, (None, data.empty_frame(), None))[1]
    if not hist.empty:
        h = hist.tail(250).set_index("date")
        chart = pd.DataFrame({
            "종가": h["close"],
            "20일선": h["close"].rolling(20).mean(),
            "60일선": h["close"].rolling(60).mean(),
            "52주 최고": row.high52,
        })
        price_chart(chart, ["#16212B", "#2E6B6F", "#9AA9B3", "#F2B134"], height=280)
    if data.is_kr(code):
        st.link_button("네이버 금융에서 보기", f"https://finance.naver.com/item/main.naver?code={code}")
    else:
        st.link_button("야후 파이낸스에서 보기", row.url)


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
    histories = {**load_histories(KR_CODES), **load_histories_overseas(OS_CODES)}
    quotes, quote_error = load_quotes(KR_CODES)
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

    render_market()
    render_radar(df)
    f = apply_filters(df)

    n_hot = int((f["days_since_high"] <= recent_days).sum())
    n_near = int((f["to_high"] <= 10).sum())
    n_aligned = int((f["aligned"] == True).sum())  # noqa: E712

    with st.container(key="desk_kpis"):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("보고 있는 종목", f"{len(f)}개")
        c2.metric(f"최근 {recent_days}거래일 신고가", f"{n_hot}개")
        c3.metric("신고가까지 10% 이내", f"{n_near}개")
        c4.metric("정배열", f"{n_aligned}개")

    if f.empty:
        st.info("조건에 맞는 종목이 없어요. 왼쪽에서 산업이나 하락폭 조건을 넓혀 보세요.")
    else:
        with st.container(key="desk_table"):
            render_table(f)
            st.caption("노란 줄은 설정한 기간 안에 52주 신고가를 쓴 종목이에요. 해외 종목은 야후 파이낸스 일봉 기준이라 "
                       "15분 안팎 늦고, 가격은 현지 통화예요. 설명은 각 자료 작성 시점 기준 요약이에요.")
        with st.container(key="mobile_view"):
            render_cards(f, n_hot, n_near, n_aligned)
        render_detail(f, histories)
    render_checks(df, quote_error)


st.title("밸류체인 신고가 보드")
st.fragment(render_board, run_every=REFRESH[refresh_label])()
