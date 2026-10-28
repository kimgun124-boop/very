"""🛰️ 정찰병 신호 · 스승님(깡토) 국면별 플레이북 — 첫 화면 전광판이 쓰는 판정.

스승님 10/28 장중 멘트(정리):
  - 장이 끝나 봐야 알지만 대충 FTD. 오늘 인더스트리 액션은 반도체, 선도주(먼저 신고가 등)
  - 정석적인 FTD보다 의심스러운 FTD가 때로는 더 좋을 수 있음 — 주가는 의심의 벽을 타고 오른다
  - 어제 3일차 강세로 끝나서 오늘 시초가부터 정찰병 투입 시작. 앞으로 양호해질지 다시 내려갈지는 모름
  - 그냥 정해진 규칙과 원칙을 지키면서 대응
  - 오늘 신고가 가는 5종목을 다 편입할 수 있었던 건, 시장이 하락할 때 양호한 종목군을 미리 체크해 놨기 때문
  - 수익 쿠션이 넉넉할수록 여유가 생김

흐름: 지수 국면(market.analyze) → 플레이북 단계 → 조정 구간에서 버틴 종목(견조주) → 그중 오늘 신고가 가는 종목 = 정찰병 후보.
효석 원칙이 항상 우선: 지수 60일선 아래에서는 본매매(유닛)는 쉬고 정찰병 크기만, 1R 8%, 물타기 금지.
"""
from __future__ import annotations

SCOUT_VERSION = "2026-10-28-ibdexp"   # app.py가 이 값으로 새 파일인지 확인해요

import numpy as np
import pandas as pd

MENTOR_SRC = "스승님(깡토) 10/28 장중 멘트 · 깡토 급락장 Q&A"

# 정찰병 크기: 내 원칙에서 1R 포지션(1유닛)은 계좌의 약 6~8%, 그보다 작으면 정찰병 → 절반 정도
SCOUT_WEIGHT = 3.0          # 계좌 대비 % (1유닛 ≈ 6%의 절반)
SCOUT_STOP = 8.0            # 1R 손절 %

