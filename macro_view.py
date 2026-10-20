"""🌍 매크로 시황 탭 화면. 데이터는 macro.py, 이 파일은 그리기만."""
from __future__ import annotations

import html

import pandas as pd
import streamlit as st

import macro

UP, DOWN, FLAT = "#D6333B", "#1F66C9", "#51616C"     # 한국식: 상승 빨강, 하락 파랑
CHIP = {"교차확인": "#1D7A4C", "단일출처": "#51616C", "상충": "#B7791F", "주장": "#C53030", "의견": "#7B5EA7", "없음": "#9AA5AE"}

CSS = """
<style>
.mc-sum{background:#fff;border:1px solid #DCE3E7;border-radius:10px;padding:12px 14px;margin:4px 0 14px;line-height:1.7}
.mc-sum b{font-weight:600}
.mc-wrap{overflow-x:auto;margin-bottom:6px}
.mc-t{width:100%;border-collapse:collapse;font-size:.88rem;font-variant-numeric:tabular-nums}
.mc-t th{color:#51616C;font-weight:500;text-align:right;padding:5px 8px;border-bottom:1px solid #DCE3E7;white-space:nowrap}
.mc-t td{text-align:right;padding:5px 8px;border-bottom:1px solid #EEF2F4;white-space:nowrap}
.mc-t .l{text-align:left}
.mc-t .note{color:#6B7A85;font-size:.78rem;white-space:normal;min-width:12rem;text-align:left}
.mc-chip{display:inline-block;padding:0 8px;border-radius:999px;border:1px solid;font-size:.75rem;line-height:1.6}
.mc-legend{font-size:.8rem;color:#51616C;margin:-4px 0 10px}
.mc-news{border:1px solid #DCE3E7;border-radius:10px;padding:10px 12px;margin-bottom:8px;background:#fff}
.mc-news .h{font-weight:600;margin:4px 0}
.mc-news .m{font-size:.78rem;color:#6B7A85}
.mc-news a{color:inherit}
.mc-bar{display:grid;grid-template-columns:minmax(7rem,max-content) 1fr 4.5rem;gap:4px 8px;align-items:center;font-size:.85rem}
.mc-bar .track{position:relative;height:14px}
.mc-bar .mid{position:absolute;left:50%;top:0;bottom:0;border-left:1px solid #C9D3D9}
.mc-bar .fill{position:absolute;top:2px;bottom:2px;border-radius:3px}
.mc-bar .v{text-align:right;font-variant-numeric:tabular-nums}
</style>
"""


def _c(v) -> str:
    if v is None or pd.isna(v) or v == 0:
        return FLAT
    return UP if v > 0 else DOWN


def _p(v, d=2) -> str:
    return "—" if v is None or pd.isna(v) else f'<span style="color:{_c(v)}">{v:+.{d}f}%</span>'


def _pp(v) -> str:
    return "—" if v is None or pd.isna(v) else f'<span style="color:{_c(v)}">{v:+.3f}%p</span>'


def _chip(status: str) -> str:
    col = CHIP.get(status, FLAT)
    return f'<span class="mc-chip" style="color:{col};border-color:{col}">{html.escape(status)}</span>'


def _ma(s) -> str:
    if not s or s.get("above50") is None:
        return "—"
    return '<span style="color:%s">%s</span>' % ((UP, "위") if s["above50"] else (DOWN, "아래"))


def _table(head: list[str], rows: list[list[str]], left: int = 1) -> str:
    th = "".join(f'<th class="{"l" if i < left else ""}">{h}</th>' for i, h in enumerate(head))
    body = "".join("<tr>" + "".join(f'<td class="{"l" if i < left else ("note" if c.startswith("§") else "")}">{c.lstrip("§")}</td>'
                                    for i, c in enumerate(r)) + "</tr>" for r in rows)
    return f'<div class="mc-wrap"><table class="mc-t"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def _bars(items: list[tuple[str, float | None]]) -> str:
    items = [(n, v) for n, v in items if v is not None and not pd.isna(v)]
    if not items:
        return '<div class="mc-legend">불러오지 못했어요</div>'
    ext = max(abs(v) for _, v in items) or 1
    rows = []
    for n, v in sorted(items, key=lambda x: -x[1]):
        w = abs(v) / ext * 50
        left = 50 if v >= 0 else 50 - w
        rows.append(f'<div>{html.escape(n)}</div><div class="track"><div class="mid"></div>'
                    f'<div class="fill" style="left:{left:.1f}%;width:{max(w, .5):.1f}%;background:{_c(v)}"></div></div>'
                    f'<div class="v">{_p(v)}</div>')
    return '<div class="mc-bar">' + "".join(rows) + "</div>"


