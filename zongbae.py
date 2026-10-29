"""🌙 종가배팅 — 4인 관점(M 마스터 · K · C · D) 비교와 오늘 종목 자동 점검 (2026-10-17 강의 정리 기준).

숫자는 강의 기준 '대략'이며 절대 조건이 아니에요. 종가배팅은 추세 계좌와 섞지 않는 '종배 전용 계좌'의 단기 매매이고,
다음날 짧게 정리 · 빠른 손절이 공통 원칙이에요. 공부·설계용 정리이며 매매 판단이 아니에요.

자동 점검이 쓰는 값(보드 표): change(등락) · tv_live(오늘 거래대금) · day_open/prev(시가갭) · cap_krw(시총) ·
nh52_d(오늘 52주 신고가) · ath_d(역대 신고가) · to_high(신고가까지) · dcr_today(종가 위치) · aligned(정배열) ·
streak_외국인/기관 · flow_외국인/기관(수급). 프로그램 매매와 분봉(3분봉 이탈 등)은 보드에 없어서 '?'로 둬요.
"""
from __future__ import annotations

import pandas as pd

ZB_VERSION = "2026-10-29-zb"
EOK = 1e8

VIEWS = {
    "M": {"name": "마스터", "color": "#E5484D",
          "line": "테마 1등주가 신고가를 큰 거래대금으로 돌파한 날(M봉), 2:40 이후 M1 라인 위에서 진입",
          "entry": "14:40↑ (M0+ 14:50↑ 고가 돌파)", "tv": "1,000억↑ (검색은 2:40까지 500억↑)", "chg": "+10%↑ 장대양봉",
          "high": "1년↑, 4년·역사적이면 더 좋음", "gap": "7%↑ 제외", "cap": "5조↓", "flow": "프로그램·외국인·기관 확인",
          "tp": "저점·평단 +3%부터 분할 · 갭 3%↑면 정리", "sl": "2:40 이후 저점 이탈 · M0+는 평단 -3% / M1 -1%",
          "hold": "다음날 끝", "size": "1등주 적극, 2등주 축소 · 종배 전용 계좌"},
    "K": {"name": "K", "color": "#F59E0B",
          "line": "역사적 신고가·4년 박스를 처음 뚫은 날(K봉)부터 그 종목만, 오후장에 버티면 진입",
          "entry": "14:40↑ (K10+ 14:50↑, 상방 14:57~15:00)", "tv": "1,000억↑", "chg": "종가 +10%↑ (K10은 +12%↑)",
          "high": "역사적 신고가 / 4년↑ 박스 첫 돌파", "gap": "8%↑ 제외", "cap": "—", "flow": "섹터 동반이면 비중↑",
          "tp": "가장 가까운 저점 +3%부터 분할", "sl": "저점을 3분봉 음봉으로 이탈 · K10+는 K1(오전) -1% / 평단 -3%",
          "hold": "다음날 끝", "size": "종목당 10% · 첫 K봉만 크게(최대 50%)"},
    "C": {"name": "C", "color": "#06B6D4",
          "line": "신고가 근처 정배열 + 기관·외국인 수급 종목을 오후장에 나눠 진입, 고가 마감까지 가져감",
          "entry": "14:30↑ (강한 장 1시, 약한 장 15:10↑)", "tv": "기준봉 1,000억 이하면 안 봄",
          "chg": "(장후 홀기: +20%↑ & 500억↑)", "high": "정배열 + 신고가 구간", "gap": "—", "cap": "2,000억↓ 거의 안 함",
          "flow": "핵심 — 기관·외국인 연속, 프로그램",
          "tp": "갭 뜨면 시가 근처 절반, 저항선에서 분할", "sl": "갭 안 뜨면 본전 정리 · 프로그램 매도 시 절반↑ · 저점 깨면 전량",
          "hold": "다음날 오전(늦어도 11시)", "size": "2~3종목 · 1차 30% → 3시 이후 불타기"},
    "D": {"name": "D", "color": "#A78BFA",
          "line": "\"세 시의 남자\". 다음날 남들이 찾을 종목을 3시·애프터장에서 진입",
          "entry": "15:00↑ · 애프터장 19:00~19:30↑", "tv": "정규 1,500억↑", "chg": "—",
          "high": "첫 양봉·신고가·고가권 횡보", "gap": "—", "cap": "대형주 강하면 1순위",
          "flow": "외국인·기관 양매수, 프로그램 최대일 가산",
          "tp": "다음날 오전에 정리", "sl": "9:05까지 전일 진입가 미회복 시 손절 · 15분 안에 대응 끝",
          "hold": "다음날 오전 · 애프터장분은 프리장 안", "size": "1,000만 기준 2종목 × 300만 · 현금 3~400만 남김"},
}
ROWS = [("고르는 기준", None), ("한 줄", "line"), ("진입 시각", "entry"), ("거래대금", "tv"), ("등락률", "chg"),
        ("신고가", "high"), ("시가갭 제외", "gap"), ("시총", "cap"), ("수급", "flow"),
        ("끝내는 법", None), ("익절", "tp"), ("손절", "sl"), ("보유", "hold"), ("비중", "size")]