# 단계 → 화면 문구. rank가 높을수록 공격적으로 들어가도 되는 장
PLAYBOOK: dict[str, dict] = {
    "corr": {
        "rank": 0, "em": "🔴", "color": "#C0262E", "bg": "#FDECEC", "title": "조정 국면 — 쉬는 장",
        "size": "현금 대부분 · 신규 매수 쉬기",
        "buy": "반등을 예상해 미리 사지 않기. 대신 하락장에서 버티는 종목(견조주)을 미리 체크해 두기 — 다음 FTD 때 바로 편입할 목록",
        "hold": "손절선 그대로 지키기(변동성이 커도 넓히지 않음) · 쿠션 없는 포지션은 정리 · 존버는 추세추종 사전에 없음",
        "mentor": "쉴 때는 쉬는 게 최선. 추세추종은 늦은 매매 — 추세가 나온 뒤 들어가도 늦지 않다",
    },
    "rally12": {
        "rank": 1, "em": "🟠", "color": "#C2410C", "bg": "#FFF1E6", "title": "반등 시도 초반 — 3일차 강세 기다림",
        "size": "현금 대부분 · 아직 정찰병 전",
        "buy": "랠리 1~2일차는 지켜보기. 견조주 목록에서 신고가 근처까지 올라온 종목을 체크하며 3일차 강세를 기다림",
        "hold": "바닥을 다시 깨면 반등 시도 실패 → 새 바닥부터 다시. 보유분은 손절선 그대로",
        "mentor": "바닥은 아무도 모른다. 반등이 나오고 나서 대응해도 전혀 늦지 않다",
    },
    "day3": {
        "rank": 2, "em": "🟣", "color": "#5B47F5", "bg": "#EFEDFF", "title": "3일차 강세 — 정찰병 투입 허락",
        "size": f"정찰병만 · 종목당 계좌 약 {SCOUT_WEIGHT:.0f}% (1유닛보다 작게)",
        "buy": "3일차가 강세로 끝났으면 다음 날 시초가부터 정찰병 투입 — 견조주 중 신고가 가는 선도주부터. 같은 섹터에 우르르 넣지 않기",
        "hold": "정찰병은 죽는 게 당연 — 1R(-8%) 손절 그대로. 살아남아 쿠션이 생긴 종목만 다음 단계 후보",
        "mentor": "어제 3일차 강세로 끝나서 오늘 시초가부터 정찰병 투입 시작. 앞으로 어떻게 될지는 모른다 — 규칙대로 대응",
    },
    "susp_ftd": {
        "rank": 3, "em": "🟣", "color": "#4532D9", "bg": "#EFEDFF", "title": "의심스러운 FTD — 의심의 벽",
        "size": f"정찰병 · 종목당 약 {SCOUT_WEIGHT:.0f}% → 쿠션 생긴 종목만 1유닛",
        "buy": "정석 FTD(4일차 이후 +1.25%·거래량 증가)는 아니어도 반등 4일차 이후 강한 상승. 오늘 인더스트리 액션 섹터의 선도주(먼저 신고가) 위주로 정찰병",
        "hold": "랠리 저점을 깨면 모든 판단을 되돌리기. 쿠션이 넉넉한 종목일수록 흔들려도 여유",
        "mentor": "정석적인 FTD보다 의심스러운 FTD가 때로는 더 좋을 수 있다 — 주가는 의심의 벽을 타고 오른다",
    },
    "ftd": {
        "rank": 4, "em": "🟢", "color": "#15803D", "bg": "#E9F7EE", "title": "FTD 확인 — 점진적으로 비중 확대",
        "size": "정찰병 → 성공한 종목에 1유닛씩 (지수 60일선 위로 올라와야 본매매)",
        "buy": "FTD는 '공격'이 아니라 '다시 볼 허락'. 인더스트리 액션 섹터·선도주에 정찰병, 살아서 오르는 종목에 피라미딩",
        "hold": "랠리 저점 이탈 = FTD 실패 → 되돌림. 3R에서 절반 익절, 나머지는 추세가 꺾일 때까지",
        "mentor": "늦게 사더라도 추세가 강력하면 피라미딩으로 강한 종목에 비중을 싣는 게 핵심 — 슈퍼루키 1~2종목이 계좌를 끈다",
    },
    "press": {
        "rank": 5, "em": "🟡", "color": "#B45309", "bg": "#FFF8E6", "title": "상승 둔화 — 정찰병 크기로만",
        "size": "신규는 정찰병 크기 · 약한 종목부터 정리",
        "buy": "분산일이 평소보다 많음. 신규 매수는 작게, 손절선은 엄격하게",
        "hold": "약한 종목부터 정리, 쿠션 있는 대장주는 추세가 꺾일 때까지 홀딩",
        "mentor": "시장이 나쁘면 비중을 점점 줄이고, 좋아지면 점점 늘린다",
    },
    "up": {
        "rank": 6, "em": "🟢", "color": "#15803D", "bg": "#E9F7EE", "title": "상승 추세 — 원칙대로 매매",
        "size": "원칙 유닛(6% → 12% → 18%) · 종목당 위험 1.5%",
        "buy": "주도섹터 대장주의 신고가 돌파 · 🔔 매수하세요 신호 · 첫 신고가가 가장 좋음",
        "hold": "3R(+24%)에서 절반, 나머지는 5일선이 50일선에 닿을 때까지. 쿠션 있는 종목은 흔들려도 홀딩",
        "mentor": "수익 쿠션이 넉넉할수록 여유가 생긴다",
    },
}
PHASE_ORDER = ["corr", "rally12", "day3", "susp_ftd", "ftd", "press", "up"]