def render(snap: dict | None) -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    if not snap:
        st.info("매크로 시황을 불러오는 중이에요. 잠시 뒤 다시 열어 보세요.")
        return
    sm, ver, fred = snap["sm"], snap["ver"], snap["fred"]
    lines = macro.facts(snap)
    st.markdown(f'<div class="mc-sum"><b>숫자로 본 오늘</b> ({snap["at"]:%m/%d %H:%M} 기준)<br>'
                + " · ".join(html.escape(x) for x in lines) + "</div>", unsafe_allow_html=True)
    st.markdown('<div class="mc-legend">검증: ' + " ".join(_chip(k) for k in ("교차확인", "단일출처", "상충")) +
                " — 야후 종가를 stooq·FRED와 같은 날짜끼리 맞춰 봐요(차이 0.15% 안이면 교차확인). "
                "시세는 15분 안팎 지연이에요.</div>", unsafe_allow_html=True)

    st.markdown("#### 세계 주요 지수")
    rows = []
    for i in macro.INDICES:
        s = sm.get(i["yf"])
        v, note = ver.get(i["yf"], ("없음", ""))
        rows.append([i["region"], i["name"], f'{s["last"]:,.2f}' if s else "—", _p(s and s["d1"]), _p(s and s["d5"]),
                     _p(s and s["d20"]), _ma(s), f'{s["date"]:%m/%d}' if s else "—", _chip(v), "§" + html.escape(note)])
    st.markdown(_table(["지역", "지수", "종가", "1일", "5일", "20일", "50일선", "기준일", "검증", "대조"], rows, left=2),
                unsafe_allow_html=True)

    st.markdown("#### 금리 · 환율 · 원자재")
    rows = []
    for m in macro.MACRO:
        s = sm.get(m["yf"])
        v, note = ver.get(m["yf"], ("없음", ""))
        if m["kind"] == "rate":
            val, d1, d20 = (f'{s["last"]:.3f}%' if s else "—"), _pp(s and s.get("a1")), _pp(s and s.get("a20"))
        else:
            val = f'{m.get("unit", "")}{s["last"]:,.2f}' if s else "—"
            d1, d20 = _p(s and s["d1"]), _p(s and s["d20"])
        rows.append([m["group"], m["name"], val, d1, d20, f'{s["date"]:%m/%d}' if s else "—", _chip(v), "§" + html.escape(note)])
    st.markdown(_table(["구분", "항목", "값", "1일", "20일", "기준일", "검증", "대조"], rows, left=2), unsafe_allow_html=True)

    st.markdown("#### 시장 체력 · 위험 지표")
    rows = []
    for x in macro.VOL:
        s = sm.get(x["yf"])
        rows.append(["변동성", x["name"], f'{s["last"]:.2f}' if s else "—", _p(s and s["d1"]), _p(s and s["d20"]),
                     f'{s["date"]:%m/%d}' if s else "—", "§" + x["read"]])
    for f in macro.FRED:
        s = fred.get(f["id"])
        rows.append([f["group"], f["name"], f'{s["last"]:.2f}%' if s else "—", _pp(s and s["a1"]), _pp(s and s.get("a20")),
                     f'{s["date"]:%m/%d}' if s else "—", "§" + f["read"] + " (FRED)"])
    rsp, spy = sm.get("RSP"), sm.get("SPY")
    if rsp and spy and rsp["d20"] is not None and spy["d20"] is not None:
        rows.append(["시장 폭", "동일가중 − 시총가중 (20일)", f'{rsp["d20"] - spy["d20"]:+.2f}%p', _p(rsp["d1"]), _p(rsp["d20"]),
                     f'{rsp["date"]:%m/%d}', "§음수면 소수 대형주가 지수를 끌고 가는 중. 1일·20일은 RSP 등락"])
    st.markdown(_table(["구분", "지표", "값", "1일", "20일", "기준일", "읽는 법"], rows, left=2), unsafe_allow_html=True)

    st.markdown("#### 미국 섹터 ETF")
    period = st.radio("기간", ["1일", "5일", "20일"], horizontal=True, key="mc_period", label_visibility="collapsed")
    k = {"1일": "d1", "5일": "d5", "20일": "d20"}[period]
    sec = {s: n for s, n in macro.SECTORS}
    st.markdown(_bars([(f"{n} {s}", (sm.get(s) or {}).get(k)) for s, n in macro.SECTORS]), unsafe_allow_html=True)

    st.markdown("#### 섹터별 대장주")
    st.caption("섹터 대비 = 종목 등락 − 소속 섹터 ETF 등락(같은 기간). 양수면 섹터보다 강했다는 뜻이에요.")
    rows = []
    for etf, names in macro.LEADERS.items():
        es = sm.get(etf)
        for t, n in names:
            s = sm.get(t)
            rel = s[k] - es[k] if s and es and s[k] is not None and es[k] is not None else None
            v, _ = ver.get(t, ("없음", ""))
            rows.append([f"{sec[etf]} {etf}", t, n, f'{s["last"]:,.2f}' if s else "—", _p(s and s[k]),
                         "—" if rel is None else f'<span style="color:{_c(rel)}">{rel:+.2f}%p</span>', _ma(s), _chip(v)])
    st.markdown(_table(["섹터", "티커", "종목", "종가($)", period, "섹터 대비", "50일선", "검증"], rows, left=3),
                unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### 테마 ETF")
        st.markdown(_bars([(f"{n} {s}", (sm.get(s) or {}).get(k)) for s, n in macro.THEMES]), unsafe_allow_html=True)
    with c2:
        st.markdown("#### 한국 대장주")
        st.markdown(_bars([(n, (sm.get(c) or {}).get(k)) for c, n, _ in macro.KR_LEADERS]), unsafe_allow_html=True)
        st.caption("네이버 일봉(장중 갱신). 단일출처예요.")

    st.markdown("#### 사실 필터 뉴스")
    news = snap.get("news") or []
    pick = st.radio("보기", ["사실만(교차확인·단일출처)", "교차확인만", "주장·의견까지 전부"], horizontal=True, key="mc_news",
                    label_visibility="collapsed")
    allow = {"사실만(교차확인·단일출처)": ("교차확인", "단일출처"), "교차확인만": ("교차확인",),
             "주장·의견까지 전부": ("교차확인", "단일출처", "주장", "의견")}[pick]
    cnt = {s: sum(1 for n in news if n["status"] == s) for s in CHIP}
    st.caption(f"지난 24시간 {len(news)}건 · 교차확인 {cnt['교차확인']} · 단일출처 {cnt['단일출처']} · "
               f"주장 {cnt['주장']} · 의견(전망·추측·질문형 제목) {cnt['의견']}. "
               "교차확인 = 서로 다른 언론사 2곳 이상이 같은 사건을 보도. 제목 기준 자동 분류라 원문을 꼭 확인하세요.")
    shown = [n for n in news if n["status"] in allow][:40]
    if not shown:
        st.caption("조건에 맞는 뉴스가 없어요.")
    for n in shown:
        when = n["when"].astimezone(macro.data.KST).strftime("%m/%d %H:%M") if n["when"] else ""
        others = "".join(f' · <a href="{html.escape(o["url"])}" target="_blank">{html.escape(o["source"])}</a>'
                         for o in n["others"])
        st.markdown(
            f'<div class="mc-news">{_chip(n["status"])} <span class="m">{when}</span>'
            f'<div class="h"><a href="{html.escape(n["url"])}" target="_blank">{html.escape(n["title"])}</a></div>'
            f'<div class="m">{html.escape(", ".join(n["sources"]))}{others}</div></div>',
            unsafe_allow_html=True)
