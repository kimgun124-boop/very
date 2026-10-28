"""🗺️ 큰 그림 — 시장을 AI / 비AI / 그외 영역으로 먼저 나누고, 그 안의 테마별로 본다(2026.10.28 효석 분류).

  AI    : 메모리 · 반도체소부장 · 광통신 · 전력 · 원전 · MLCC · 유리기판 · 재생에너지 · ESS · AI보안 (+ AI 소프트웨어)
  비AI  : 바이오 · 화장품 · 조선 · 방산 · 인바운드 · 엔터 · 철강 · 석유화학 · 은행 · 증권 · 건설 (+ 자동차 · 비AI 기타)
  그외  : 로봇 · 우주항공 · 코인 (+ 전략광물 · 기타)

종목의 세부 분류(stocks.py group)는 그대로 두고, 분류 → 테마 지도(GROUP_THEME)로 테마를 붙여요.
한 종목이 여러 테마에 걸치면(예: 삼성전기 = 반도체소부장 + MLCC + 유리기판) EXTRA로 더하고,
분류와 다른 테마가 맞는 종목은 PRIMARY로 바꿔요(예: 두산에너빌리티 → 원전).
"""
from __future__ import annotations

import pandas as pd

BIG: list[tuple[str, str, list[str]]] = [
    ("AI", "🤖", ["메모리", "반도체소부장", "광통신", "전력", "원전", "MLCC", "유리기판", "재생에너지", "ESS", "AI보안", "AI 소프트웨어"]),
    ("비AI", "🏭", ["바이오", "화장품", "조선", "방산", "인바운드", "엔터", "철강", "석유화학", "은행", "증권", "건설", "자동차", "비AI 기타"]),
    ("그외", "🛰️", ["로봇", "우주항공", "코인", "전략광물", "기타"]),
]
BUCKET_OF = {t: b for b, _, ts in BIG for t in ts}
THEME_ORDER = [t for _, _, ts in BIG for t in ts]

_G = {
    "메모리": ["메모리·파운드리", "메모리(글로벌)", "HDD"],
    "반도체소부장": ["전공정 장비", "전공정 계측", "인프라 장비", "전공정 부품", "전공정 소재", "후공정 테스트 장비",
                 "후공정 테스트 부품", "후공정 패키징·검사 장비", "패키징 소재", "OSAT", "설계(IP·팹리스·디자인하우스)",
                 "기판", "유통·기타", "전자부품", "반도체 장비(글로벌)", "칩 설계(GPU·CPU·ASIC)", "파운드리(글로벌)",
                 "기판(해외)", "아날로그·RF·전력반도체", "EDA", "AI 인프라: 본진", "AI 인프라: 테스트·계측",
                 "AI 인프라: 장비·패키징", "AI 인프라: 전력반도체"],
    "광통신": ["통신·네트워크·광", "IT하드웨어·통신장비", "AI 인프라: 신호·광학"],
    "전력": ["AIDC 전력기기", "AIDC 전선", "전력기기·전선", "AIDC 발전·개발", "AIDC 온사이트 발전", "전력·에너지(해외)"],
    "원전": ["원전·SMR"],
    "MLCC": ["MLCC·수동부품", "MLCC"],
    "재생에너지": ["AIDC 태양광·풍력 제조", "풍력"],
    "ESS": ["AIDC ESS", "ESS·연료전지 부품", "2차전지 소재", "2차전지 장비", "충전·재활용", "리튬·원료", "배터리", "리튬"],
    "AI보안": ["보안·양자", "사이버보안", "양자컴퓨팅"],
    "AI 소프트웨어": ["AI·소프트웨어", "AI 소프트웨어", "하이퍼스케일러", "네오클라우드", "소프트웨어·인터넷", "빅테크·AI·SW·네트워크"],
    "바이오": ["제약·바이오", "의료기기·AI·유통", "바이오", "바이오(해외)"],
    "화장품": ["화장품 ODM", "화장품 용기", "화장품 브랜드·유통", "더마 코스메틱"],
    "조선": ["조선", "조선 기자재·EPC", "해운"],
    "방산": ["방산·우주", "방산·우주(해외)"],
    "인바운드": ["인바운드(카지노·면세·여행·백화점)"],
    "엔터": ["게임·엔터·미디어", "소비·미디어·플랫폼(해외)"],
    "철강": ["철강·소재·화학"],
    "석유화학": ["석유화학", "정유·가스", "소재·화학"],
    "은행": ["은행·보험", "금융(해외)"],
    "증권": ["금융·증권", "금융"],
    "건설": ["건설", "건자재·인프라", "기계"],
    "자동차": ["자동차", "자동차·로봇·소재(해외)"],
    "비AI 기타": ["지주", "소비재·유통", "종합상사", "운송", "통신서비스", "OLED", "디스플레이·전자", "스팩"],
    "로봇": ["로봇"],
    "우주항공": ["발사체·위성", "우주 특수합금", "우주 통신·배터리"],
    "코인": ["코인·디지털자산", "크립토"],
    "전략광물": ["희토류"],
    "기타": ["기타(동전주·정리매매 등)"],
}
GROUP_THEME = {g: t for t, gs in _G.items() for g in gs}