def index_phase(m: dict | None, susp_pct: float = 0.8, ftd_fresh: int = 15) -> dict | None:
    """지수 하나의 market.analyze 결과 → 플레이북 단계 + 이유."""
    if not m:
        return None
    s, day, chg = m["state"], int(m.get("day") or 0), m.get("chg")
    sr = m.get("series")
    why = ""
    if s == "corr":
        ph = "corr"
        why = f"고점 대비 {m['draw']:+.1f}% · 아직 랠리 데이 전"
        ev = (m.get("events") or [{}])[0]
        if ev.get("kind") == "fail":
            why = "FTD 실패(랠리 저점 이탈) — " + why
    elif s == "rally":
        day3_up = False
        if sr is not None and len(sr) and "day" in sr:
            cur = sr.tail(max(day, 1))
            d3 = cur[cur["day"] == 3]
            day3_up = bool(len(d3) and (d3["chg"].iloc[-1] or 0) > 0)
        if day >= 4 and chg is not None and chg >= susp_pct:
            ph, why = "susp_ftd", f"반등 {day}일차 {chg:+.2f}%" + ("" if m.get("vol_up") else " (거래량은 어제보다 적음)") + " — 정석 FTD 기준엔 못 미침"
        elif day >= 4 and day3_up:
            ph, why = "day3", f"반등 {day}일차 · 3일차가 강세로 끝남"
        elif day == 3 and chg is not None and chg > 0:
            ph, why = "day3", f"오늘이 3일차 {chg:+.2f}% — 강세로 끝나면 다음 날 시초가부터 정찰병(장중이면 종가 확인)"
        else:
            ph, why = "rally12", f"반등 {day}일차" + (f" {chg:+.2f}%" if chg is not None else "")
        if day in (2, 3) and chg is not None and chg >= 1.25 and m.get("vol_up"):
            why += " · 큰 상승+거래량 증가지만 IBD 기준(Day 4 이후)으론 아직 FTD 아님 — 일부 서비스는 '이른 FTD'로 셈"
    elif s == "press":
        ph, why = "press", f"분산일 {m['dd']}개(평소 {m['q50']:.0f}개)"
    else:
        lf = m.get("last_ftd")
        fresh = False
        n_after = None
        if lf and lf.get("result") == "⏳ 진행 중" and sr is not None and len(sr):
            n_after = int((pd.to_datetime(sr["date"]) > pd.Timestamp(lf["date"])).sum())
            fresh = n_after <= ftd_fresh
            if fresh:
                why = f"FTD {pd.Timestamp(lf['date']):%m/%d}(Day {lf['day']}, {lf['chg']:+.2f}%) 뒤 {n_after}거래일 · 랠리 저점 {lf['low']:,.2f}"
        ph = "ftd" if fresh else "up"
        if not fresh:
            why = f"52주 고점 대비 {m['draw']:+.1f}% · 분산일 {m['dd']}개"
    na = locals().get("n_after")
    return {"phase": ph, "why": why, "n_after": na, **{k: PLAYBOOK[ph][k] for k in ("em", "title", "rank")}}


def combine(phases: dict) -> tuple[str | None, str]:
    """두 지수 중 더 나아간 단계로 정찰병 허락을 판단(스승님도 시장 전체를 '대충 FTD'로 봄). (단계, 설명)"""
    ok = {k: v for k, v in phases.items() if v}
    if not ok:
        return None, ""
    best = max(ok.items(), key=lambda kv: kv[1]["rank"])
    others = [f"{k} {v['em']} {PLAYBOOK[v['phase']]['title'].split(' — ')[0]}" for k, v in ok.items()]
    return best[1]["phase"], " · ".join(others)


# ─────────────────────────── 견조주: 조정 구간에서 버틴 종목 ───────────────────────────
def resilient(df: pd.DataFrame, histories: dict, low_date, peak_date, top_pct: float = 20.0,
              max_age_days: int = 120) -> pd.DataFrame | None:
    """조정 바닥(low_date) 시점에 52주 고점에서 가장 가까웠던 상위 top_pct% = 견조주.
    gap_low = 바닥날 종가 / 그때까지 52주 최고가 − 1, dd_win = 지수 고점일 → 바닥일 종가 변화."""
    if low_date is None:
        return None
    low = pd.Timestamp(low_date)
    if (pd.Timestamp.now() - low).days > max_age_days:
        return None
    peak = pd.Timestamp(peak_date) if peak_date is not None else low - pd.Timedelta(days=60)
    rows = []
    import data
    for code in df["code"]:
        if not data.is_kr(code):
            continue
        h = (histories.get(code) or (None, None, None))[1]
        if h is None or len(h) < 60:
            continue
        dt = pd.to_datetime(h["date"])
        c = pd.to_numeric(h["close"], errors="coerce").to_numpy(dtype=float)
        hi = pd.to_numeric(h["high"], errors="coerce").to_numpy(dtype=float)
        i = int((dt <= low).sum()) - 1
        if i < 20:
            continue
        hi52 = np.nanmax(hi[max(0, i - 249):i + 1])
        if not hi52 or not np.isfinite(c[i]):
            continue
        j = int((dt <= peak).sum()) - 1
        rows.append({"code": code, "gap_low": (c[i] / hi52 - 1) * 100,
                     "dd_win": (c[i] / c[j] - 1) * 100 if j >= 0 and c[j] > 0 else None})
    if not rows:
        return None
    t = pd.DataFrame(rows)
    t["res_pct"] = (t["gap_low"].rank(pct=True) * 100).round()
    t["resilient"] = t["res_pct"] >= 100 - top_pct
    return t