COMMON = ["큰 거래대금", "신고가 자리", "장 막판 진입", "고가 근처 마감", "다음날 짧게 정리", "빠른 손절"]
TWO_WAYS = [("재료·차트형 (M · K)", "테마주 장세", "테마 1등주 · 역사적 신고가 첫 돌파 같은 '자리'와 재료가 핵심. 등락 +10%↑ 장대양봉."),
            ("수급형 (C · D)", "지수 상승장", "기관·외국인이 같이 사는 정배열 · 대형주가 핵심. 3시 전후·애프터장까지 나눠 진입.")]


def _n(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def gap_pct(r) -> float | None:
    o, p = _n(r.get("day_open")), _n(r.get("prev"))
    return (o / p - 1) * 100 if o and p else None


def market_type(overview: dict, df: pd.DataFrame) -> tuple[str, str, list[str]]:
    """오늘 장세 → (이름, 설명, 잘 맞는 관점). 지수 상승장 = 코스피·코스닥 둘 다 +0.5%↑이고 오른 종목이 55%↑ →
    수급형(C·D). 지수는 약한데 한 섹터가 평균 +5%↑ → 재료·차트형(M·K) 테마주 장세."""
    rates, up, tot = [], 0.0, 0.0
    for sm in (overview or {}).values():
        if not sm:
            continue
        if sm.get("rate") is not None:
            rates.append(float(sm["rate"]))
        b = sm.get("breadth") or {}
        up += (b.get("상승") or 0) + (b.get("상한") or 0)
        tot += sum((b.get(k) or 0) for k in ("상승", "상한", "보합", "하락", "하한"))
    if not rates:
        return "지수 자료 없음", "지수 자료가 오면 다시 정리해요", []
    upr = up / tot * 100 if tot else None
    kr = df[df["code"].astype(str).str.fullmatch(r"\d{6}")] if df is not None and len(df) else pd.DataFrame()
    hot = []
    if len(kr):
        g = kr.assign(_c=pd.to_numeric(kr["change"], errors="coerce")).groupby("group")["_c"].agg(["mean", "size"])
        hot = list(g[(g["size"] >= 3) & (g["mean"] >= 5)].sort_values("mean", ascending=False).index[:3])
    idx_txt = " · ".join(f"{x:+.2f}%" for x in rates) + (f" · 오른 종목 {upr:.0f}%" if upr is not None else "")
    if all(x >= 0.5 for x in rates) and (upr or 0) >= 55:
        return "지수 상승장", f"지수 {idx_txt} — 수급형(C·D)이 잘 맞는 날" + (f" · 강한 섹터 {', '.join(hot)}" if hot else ""), ["C", "D"]
    if hot:
        return "테마주 장세", f"지수 {idx_txt} · 섹터 평균 +5%↑ {', '.join(hot)} — 재료·차트형(M·K)이 잘 맞는 날", ["M", "K"]
    return "애매한 장", f"지수 {idx_txt} — 뚜렷한 쏠림이 없어요. 종배는 크기를 줄이거나 쉬기", []


def checks(r: dict, flows: dict | None, sector_hot: bool, before_cut: bool) -> dict:
    """한 종목 → {관점: [(상태 ✓/✕/?, 문구)]}. before_cut = 아직 14:40 전(거래대금은 500억↑면 통과로 봄)."""
    chg, tv, cap = _n(r.get("change")), _n(r.get("tv_live")), _n(r.get("cap_krw"))
    gp, dcr, toh = gap_pct(r), _n(r.get("dcr_today")), _n(r.get("to_high"))
    nh = bool(r.get("nh52_d")) or r.get("days_since_high") == 0
    ath = bool(r.get("ath_d"))
    al = r.get("aligned")
    tv_eok = None if tv is None else tv / EOK
    f = flows or {}
    sf, si = f.get("streak_외국인"), f.get("streak_기관")
    ff, fi = f.get("flow_외국인"), f.get("flow_기관")

    def ok(c, yes, no, unknown="?"):
        if c is None:
            return ("?", unknown)
        return ("✓", yes) if c else ("✕", no)

    def tvc(th):
        need = th / 2 if before_cut else th
        return ok(None if tv_eok is None else tv_eok >= need,
                  f"거래대금 {tv_eok:,.0f}억 ({th:,}억↑)" if tv_eok is not None else "",
                  f"거래대금 {tv_eok:,.0f}억 ({th:,}억↑ 아님)" if tv_eok is not None else "", "거래대금 모름")
    out = {}
    out["M"] = [ok(None if chg is None else chg >= 10, f"등락 {chg:+.2f}% (+10%↑)" if chg is not None else "",
                   f"등락 {chg:+.2f}% (+10%↑ 아님)" if chg is not None else ""),
                ("✓", "1년 신고가" + (" · 역사적 신고가(더 좋음)" if ath else "")) if nh else ("✕", "1년 신고가 아님"),
                ok(None if cap is None else cap <= 5e12, f"시총 {cap / 1e12:.1f}조 (5조↓)" if cap else "",
                   f"시총 {cap / 1e12:.1f}조 (5조↓ 아님)" if cap else ""),
                tvc(1000),
                ok(None if gp is None else gp < 7, f"시가갭 {gp:+.1f}%" if gp is not None else "", f"시가갭 {gp:+.1f}% (7%↑ 제외)" if gp is not None else "")]
    out["K"] = [("✓", "역사적 신고가 돌파(K봉 후보)") if ath else ("?", "4년 박스·역사적 신고가 첫 돌파 — 월봉으로 확인"),
                ok(None if chg is None else chg >= 10, f"종가 {chg:+.1f}% (+10%↑" + (" · K10 +12%↑" if (chg or 0) >= 12 else "") + ")" if chg is not None else "",
                   f"종가 {chg:+.1f}% (+10%↑ 아님)" if chg is not None else ""),
                tvc(1000),
                ok(None if gp is None else gp < 8, f"시가갭 {gp:+.1f}% (8%↑ 아님)" if gp is not None else "", f"시가갭 {gp:+.1f}% (8%↑ 제외)" if gp is not None else "")]
    if sector_hot:
        out["K"].append(("✓", "섹터 동반 상승 — 비중↑ 근거"))
    out["C"] = [tvc(1000),
                ok(None if cap is None else cap >= 2e11, f"시총 {cap / 1e12:.1f}조 (작은 종목 아님)" if cap else "",
                   f"시총 {cap / 1e8:,.0f}억 (2,000억↓ 거의 안 함)" if cap else ""),
                ("✓", "신고가 구간") if (nh or (toh is not None and toh <= 5)) else ("✕", f"신고가까지 {toh:.1f}%" if toh is not None else "신고가 구간 아님"),
                ok(None if al is None else bool(al), "정배열", "정배열 아님")]
    if sf is None and si is None:
        out["C"].append(("?", "기관·외국인 연속 — 수급 자료 대기"))
    else:
        both = (sf or 0) > 0 and (si or 0) > 0
        out["C"].append(("✓" if both else "✕", f"외국인 {sf or 0:+d}일 · 기관 {si or 0:+d}일 연속" + ("" if both else " (둘 다 연속 매수 아님)")))
    out["D"] = [tvc(1500),
                ok(None if cap is None else cap >= 1e12, f"대형주(시총 {cap / 1e12:.1f}조) — 강하면 1순위" if cap else "",
                   f"시총 {cap / 1e12:.2f}조 — 대형주 아님" if cap else ""),
                ("?", "외국인·기관 양매수 — 수급 자료 대기") if ff is None and fi is None else
                (("✓", f"외국인 {ff:+,.0f}억 · 기관 {fi:+,.0f}억 양매수") if (ff or 0) > 0 and (fi or 0) > 0 else
                 ("✕", f"외국인 {ff or 0:+,.0f}억 · 기관 {fi or 0:+,.0f}억 (양매수 아님)")),
                ok(None if dcr is None else (dcr >= 70 and (nh or (toh is not None and toh <= 5))),
                   f"고가권(종가 위치 {dcr:.0f}%)·신고가 근처" if dcr is not None else "", f"고가권 아님(종가 위치 {dcr:.0f}%)" if dcr is not None else "",
                   "고가권 횡보 — 확인 필요")]
    return out


def verdict(items: list) -> tuple[str, str]:
    """✕가 없으면 가까움, ✕ 1개면 하나 부족, 그 이상이면 멀어요. (라벨, 색)"""
    n_x = sum(1 for s, _ in items if s == "✕")
    n_q = sum(1 for s, _ in items if s == "?")
    if n_x == 0:
        return ("가까움" if n_q <= 1 else "수급 보면 됨"), "#15803D" if n_q <= 1 else "#B45309"
    if n_x == 1:
        return "하나 부족", "#B45309"
    return "멀어요", "#9CA3AF"


def candidates(df: pd.DataFrame, min_chg: float = 5.0, min_tv_eok: float = 300) -> pd.DataFrame:
    """종배 후보: 국내 · 오늘 +min_chg%↑ 또는 (오름 & 거래대금 1,000억↑), 거래대금 min_tv_eok억↑."""
    if df is None or df.empty:
        return pd.DataFrame()
    kr = df[df["code"].astype(str).str.fullmatch(r"\d{6}")].copy()
    chg = pd.to_numeric(kr["change"], errors="coerce")
    tv = pd.to_numeric(kr["tv_live"], errors="coerce") / EOK
    m = ((chg >= min_chg) | ((chg > 0) & (tv >= 1000))) & (tv >= min_tv_eok)
    return kr[m].assign(_tv=tv[m]).sort_values("_tv", ascending=False).drop(columns="_tv")


def summary_line(r: dict, ck: dict) -> str:
    """'한 줄 정리' — 어느 갈래에 가까운지."""
    v = {k: verdict(x)[0] for k, x in ck.items()}
    mk = [k for k in ("M", "K") if v[k] == "가까움"]
    cd = [k for k in ("C", "D") if v[k] in ("가까움", "수급 보면 됨")]
    chg, tv, cap = _n(r.get("change")), _n(r.get("tv_live")), _n(r.get("cap_krw"))
    facts = (f"시총 {cap / 1e12:.1f}조" if cap and cap >= 1e12 else (f"시총 {cap / EOK:,.0f}억" if cap else "")) + \
        (f" · 거래대금 {tv / EOK:,.0f}억" if tv else "") + (f" · 상승 {chg:+.2f}%" if chg is not None else "")
    if mk and cd:
        head = "두 갈래 모두에 걸쳐 있어요"
    elif mk:
        head = f"재료·차트형({'·'.join(mk)})에 가까워요"
    elif cd:
        head = f"수급형({'·'.join(cd)})에 가까워요"
    else:
        head = "두 갈래 어느 쪽과도 뚜렷하게 가깝지 않아요"
    return f"이 종목은 {head}. {facts}." + (" 수급(외국인·기관)이 붙었는지가 남은 열쇠예요." if any(s == "?" for x in ck.values() for s, _ in x) else "")
