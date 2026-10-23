"""📚 관점 학습 — 깡토의 추세추종 × 너쟁이(추노 스터디, 직장인 테크노 펀더멘털리스트) 인터뷰에서 뽑은 관점.

보드가 쓰는 곳
- 🌏 글로벌 피어: 한국 밸류체인 분류 ↔ 해외 같은 업종(피어)·수요처(고객사) 바구니를 나란히 — '우리만 빠지는가?'
- 🪜 증거 쌓기: 실적 서프라이즈 → 국내 섹터 액션 → 글로벌 피어 동반 → 리포트·뉴스 → 대장주·밸류.
  정보가 구체화될수록 비중을 늘리는(피라미딩) 단계를 분류별로 보여줘요.
- 👀 보유 점검: 수익 쿠션 훼손 · 타임컷 · 글로벌 피어 · 밸류 쿠션 항목(holdings.masters_view)
모든 판단은 효석 원칙(리스크관리 > 멘탈 > 종목 > 타점)이 우선이고, 아래 관점은 '재량'을 보태는 참고예요.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _is_kr(c) -> bool:
    import data
    return data.is_kr(c)

# ─────────────────────────── 관점(원문 요지) ───────────────────────────
NJ_SRC = "깡토의 추세추종 · 너쟁이(추노) 인터뷰"
NJ_VIEWS = [
    ("리스크", "리스크 관리가 전부", "언제·어떻게 사는가(타점·기법)보다 리스크 관리가 가장 중요. 2% 룰로 하방을 막고 상방만 본다. "
     "손절을 지키면 '나는 여전히 통계 안에 있다'."),
    ("쿠션", "수익 쿠션을 먼저 만든다", "시장 흐름을 따라 남보다 먼저(선제적으로) 들어가야 쿠션이 생기고, 쿠션이 있으면 위에서 흔들려도 견딘다. "
     "늦게 사면 안전 마진이 없어 휩소에 잦은 하차."),
    ("쿠션", "쿠션이 훼손되면 정리", "하락이 평균 단가를 위협하면(쿠션 훼손) '저평가니까 언젠간' 같은 희망 회로 없이 기계적으로 최대한 정리."),
    ("손절", "아이디어 훼손이면 1~2%라도", "7~8%는 통계적 기준일 뿐 — 아이디어가 훼손됐거나 시장이 너무 나쁘면 1~2%에서도 덜어내거나 타이트하게 손절."),
    ("손절", "타임컷", "아이디어대로라면 며칠 안에 올라야 한다. 며칠 안에 안 오르면 2~3%든 따지지 않고 정리. 제일 좋은 종목은 사자마자 오르는 종목."),
    ("발굴", "실적 서프라이즈 → 원인 → 수요처 → 노출도", "서프라이즈가 나면 '왜?'를 판다(예: 심텍 서프라이즈 → SOCAMM → 엔비디아 칩 수요 → "
     "엔비디아 매출 비중·노출도 큰 기업 → 테스트 소켓·프로브카드). 해외 뉴스(대만 쇼티지)·해외 피어 실적 코멘트로 좁힌다."),
    ("비중", "정보가 구체화될수록 피라미딩", "처음 실적은 '좋아질 수도' 수준 → 일부만. 해외 뉴스·피어 기업 섹터 액션이 확인될 때마다 집중도를 높인다. "
     "한 번에 다 사지 않고 증거가 쌓일 때마다."),
    ("비중", "저평가 쪽에 피라미딩", "성장은 빠른데 비싼 종목 vs 조금 느려도 싼 종목이면 후자 — 안전 마진이 있어야 적극 피라미딩·홀딩이 심리적으로 가능."),
    ("익절", "밸류가 과하면 일부만", "밸류가 너무 높아졌다고 판단되면 일부 익절, 나머지는 추세가 꺾일 때까지 끌고 간다(끝은 아무도 모름)."),
    ("시장", "글로벌 피어로 확인", "한국 소부장이 빠질 때 미국·대만·일본 피어를 본다 — 해외 피어가 안 꺾였으면 국내만의 흔들림일 수 있다. "
     "시장 흐름은 여러 나라가 같이 움직이는 경우가 많다."),
    ("시장", "글로벌 섹터 액션 > 국내 작은 테마", "AI가 주도 산업이면 그 안에, 그중에서도 글로벌로 같이 움직이는 섹터(후공정·OSAT 등)에 있으려 한다."),
    ("트리거", "트리거는 여러 개", "신고가 돌파는 트리거 중 하나 — 포켓피봇·정배열·섹터 액션·펀더멘털을 바꿀 뉴스도 트리거. "
     "단, 재량이 쌓인 '응용 단계'에서. 초보는 통계 룰 안에서."),
    ("선별", "이유 있는 상승만", "테크노 펀더멘털: 차트만 보면 시총 작은 종목·동전주도 걸린다. 펀더멘털 이유가 있는 상승만 추려서 따라간다."),
    ("습관", "매일 '왜 올랐나·왜 빠졌나'", "시장이 무엇에 주목하는지 매일 기록 — 작은 트리거도 수집해 손절을 각오하고 도전. 승률 30~40%라 용기 있게 계속 진입."),
    ("습관", "복기로 재량을 쌓는다", "투자 = 통계 + 재량. 재량은 복기로만 쌓인다 — 진입은 순간이라, 과거 경험이 정리돼 있어야 망설이지 않는다."),
    ("멘탈", "피곤할 때 뇌동매매 금지", "피곤한 상태·스스로 합리화하며 매수 버튼을 누를 때가 큰 손실(LNG 사례). 인지하면 당일 바로 손절."),
]

AI_VIEWS = "\n".join(f"- {t}: {x}" for _, t, x in NJ_VIEWS)

# ─────────────────────────── 한국 분류 ↔ 해외 피어·수요처 ───────────────────────────
# peers = 같은 일을 하는 해외 기업(보드의 해외 분류 이름), demand = 이 분류의 수요처(고객사) 바구니
_SEMI_DEMAND = ["AI 인프라: 본진", "하이퍼스케일러"]
PEER_MAP: dict[str, dict] = {
    "후공정 테스트 장비": {"peers": ["AI 인프라: 테스트·계측"], "demand": _SEMI_DEMAND},
    "후공정 테스트 부품": {"peers": ["AI 인프라: 테스트·계측"], "demand": _SEMI_DEMAND},
    "후공정 패키징·검사 장비": {"peers": ["AI 인프라: 장비·패키징"], "demand": _SEMI_DEMAND},
    "OSAT": {"peers": ["AI 인프라: 장비·패키징"], "demand": _SEMI_DEMAND},
    "패키징 소재": {"peers": ["AI 인프라: 장비·패키징"], "demand": _SEMI_DEMAND},
    "전공정 장비": {"peers": ["반도체 장비(글로벌)"], "demand": ["메모리(글로벌)", "AI 인프라: 본진"]},
    "전공정 계측": {"peers": ["반도체 장비(글로벌)"], "demand": ["메모리(글로벌)", "AI 인프라: 본진"]},
    "전공정 부품": {"peers": ["반도체 장비(글로벌)"], "demand": ["메모리(글로벌)", "AI 인프라: 본진"]},
    "전공정 소재": {"peers": ["반도체 장비(글로벌)"], "demand": ["메모리(글로벌)", "AI 인프라: 본진"]},
    "인프라 장비": {"peers": ["반도체 장비(글로벌)"], "demand": ["메모리(글로벌)", "AI 인프라: 본진"]},
    "메모리·파운드리": {"peers": ["메모리(글로벌)", "파운드리(글로벌)"], "demand": ["하이퍼스케일러", "칩 설계(GPU·CPU·ASIC)"]},
    "기판": {"peers": ["기판(해외)", "MLCC"], "demand": _SEMI_DEMAND},
    "MLCC·수동부품": {"peers": ["MLCC"], "demand": _SEMI_DEMAND},
    "설계(IP·팹리스·디자인하우스)": {"peers": ["칩 설계(GPU·CPU·ASIC)", "EDA"], "demand": ["하이퍼스케일러"]},
    "AIDC 전력기기": {"peers": ["전력·에너지(해외)"], "demand": ["하이퍼스케일러", "네오클라우드"]},
    "AIDC 전선": {"peers": ["전력·에너지(해외)"], "demand": ["하이퍼스케일러", "네오클라우드"]},
    "AIDC 발전·개발": {"peers": ["AIDC 온사이트 발전", "전력·에너지(해외)"], "demand": ["하이퍼스케일러"]},
    "AIDC 온사이트 발전": {"peers": ["AIDC 온사이트 발전"], "demand": ["하이퍼스케일러"]},
    "AIDC ESS": {"peers": ["전력·에너지(해외)"], "demand": ["하이퍼스케일러"]},
    "AIDC 태양광·풍력 제조": {"peers": ["전력·에너지(해외)"], "demand": ["하이퍼스케일러"]},
    "2차전지 소재": {"peers": ["리튬"], "demand": ["자동차·로봇·소재(해외)"]},
    "리튬·원료": {"peers": ["리튬"], "demand": ["자동차·로봇·소재(해외)"]},
    "희토류": {"peers": ["희토류"], "demand": []},
    "발사체·위성": {"peers": ["발사체·위성"], "demand": []},
    "우주 특수합금": {"peers": ["우주 특수합금"], "demand": ["발사체·위성"]},
    "방산·우주": {"peers": ["방산·우주(해외)"], "demand": []},
    "AI·소프트웨어": {"peers": ["AI 소프트웨어"], "demand": ["하이퍼스케일러"]},
    "보안·양자": {"peers": ["사이버보안", "양자컴퓨팅"], "demand": []},
    "통신·네트워크·광": {"peers": ["AI 인프라: 신호·광학"], "demand": ["하이퍼스케일러", "AI 인프라: 본진"]},
    "의료기기·AI·유통": {"peers": ["바이오(해외)"], "demand": []},
}


def _stats_one(df_h: pd.DataFrame | None, live: float | None = None) -> dict | None:
    """일봉 하나 → 1·5·20일 수익률, 20일선 위, 52주 고점 거리. (해외는 마지막 거래일 기준)"""
    if df_h is None or len(df_h) < 30:
        return None
    c = pd.to_numeric(df_h["close"], errors="coerce").to_numpy(dtype=float)
    h = pd.to_numeric(df_h["high"], errors="coerce").to_numpy(dtype=float)
    if live and live > 0 and c[-1] > 0 and abs(live / c[-1] - 1) < 0.31:      # 상하한가(±30%) 안의 실시간 가격만
        try:
            last = pd.Timestamp(df_h["date"].iloc[-1]).date()
        except (KeyError, ValueError, TypeError):
            last = None
        import data
        today = data.now_kst().date()
        if last == today:                              # 오늘 봉이 있으면 덮어쓰고
            c = c.copy()
            c[-1] = live
        elif data.market_status({}).startswith("장중") and last is not None and last < today:
            c, h = np.append(c, live), np.append(h, live)    # 장중인데 오늘 봉이 아직 없으면 붙여요
    c = c[np.isfinite(c)]
    if len(c) < 30 or c[-1] <= 0:
        return None
    hi = np.nanmax(h[-250:]) if len(h) else np.nan
    hi = max(hi, c[-1]) if hi == hi else c[-1]
    return {"r1": (c[-1] / c[-2] - 1) * 100, "r5": (c[-1] / c[-6] - 1) * 100, "r20": (c[-1] / c[-21] - 1) * 100,
            "a20": bool(c[-1] > np.mean(c[-20:])), "near": bool(c[-1] >= hi * 0.95)}


def basket(codes, histories: dict, live: dict | None = None) -> dict | None:
    """바구니(여러 종목)의 중앙값 수익률·20일선 위 비율·신고가 5% 안 비율."""
    rows = []
    for c in codes:
        st = _stats_one((histories.get(c) or (None, None, None))[1], (live or {}).get(c))
        if st:
            rows.append(st)
    if not rows:
        return None
    t = pd.DataFrame(rows)
    return {"n": len(t), "r1": float(t["r1"].median()), "r5": float(t["r5"].median()), "r20": float(t["r20"].median()),
            "a20": float(t["a20"].mean() * 100), "near": float(t["near"].mean() * 100)}


def _strong(b: dict | None) -> bool | None:
    if not b:
        return None
    return bool(b["r20"] > 0 and b["a20"] >= 60)


def _weak(b: dict | None) -> bool | None:
    if not b:
        return None
    return bool(b["r5"] < 0 and b["a20"] < 40)


def peer_verdict(kr: dict | None, pe: dict | None) -> tuple[str, str]:
    """(라벨, 설명) — 너쟁이 관점: 해외 피어가 안 꺾였으면 국내만의 흔들림일 수 있다."""
    if not kr or not pe:
        return "자료 부족", "해외 피어 일봉이 아직 없어요."
    kr_dn = kr["r5"] < -2 or kr["a20"] < 40
    kr_up = kr["r5"] > 0 and kr["a20"] >= 60
    if kr_dn and _strong(pe):
        return "🟡 한국만 약함", "해외 피어는 20일선 위·상승 중 — 국내만의 흔들림(수급·반대매매)일 수 있어요. 펀더멘털 훼손 신호는 아님."
    if kr_dn and _weak(pe):
        return "🔴 글로벌 동반 약세", "해외 피어도 같이 꺾임 — 섹터 전체 조정. 쿠션 없는 포지션은 리스크 관리 먼저."
    if kr_up and _strong(pe):
        return "🟢 글로벌 섹터 액션", "한국·해외가 같이 강함 — 글로벌 흐름 안의 섹터(너쟁이가 선호하는 자리)."
    if kr_up and _weak(pe):
        return "🟠 국내만 강함", "해외 피어는 약한데 국내만 강함 — 국내 테마성일 수 있어 신뢰도 낮춤."
    return "⚪ 혼조", "뚜렷한 동반·괴리 없음."


def peer_table(df: pd.DataFrame, histories: dict) -> list[dict]:
    """한국 분류마다 국내 바구니 vs 해외 피어 vs 수요처."""
    kr = df[df["code"].map(_is_kr)]
    live = {c: p for c, p in zip(kr["code"], pd.to_numeric(kr["price"], errors="coerce")) if p == p}
    os_ = df[~df["code"].isin(kr["code"])]
    by_os = {g: list(s["code"]) for g, s in os_.groupby("group")}
    out = []
    for g, sub in kr.groupby("group"):
        mp = PEER_MAP.get(g)
        if not mp:
            continue
        pcodes = [c for x in mp["peers"] for c in by_os.get(x, [])]
        dcodes = [c for x in mp["demand"] for c in by_os.get(x, [])]
        kb = basket(sub["code"], histories, live)
        pb = basket(pcodes, histories)
        db = basket(dcodes, histories) if dcodes else None
        lab, why = peer_verdict(kb, pb)
        out.append({"group": g, "sector": sub["sector"].iloc[0], "kr": kb, "peer": pb, "demand": db,
                    "peer_names": ", ".join(os_.set_index("code").loc[[c for c in pcodes if c in set(os_["code"])], "name"]
                                            .astype(str).head(8)) if pcodes else "",
                    "peer_groups": " · ".join(mp["peers"]), "demand_groups": " · ".join(mp["demand"]),
                    "verdict": lab, "why": why, "peer_strong": _strong(pb), "demand_strong": _strong(db)})
    return out


# ─────────────────────────── 증거 사다리(피라미딩 단계) ───────────────────────────
def fwd_per(fin: pd.DataFrame | None, price: float | None) -> tuple[float | None, float | None]:
    """(올해 예상 PER, PEG). EPS 예상(E)이 없으면 최근 실적 EPS."""
    if fin is None or fin.empty or "eps" not in fin or not price:
        return None, None
    act = fin[~fin["is_est"]].dropna(subset=["eps"]) if "is_est" in fin else fin.dropna(subset=["eps"])
    est = fin[fin["is_est"]].dropna(subset=["eps"]) if "is_est" in fin else fin.iloc[0:0]
    eps = float(est["eps"].iloc[0]) if len(est) else (float(act["eps"].iloc[-1]) if len(act) else None)
    if not eps or eps <= 0:
        return None, None
    per = price / eps
    base = float(act["eps"].iloc[-1]) if len(act) else None
    g = (eps / base - 1) * 100 if base and base > 0 and len(est) else None
    return per, (per / g if g and g > 0 else None)


LADDER = [("실적", "실적 서프라이즈"), ("섹터", "국내 섹터 액션"), ("글로벌", "글로벌 피어 동반"), ("근거", "리포트·뉴스 뒷받침")]
STAGE_TXT = {0: "관찰", 1: "정찰병(1유닛 미만)", 2: "1유닛", 3: "2유닛", 4: "3유닛(최대)"}


def evidence_ladder(df: pd.DataFrame, peers: list[dict], surprises: list[dict], reports: list[dict],
                    fins: dict, leaders: set, recent_days: int = 30) -> pd.DataFrame:
    """분류별 증거 4단계 + 대장주·밸류. 증거 개수 = 피라미딩 단계(비중은 원칙의 3유닛·종목당 1.5% 위험 안에서)."""
    kr = df[df["code"].map(_is_kr)].copy()
    if kr.empty:
        return pd.DataFrame()
    pmap = {p["group"]: p for p in peers}
    by_code_group = dict(zip(kr["code"], kr["group"]))
    sur_g: dict = {}
    for s in surprises:
        g = by_code_group.get(s.get("code"))
        if g and (s.get("beat") or 0) > 0:
            sur_g.setdefault(g, []).append(s)
    cut = (pd.Timestamp.now(tz="Asia/Seoul") - pd.Timedelta(days=recent_days)).strftime("%Y-%m-%d")
    rep_g: dict = {}
    for r in reports:
        if str(r.get("added_at") or r.get("date") or "")[:10] < cut:
            continue
        for s in r.get("stocks") or []:
            g = by_code_group.get(s.get("code"))
            if g:
                rep_g.setdefault(g, set()).add(r.get("title") or "리포트")
    rows = []
    for g, sub in kr.groupby("group"):
        hot = sub[pd.to_numeric(sub["days_since_high"], errors="coerce") <= 5]
        tvx = pd.to_numeric(sub.get("tv_x"), errors="coerce") if "tv_x" in sub else pd.Series(dtype=float)
        surge = int(((tvx >= 1.5) & (pd.to_numeric(sub["change"], errors="coerce") > 0)).sum()) if len(tvx) else 0
        sect = g in leaders or len(hot) >= 2 or surge >= 2
        p = pmap.get(g)
        glob = bool(p and p.get("peer_strong"))
        sur = sur_g.get(g, [])
        rep = rep_g.get(g, set())
        steps = {"실적": bool(sur), "섹터": sect, "글로벌": glob, "근거": bool(rep)}
        n = sum(steps.values())
        lead = sub.sort_values(["lead_rank"] if "lead_rank" in sub else ["rs"], ascending=True if "lead_rank" in sub else False,
                               na_position="last").iloc[0]
        per, peg = fwd_per(fins.get(lead["code"]), float(lead["price"]) if lead.get("price") == lead.get("price") else None)
        rows.append({"group": g, "sector": sub["sector"].iloc[0], "stage": n, "stage_txt": STAGE_TXT[n], **steps,
                     "sur_txt": ", ".join(f"{s.get('name') or s.get('code')} {s['beat']:+.0f}%" for s in sur[:3]),
                     "sect_txt": (f"신고가 {len(hot)}" + (f" · 거래 1.5배↑ {surge}" if surge else "") + (" · 주도섹터" if g in leaders else "")),
                     "glob_txt": (p["verdict"] if p else "해외 피어 없음"),
                     "rep_txt": ", ".join(list(rep)[:2]),
                     "leader": f"{lead['name']} · {lead['code']}", "lead_rs": lead.get("rs"), "lead_to_high": lead.get("to_high"),
                     "per": per, "peg": peg, "n": len(sub)})
    out = pd.DataFrame(rows)
    return out.sort_values(["stage", "sector"], ascending=[False, True]).reset_index(drop=True)


def value_cushion(per: float | None, peg: float | None) -> tuple[str, str]:
    """너쟁이: 저평가면 피라미딩이 편하고, 고평가면 일부 익절·추가 매수 신중."""
    if per is None:
        return "info", "예상 PER 없음"
    if peg is not None and peg <= 1.0:
        return "good", f"PER {per:.1f} · PEG {peg:.2f} — 성장 대비 싼 편: 피라미딩 심리 여유"
    if per >= 40 or (peg is not None and peg >= 2.0):
        return "warn", f"PER {per:.1f}" + (f" · PEG {peg:.2f}" if peg else "") + " — 비싼 편: 추가 매수 신중, 과열이면 일부 익절"
    return "info", f"PER {per:.1f}" + (f" · PEG {peg:.2f}" if peg else "")