# ─────────────────────────── 정찰병 후보 ───────────────────────────
def candidates(df: pd.DataFrame, scan_today: pd.DataFrame | None, res: pd.DataFrame | None, ia_groups: set,
               rs_min: float = 70, min_cap_eok: float = 1000, near_pct: float = 3.0, min_vx: float = 1.0) -> pd.DataFrame:
    """견조주(없으면 RS 상위) 중 오늘 52주 신고가를 가는 종목 = '정찰병 투입', 신고가 near_pct% 안 = '대기'."""
    cols = ["code", "name", "group", "tier", "score", "price", "chg", "vx", "to_res", "brk_close", "brk_touch", "rs",
            "gap_low", "res_pct", "ia", "first", "lead", "stop", "target", "why"]
    if scan_today is None or scan_today.empty:
        return pd.DataFrame(columns=cols)
    keep = [k for k in ("name", "rs", "cap_krw", "atr_pct", "sector_first", "lead_rank", "lead_ok") if k in df]
    t = scan_today.merge(df[["code", *keep]].drop_duplicates("code"), on="code", how="left")
    if res is not None:
        t = t.merge(res[["code", "gap_low", "res_pct", "resilient"]], on="code", how="left")
        t = t[t["resilient"] == True]  # noqa: E712
    else:
        t = t.assign(gap_low=np.nan, res_pct=np.nan)
        t = t[pd.to_numeric(t.get("rs"), errors="coerce") >= 80]        # 조정 구간이 없으면 RS 80↑를 강한 종목으로
    num = lambda s: pd.to_numeric(s, errors="coerce")  # noqa: E731
    t = t[(num(t["rs"]).fillna(rs_min) >= rs_min) & (num(t.get("cap_krw")).fillna(0) / 1e8 >= min_cap_eok)]
    if t.empty:
        return pd.DataFrame(columns=cols)
    out = []
    for r in t.itertuples():
        nh = bool(r.brk_close or r.brk_touch)
        near = r.to_res is not None and r.to_res == r.to_res and r.to_res <= near_pct
        if not (nh or near) or (r.chg is not None and r.chg == r.chg and r.chg <= 0):
            continue
        if nh and (r.vx or 0) < min_vx:            # 스승님 '부족함 없는 돌파일': 신고가 + 거래량(평소 이상) + 양봉
            nh = False
            if not near:
                continue
        ia = r.group in ia_groups
        first = bool(getattr(r, "sector_first", False) is True)
        lr = getattr(r, "lead_rank", None)
        lead = "👑 대장주" if getattr(r, "lead_ok", None) is True and lr == lr and lr is not None and int(lr) == 1 else None
        score = (3 if nh else 0) + (1 if r.brk_close else 0) + (2 if ia else 0) + (1 if first else 0) \
            + (1 if (r.vx or 0) >= 1.5 else 0) + (1 if lead else 0) + ((r.res_pct or 0) / 100 if r.res_pct == r.res_pct else 0)
        atr = getattr(r, "atr_pct", None)
        sp = float(atr) if atr is not None and atr == atr and atr >= SCOUT_STOP else SCOUT_STOP
        why = [x for x in ("52주 신고가 종가 돌파" if r.brk_close else ("장중 신고가" if r.brk_touch else f"신고가까지 {r.to_res:.1f}%"),
                           "오늘 인더스트리 액션 섹터" if ia else None, "섹터에서 먼저 신고가" if first else None,
                           f"거래량 {r.vx:.1f}배" if (r.vx or 0) >= 1.5 else None, lead,
                           f"조정 바닥 때 고점 대비 {r.gap_low:+.0f}%(버틴 순위 상위 {max(1, 100 - r.res_pct):.0f}%)" if r.gap_low == r.gap_low else None) if x]
        out.append({"code": r.code, "name": r.name, "group": r.group, "tier": "투입" if nh else "대기", "score": score,
                    "price": float(r.close), "chg": r.chg, "vx": r.vx, "to_res": r.to_res, "brk_close": bool(r.brk_close),
                    "brk_touch": bool(r.brk_touch), "rs": r.rs, "gap_low": r.gap_low, "res_pct": r.res_pct, "ia": ia,
                    "first": first, "lead": lead, "stop": float(r.close) * (1 - sp / 100),
                    "target": float(r.close) * (1 + 3 * sp / 100), "why": " · ".join(why)})
    o = pd.DataFrame(out, columns=cols)
    if o.empty:
        return o
    o["_t"] = (o["tier"] == "투입").astype(int)
    return o.sort_values(["_t", "score", "vx"], ascending=False).drop(columns="_t").reset_index(drop=True)