# 분류와 다른 테마가 '주 테마'인 종목 (코드 또는 이름)
PRIMARY = {
    "034020": "원전",          # 두산에너빌리티(분류는 태양광·풍력 제조)
    "052690": "원전",          # 한전기술
    "105560": "은행", "055550": "은행", "032830": "은행",   # KB금융 · 신한지주 · 삼성생명
    "041190": "코인",          # 우리기술투자(두나무 지분)
    "대한유화": "석유화학",
    "012450": "방산", "047810": "방산",                      # 한화에어로스페이스 · 한국항공우주
}
# 함께 걸치는 테마 (주 테마에 더해요)
EXTRA = {
    "011790": ["유리기판"], "161580": ["유리기판"], "112290": ["유리기판"], "089010": ["유리기판"],   # SKC · 필옵틱스 · 와이씨켐 · 켐트로닉스
    "204270": ["유리기판"], "078150": ["유리기판"],                                              # 제이앤티씨 · HB테크놀러지
    "009150": ["MLCC", "유리기판"],                                                             # 삼성전기
    "034020": ["전력"], "015760": ["원전"],                                                     # 두산에너빌리티 · 한국전력
    "041190": ["증권"],                                                                        # 우리기술투자
    "012450": ["우주항공"], "047810": ["우주항공"], "272210": ["우주항공"],                        # 한화에어로 · KAI · 한화시스템
    "003530": ["코인"],                                                                        # 한화투자증권(두나무 지분)
    "005490": ["철강"], "010130": ["철강"],                                                     # POSCO홀딩스 · 고려아연
}


def themes_of(code: str, name: str, group: str) -> list[str]:
    """종목 → 테마 목록(첫 번째가 주 테마)."""
    main = PRIMARY.get(code) or PRIMARY.get(name) or GROUP_THEME.get(group) or "기타"
    out = [main]
    for t in EXTRA.get(code, []) + EXTRA.get(name, []):
        if t not in out:
            out.append(t)
    return out


def tag(df: pd.DataFrame) -> pd.DataFrame:
    """보드 표에 theme(주 테마) · themes(전체) · bucket 열을 붙여요."""
    if df is None or df.empty:
        return df
    ths = [themes_of(c, n, g) for c, n, g in zip(df["code"], df["name"], df["group"])]
    return df.assign(theme=[t[0] for t in ths], themes=ths, bucket=[BUCKET_OF.get(t[0], "그외") for t in ths])


def summary(df: pd.DataFrame, recent: int = 5) -> pd.DataFrame:
    """테마별(여러 테마에 걸친 종목은 각 테마에 모두 셈): 종목 수 · 오늘 등락 중앙값 · 오른 비율 ·
    오늘 신고가 · 최근 신고가 · 20일선 위 비율 · 거래대금 평소 대비 · RS 1위 · 오늘 가장 강한 종목."""
    cols = ["bucket", "theme", "n", "chg", "up", "nh0", "nh5", "a20", "tvx", "leader", "leader_rs", "best", "best_chg", "score"]
    if df is None or df.empty:
        return pd.DataFrame(columns=cols)
    t = tag(df).explode("themes")
    num = lambda s: pd.to_numeric(s, errors="coerce")  # noqa: E731
    rows = []
    for th, g in t.groupby("themes"):
        chg = num(g.get("change"))
        dsh = num(g.get("days_since_high"))
        d20 = num(g.get("dist_ma20")) if "dist_ma20" in g else pd.Series(dtype=float)
        tvx = num(g.get("tv_x")) if "tv_x" in g else pd.Series(dtype=float)
        rs = num(g.get("rs"))
        lead = g.loc[rs.idxmax()] if rs.notna().any() else None
        best = g.loc[chg.idxmax()] if chg.notna().any() else None
        r = {"bucket": BUCKET_OF.get(th, "그외"), "theme": th, "n": len(g),
             "chg": float(chg.median()) if chg.notna().any() else None,
             "up": float((chg > 0).mean() * 100) if chg.notna().any() else None,
             "nh0": int((dsh == 0).sum()), "nh5": int((dsh <= recent).sum()),
             "a20": float((d20 > 0).mean() * 100) if d20.notna().any() else None,
             "tvx": float(tvx.median()) if tvx.notna().any() else None,
             "leader": None if lead is None else str(lead["name"]), "leader_rs": None if lead is None else float(rs.max()),
             "best": None if best is None else str(best["name"]), "best_chg": None if best is None else float(chg.max())}
        # 강도 점수: 오늘 등락 + 신고가 확산 + 20일선 위 비율(추세) — 순위 매기기용
        r["score"] = ((r["chg"] or 0) * 1.0 + min(r["nh5"], 10) * 0.6 + ((r["a20"] or 50) - 50) / 25)
        rows.append(r)
    out = pd.DataFrame(rows, columns=cols)
    out["_o"] = out["theme"].map({t: i for i, t in enumerate(THEME_ORDER)}).fillna(999)
    return out.sort_values("_o").drop(columns="_o").reset_index(drop=True)


def bucket_summary(s: pd.DataFrame) -> dict:
    """영역(AI·비AI·그외)별 요약: 테마 등락 중앙값의 중앙값 · 오른 테마 수 · 최근 신고가 합 · 강한 테마 3."""
    out = {}
    for b, em, _ in BIG:
        g = s[s["bucket"] == b]
        if g.empty:
            out[b] = None
            continue
        ch = pd.to_numeric(g["chg"], errors="coerce")
        top = g.sort_values("score", ascending=False).head(3)
        out[b] = {"em": em, "chg": float(ch.median()) if ch.notna().any() else None, "n_up": int((ch > 0).sum()), "n": len(g),
                  "nh5": int(g["nh5"].sum()), "top": [(r.theme, r.chg) for r in top.itertuples()]}
    return out