def pick(cands: pd.DataFrame, n: int = 6, per_group: int = 2, tier: str = "투입") -> pd.DataFrame:
    """정찰병 카드용: 점수 순으로 n개, 같은 섹터는 per_group개까지(같은 섹터 우르르 금지)."""
    if cands is None or cands.empty:
        return cands
    t = cands[cands["tier"] == tier]
    t = t.groupby("group", sort=False, group_keys=False).head(per_group)
    return t.head(n)


def scout_allowed(phase: str | None) -> bool:
    return phase in ("day3", "susp_ftd", "ftd", "press", "up")


def commentary(phase: str | None, ph_txt: str, market_ok: bool | None, d20: float | None, cands: pd.DataFrame,
               ia_secs: list[str], n_res: int | None, leaders: int) -> list[str]:
    """전광판 아래 해설 — 지금 장을 스승님 플레이북으로 읽고, 내 원칙과 겹쳐서 무엇을 할지."""
    if phase is None:
        return ["지수 일봉을 받는 중이에요."]
    pb = PLAYBOOK[phase]
    out = [f"<b>지금 장</b>: {pb['em']} {pb['title']} ({ph_txt})"]
    n_go = int((cands["tier"] == "투입").sum()) if len(cands) else 0
    n_wait = int((cands["tier"] == "대기").sum()) if len(cands) else 0
    if scout_allowed(phase) and phase in ("day3", "susp_ftd", "ftd"):
        if n_go:
            top = pick(cands)
            secs = cands[cands["tier"] == "투입"]["group"].value_counts()
            crowd = secs[secs >= 3]
            out.append(f"<b>매수</b>: 견조주 중 오늘 거래량 실어 신고가 가는 종목 {n_go}개 → 점수 높은 {len(top)}개만 정찰병 카드로. 1순위 "
                       + ", ".join(top["name"].astype(str).head(3)) + f" · 종목당 계좌 약 {SCOUT_WEIGHT:.0f}%, 손절 1R(-8%)"
                       + (f" · {crowd.index[0]}에 {int(crowd.iloc[0])}개 몰림 — 같은 섹터는 먼저 신고가 간 2개까지만" if len(crowd) else ""))
        else:
            out.append("<b>매수</b>: 정찰병은 허락됐지만 견조주 중 오늘 신고가 가는 종목이 아직 없음 — 억지로 사지 않기"
                       + (f" · 신고가 3% 안 대기 {n_wait}개" if n_wait else ""))
    elif phase in ("corr", "rally12"):
        out.append("<b>매수</b>: 아직 정찰병 전 — 반등을 예상해 미리 사지 않음. "
                   + (f"지금 견조주 {n_res}개를 목록으로 들고 있다가 3일차 강세·FTD가 나오면 그중 신고가 가는 종목부터" if n_res
                      else "하락장에서 버티는 종목(📉 하락장 점검·견조주)을 목록으로 만들어 두기"))
    else:
        out.append("<b>매수</b>: " + pb["buy"])
    if market_ok is False:
        out.append("<b>내 원칙</b>: 코스피·코스닥이 60일선 아래 → 본매매(유닛)는 쉬고 정찰병 크기만. 60일선을 회복하면 성공한 정찰병부터 1유닛")
    elif market_ok is True and phase in ("day3", "susp_ftd", "ftd"):
        out.append("<b>내 원칙</b>: 지수 60일선 위 — 정찰병이 3R 쪽으로 살아가면 1유닛으로 키우기(종목당 위험 1.5%)")
    if ia_secs:
        out.append(f"<b>인더스트리 액션</b>: 오늘 {', '.join(ia_secs[:3])} — 섹터가 같이 거래량을 동반해 오르는 곳의 선도주(먼저 신고가)가 1순위")
    elif leaders:
        out.append(f"<b>주도섹터</b>: 신고가 2종목↑ 섹터 {leaders}개 — 주도섹터가 있어야 돌파매매")
    else:
        out.append("<b>주도섹터</b>: 아직 없음 — 내 원칙상 주도섹터가 없으면 신고가 돌파매매는 쉼")
    if d20 is not None and d20 >= 80:
        out.append(f"<b>20 DOWN</b> {d20:.0f}% — 과매도권. 저가매수는 3~5유닛 분할·반등 시 축소(가치 계좌 영역), 추세 계좌는 신고가 종목만")
    out.append("<b>보유·매도</b>: " + pb["hold"])
    out.append(f"<b>스승님</b>: “{pb['mentor']}” — 장이 좋아질지 다시 내려갈지는 모름, 정해진 규칙대로 대응")
    return out


# ─────────────────────────── 🚀 선매입: 매물대 돌파 + 거래량 + 인더스트리 액션 ───────────────────────────
# 52주 신고가만 기다리지 않고, 위에 쌓인 매물(가격대별 거래량)의 큰 덩어리를 거래량을 실어 뚫는 날
# 섹터가 같이 움직이면(인더스트리 액션) 신고가 전에 먼저 산다. 손절은 뚫은 매물대 아래(최대 1R 8%).
VP_DAYS = 250        # 매물대를 보는 기간(약 1년)
VP_BINS = 40         # 가격 칸 수
VP_NODE_PCT = 75     # 이 백분위 이상 거래량이 쌓인 칸 = '매물대'


def volume_profile(d, h, l, c, v, upto: int) -> dict | None:
    """upto(오늘) 이전 VP_DAYS 거래일로 가격대별 거래량을 만들고, 오늘 종가 기준
    뚫은 매물대 · 위에 남은 매물 비율 · 다음 저항 · 아래 지지(매물대 위쪽)를 돌려줘요."""
    a = max(0, upto - VP_DAYS)
    hh, ll, cc, vv = h[a:upto], l[a:upto], c[a:upto], v[a:upto]
    ok = np.isfinite(hh) & np.isfinite(ll) & np.isfinite(cc) & np.isfinite(vv) & (vv > 0)
    if ok.sum() < 60:
        return None
    hh, ll, cc, vv = hh[ok], ll[ok], cc[ok], vv[ok]
    lo, hi = float(np.min(ll)), float(np.max(hh))
    if hi <= lo:
        return None
    edges = np.linspace(lo, hi, VP_BINS + 1)
    tp = (hh + ll + cc) / 3
    vol, _ = np.histogram(tp, bins=edges, weights=vv)
    total = vol.sum()
    if total <= 0:
        return None
    cut = max(np.percentile(vol, VP_NODE_PCT), vol.max() * 0.25)      # 가장 무거운 칸의 25% 이상만 '매물대'
    heavy = [i for i in range(VP_BINS) if vol[i] >= cut and vol[i] > 0]
    # 붙어 있는 무거운 칸을 하나의 매물대(구간)로 묶어요: (아래, 위, 거래량 비중)
    zones, run = [], []
    for i in heavy + [None]:
        if run and (i is None or i != run[-1] + 1):
            zones.append((float(edges[run[0]]), float(edges[run[-1] + 1]), float(vol[run[0]:run[-1] + 1].sum() / total * 100)))
            run = []
        if i is not None:
            run.append(i)
    C, P = float(c[upto]), float(c[upto - 1])
    centers = (edges[:-1] + edges[1:]) / 2
    # 오늘 뚫은 매물대: 어제 종가 ≤ 구간 위쪽 < 오늘 종가 — 그중 거래량 비중이 가장 큰 구간
    broke = [z for z in zones if P <= z[1] < C]
    bz = max(broke, key=lambda z: z[2]) if broke else None
    above = vol[centers > C].sum() / total * 100
    nxt = [z for z in zones if z[0] > C]
    nz = min(nxt, key=lambda z: z[0]) if nxt else None
    below = [z for z in zones if z[1] <= C]
    sz = max(below, key=lambda z: z[1]) if below else None
    return {"broke": bz is not None,
            "node_lo": bz[0] if bz else None, "node_hi": bz[1] if bz else None, "node_share": bz[2] if bz else None,
            "above": float(above),
            "next_res": nz[0] if nz else None, "next_pct": (nz[0] / C - 1) * 100 if nz else None,
            "support": sz[1] if sz else None, "zones": zones}


def early_buys(df: pd.DataFrame, scan_today: pd.DataFrame | None, histories: dict, quotes: dict, ia_groups: set,
               vol_min: float = 1.5, chg_min: float = 2.0, rs_min: float = 70, max_ext: float = 20.0,
               max_above: float = 35.0, min_cap_eok: float = 1000) -> pd.DataFrame:
    """선매입 후보: 오늘 인더스트리 액션 섹터 + 거래량 vol_min배↑ 양봉 + 매물대 돌파 + RS(70↑ 또는 1개월 RS 80↑)
    + 20일선 이격 max_ext% 이하. 52주 신고가 전이어도 돼요(이미 신고가면 '신고가 돌파'로 표시)."""
    cols = ["code", "name", "group", "price", "chg", "vx", "dcr", "rs", "node_lo", "node_hi", "node_share", "above",
            "next_res", "next_pct", "to_res", "nh", "stop", "stop_pct", "target", "score", "why"]
    if scan_today is None or scan_today.empty or not ia_groups:
        return pd.DataFrame(columns=cols)
    import data
    keep = [k for k in ("name", "rs", "rs_1m", "cap_krw", "dist_ma20", "lead_rank", "lead_ok") if k in df]
    t = scan_today[scan_today["group"].isin(list(ia_groups)) & (scan_today["vx"] >= vol_min) & scan_today["up"]
                   & (pd.to_numeric(scan_today["chg"], errors="coerce") >= chg_min)]
    if t.empty:
        return pd.DataFrame(columns=cols)
    t = t.merge(df[["code", *keep]].drop_duplicates("code"), on="code", how="left")
    num = lambda x: None if x is None or (isinstance(x, float) and x != x) else x  # noqa: E731
    today = data.now_kst().date()
    out = []
    for r in t.itertuples():
        rs, rs1 = num(getattr(r, "rs", None)), num(getattr(r, "rs_1m", None))
        if not ((rs is not None and rs >= rs_min) or (rs1 is not None and rs1 >= 80)):
            continue
        cap = num(getattr(r, "cap_krw", None))
        if cap is not None and cap / 1e8 < min_cap_eok:
            continue
        ext = num(getattr(r, "dist_ma20", None))
        if ext is not None and ext > max_ext:
            continue
        hist = (histories.get(r.code) or (None, None, None))[1]
        if hist is None or len(hist) < 80:
            continue
        d, o, h, l, c, v = data._session_bars(hist, quotes.get(r.code), today)
        vp = volume_profile(d, h, l, c, v, len(c) - 1)
        if not vp or not vp["broke"] or vp["above"] > max_above:
            continue
        C = float(c[-1])
        stop = max(C * (1 - SCOUT_STOP / 100), vp["node_lo"] * 0.995)      # 뚫은 매물대 아래, 최대 1R 8%
        sp = (1 - stop / C) * 100
        nh = bool(r.brk_close)
        lr = getattr(r, "lead_rank", None)
        lead = getattr(r, "lead_ok", None) is True and lr == lr and lr is not None and int(lr) == 1
        why = [f"매물대 {vp['node_lo']:,.0f}~{vp['node_hi']:,.0f} 돌파", f"거래량 {r.vx:.1f}배", "액션 섹터",
               f"위 매물 {vp['above']:.0f}%", "52주 신고가 돌파" if nh else (f"신고가까지 {r.to_res:.1f}%" if r.to_res == r.to_res and r.to_res is not None else None),
               f"다음 저항 +{vp['next_pct']:.0f}%" if vp["next_pct"] is not None else "위로 큰 매물대 없음",
               "👑 대장주" if lead else None]
        score = r.vx + (35 - vp["above"]) / 10 + (vp["node_share"] or 0) / 2 + (1.5 if lead else 0) + ((rs or 60) - 60) / 20
        out.append({"code": r.code, "name": r.name, "group": r.group, "price": C, "chg": r.chg, "vx": r.vx, "dcr": r.dcr, "rs": rs,
                    "node_lo": vp["node_lo"], "node_hi": vp["node_hi"], "node_share": vp["node_share"], "above": vp["above"],
                    "next_res": vp["next_res"], "next_pct": vp["next_pct"], "to_res": r.to_res, "nh": nh,
                    "stop": stop, "stop_pct": sp, "target": C * (1 + 3 * sp / 100), "score": score,
                    "why": " · ".join(w for w in why if w)})
    o = pd.DataFrame(out, columns=cols)
    return o.sort_values("score", ascending=False).reset_index(drop=True) if len(o) else o
