"""🚦 시장 신호 — 오닐의 분산일(Distribution Day)·팔로우 스루 데이(FTD)와 시장 국면, 리 태너 초기 매수 지점.

시장 국면(스탁이지 '지금 여긴'과 같은 네 칸)
  상승 추세 → (분산일이 평소보다 많이 쌓이면) 상승 둔화 → (고점에서 corr_pct% 넘게 빠지면) 조정 국면
  → (바닥 뒤 처음 오른 날 = 랠리 데이, Day 1) 반등 시도 → (Day 4 이후 +ftd_pct%·거래량 증가) FTD → 상승 추세
  FTD 뒤 랠리 저점을 깨면 FTD 실패 → 다시 조정 국면.

분산일(책 기준): 전날보다 거래량이 늘며 0.2% 넘게 내린 날 + 정체일(거래량은 늘었는데 거의 못 오르고 아래쪽 마감).
  IBD 운용 규칙: 25거래일 지나거나 그 뒤 지수가 그날 종가보다 5% 넘게 오르면 지움, FTD가 나오면 0으로.
  ⚠️ 깡토 백테스트(코스피 1990~2026): 책 기준(5주 4~5개)은 한국 지수에서 경고가 너무 자주 켜져 의미가 약함
  → '한국 보정' = 같은 지수의 지난 2~3년 분산일 개수 분포에서 지금이 상위 몇 %인지로 봐요.
FTD 통계(같은 글): 하락장(-20%) 바닥 뒤엔 항상 FTD가 나왔지만(코스피 25/25) 하락장 FTD의 절반 가까이는 바닥을 다시 깨며 실패.
  → FTD는 '공격' 신호가 아니라 '다시 볼 허락'. 나오기 전엔 성급히 올라타지 않고, 나오면 점진적으로, 랠리 저점을 깨면 되돌림.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MARKET_VERSION = "2026-10-28-lastftd"    # app.py의 MARKET_VERSION과 같아야 해요(바뀐 market.py를 서버가 새로 읽게)
MKT_DEFAULTS = {"ftd_pct": 1.25, "ftd_day": 4, "dd_pct": 0.2, "corr_pct": 10.0, "dd_window": 25, "dd_erase": 5.0}
STATE_TXT = {"up": "상승 추세", "press": "상승 둔화", "corr": "조정 국면", "rally": "반등 시도"}
STATE_EM = {"up": "🟢", "press": "🟡", "corr": "🔴", "rally": "🟠"}


def _arr(df: pd.DataFrame):
    d = pd.to_datetime(df["date"]).dt.normalize().to_numpy()
    o, h, l, c = (pd.to_numeric(df[k], errors="coerce").to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    v = pd.to_numeric(df["volume"], errors="coerce").to_numpy(dtype=float) if "volume" in df else np.full(len(c), np.nan)
    return d, o, h, l, c, v


def analyze(df: pd.DataFrame | None, p: dict | None = None) -> dict | None:
    """지수 일봉 → 날짜별 국면·분산일·FTD 기록."""
    p = {**MKT_DEFAULTS, **(p or {})}
    if df is None or len(df) < 60:
        return None
    df = df.dropna(subset=["close"]).reset_index(drop=True)
    d, o, h, l, c, v = _arr(df)
    n = len(c)
    chg = np.r_[np.nan, (c[1:] / c[:-1] - 1) * 100]
    vup = np.r_[False, v[1:] > v[:-1]]
    rng = h - l
    pos = np.where(rng > 0, (c - l) / np.where(rng > 0, rng, 1), 0.5)
    is_dd = (chg <= -p["dd_pct"]) & vup
    prev_chg = np.r_[np.nan, chg[:-1]]
    is_stall = vup & (chg >= 0) & (chg < 0.3) & (prev_chg > 0) & (chg < prev_chg / 2) & (pos < 0.5)
    has_vol = np.isfinite(v).sum() > n * 0.8 and np.nanmax(v) > 0

    state = np.empty(n, dtype=object)
    dd_cnt = np.zeros(n, dtype=int)
    day_no = np.zeros(n, dtype=int)
    active: list[int] = []           # 살아 있는 분산일 인덱스
    events: list[dict] = []          # FTD·실패·랠리 기록
    st = "up"
    peak = c[0]
    low_i = None                     # 조정 바닥(가장 낮은 저가) 위치
    rally_i = None                   # 랠리 데이(Day 1)
    ftd_low = None                   # FTD가 지켜야 할 랠리 저점
    ftd_ev = None
    for i in range(n):
        # 분산일 갱신(상승 쪽 국면에서만 셈)
        active = [j for j in active if i - j < p["dd_window"] and c[i] <= c[j] * (1 + p["dd_erase"] / 100)]
        if has_vol and st in ("up", "press") and (is_dd[i] or is_stall[i]):
            active.append(i)
        dd_cnt[i] = len(active)
        # FTD 실패 감시
        if ftd_low is not None and l[i] < ftd_low:
            if ftd_ev is not None:
                ftd_ev["failed"] = d[i]
            events.append({"kind": "fail", "i": i, "date": d[i], "txt": "FTD 실패 — 랠리 저점 이탈"})
            ftd_low, ftd_ev = None, None
            st, low_i, rally_i, peak = "corr", i, None, max(peak, c[i])
        if st in ("up", "press"):
            peak = max(peak, c[i])
            if c[i] <= peak * (1 - p["corr_pct"] / 100):
                st, low_i, rally_i = "corr", i, None
                ftd_low, ftd_ev = None, None
                active = []
        if st in ("corr", "rally"):
            if low_i is None or l[i] < l[low_i]:
                low_i, rally_i = i, None                     # 새 바닥 → 반등 시도 다시
                st = "corr"
            elif rally_i is None and i > low_i and (chg[i] > 0 or pos[i] >= 0.5):
                rally_i, st = i, "rally"
                events.append({"kind": "rally", "i": i, "date": d[i], "txt": "랠리 데이(Day 1)"})
            if st == "rally" and rally_i is not None:
                k = i - rally_i + 1
                day_no[i] = k
                if (k >= p["ftd_day"] and chg[i] >= p["ftd_pct"] and (vup[i] or not has_vol)):
                    dd0 = (c[low_i] / max(c[:low_i + 1].max(), 1e-9) - 1) * 100
                    ftd_ev = {"kind": "ftd", "i": i, "date": d[i], "day": k, "chg": float(chg[i]),
                              "vol_x": float(v[i] / v[i - 1]) if has_vol and v[i - 1] > 0 else None,
                              "low_date": d[low_i], "low": float(l[low_i]), "draw": float(dd0), "failed": None,
                              "txt": f"FTD — Day {k}, {chg[i]:+.2f}%"}
                    events.append(ftd_ev)
                    ftd_low = float(l[low_i])
                    st, peak, active = "up", c[i], []
        if st == "up" and dd_cnt[i] >= 1:
            pass
        state[i] = st
    # 한국 보정: 분산일 개수의 지난 분포(상승 쪽 국면 날만)
    up_mask = np.isin(state, ["up", "press"])
    hist_cnt = dd_cnt[up_mask][-500:]
    q80 = float(np.percentile(hist_cnt, 80)) if len(hist_cnt) >= 60 else 5.0
    q50 = float(np.median(hist_cnt)) if len(hist_cnt) >= 60 else 3.0
    # 상승 둔화 = 분산일이 평소 상위 20% 이상(한국 보정) — 책 기준(5개↑)은 따로 보여 줘요
    for i in range(n):
        if state[i] == "up" and dd_cnt[i] >= max(q80, 1) and dd_cnt[i] > q50:
            state[i] = "press"
    # FTD 성적(5·20·60일 뒤, 60일 안 랠리 저점 이탈 = 실패)
    ftds = []
    for e in events:
        if e["kind"] != "ftd":
            continue
        i = e["i"]
        r = {k: ((c[i + k] / c[i] - 1) * 100 if i + k < n else None) for k in (5, 20, 60)}
        fail_i = next((j for j in range(i + 1, min(n, i + 61)) if l[j] < e["low"]), None)
        done = i + 60 < n or fail_i is not None
        ftds.append({**{k: e[k] for k in ("date", "day", "chg", "vol_x", "low_date", "low", "draw")},
                     "r5": r[5], "r20": r[20], "r60": r[60],
                     "result": ("❌ 실패" if fail_i is not None else ("✅ 유지" if done else "⏳ 진행 중")),
                     "fail_date": d[fail_i] if fail_i is not None else None})
    last = n - 1
    dd_now = [{"date": d[j], "chg": float(chg[j]), "vol_x": float(v[j] / v[j - 1]) if v[j - 1] > 0 else None,
               "kind": "분산일" if is_dd[j] else "정체일", "left": p["dd_window"] - (last - j)} for j in active]
    peak_all = float(np.nanmax(c[max(0, n - 250):]))
    out = {"state": state[last], "state_txt": STATE_TXT[state[last]], "em": STATE_EM[state[last]],
           "day": int(day_no[last]), "dd": int(dd_cnt[last]), "dd_list": dd_now[::-1], "q50": q50, "q80": q80,
           "dd_pct_rank": float((hist_cnt <= dd_cnt[last]).mean() * 100) if len(hist_cnt) else None,
           "book_warn": dd_cnt[last] >= 5, "ftds": ftds[::-1], "events": events[::-1][:30],
           "draw": (c[last] / peak_all - 1) * 100, "close": float(c[last]), "date": d[last],
           "low": float(l[low_i]) if low_i is not None and state[last] in ("corr", "rally") else None,
           "rally_date": d[rally_i] if rally_i is not None and state[last] == "rally" else None,
           "ftd_low": ftd_low, "last_ftd": ftds[-1] if ftds else None, "has_vol": bool(has_vol),   # last_ftd = 가장 최근(ftds는 오래된 순)
           # 정찰병 신호(scout.py)용: 조정 바닥·그 전 고점 날짜, 오늘 등락·거래량 증가·종가 위치
           "low_date": d[low_i] if low_i is not None else None,
           "peak_date": d[max(0, low_i - 250) + int(np.nanargmax(c[max(0, low_i - 250):low_i + 1]))] if low_i is not None else None,
           "chg": float(chg[last]) if np.isfinite(chg[last]) else None, "vol_up": bool(vup[last]),
           "pos": float(pos[last]),
           "series": pd.DataFrame({"date": d, "close": c, "state": state, "dd": dd_cnt, "day": day_no, "chg": chg})}
    return out


def ftd_stats(ftds: list[dict]) -> dict:
    done = [f for f in ftds if f["result"] != "⏳ 진행 중"]
    if not done:
        return {"n": 0}
    ok = [f for f in done if f["result"] == "✅ 유지"]
    r60 = [f["r60"] for f in done if f["r60"] is not None]
    return {"n": len(done), "ok": len(ok), "rate": len(ok) / len(done) * 100,
            "r60": float(np.median(r60)) if r60 else None}


def action_text(m: dict) -> tuple[str, str]:
    """국면별 대응(깡토 글 + 오닐): (요약, 설명)."""
    s = m["state"]
    if s == "corr":
        return ("🔴 조정 국면 — 반등에 성급히 올라타지 않기",
                "아직 바닥 확인 전. 하락장 중간 반등은 FTD 전까지 지켜보기 · 무너진 종목보다 버티는 종목(견조주) 목록 만들기 · "
                "추세추종이면 현금 비중이 높아야 정상.")
    if s == "rally":
        return (f"🟠 반등 시도 Day {m['day']} — FTD 기다리는 중",
                f"랠리 데이 뒤 {m['day']}일째. Day 4 이후 큰 폭 상승 + 거래량 증가(FTD)를 기다리고, 그 전에 바닥"
                + (f"({m['low']:,.2f})" if m.get('low') else "") + "을 깨면 반등 시도 실패 → 새 바닥부터 다시.")
    if s == "press":
        return ("🟡 상승 둔화 — 분산일이 평소보다 많음",
                "큰돈이 파는 흔적이 평소보다 많이 쌓임(한국 보정). 신규 매수는 정찰병 크기로, 손절선은 엄격하게, 약한 종목부터 정리.")
    lf = m.get("last_ftd")
    tail = ""
    if lf and lf["result"] == "⏳ 진행 중":
        tail = f" FTD({pd.Timestamp(lf['date']):%m/%d}) 뒤 진행 중 — 랠리 저점 {lf['low']:,.2f}를 깨면 모든 판단을 되돌리기."
    return ("🟢 상승 추세 — 원칙대로 매매",
            "FTD는 '공격' 신호가 아니라 '다시 볼 허락'. 주도주가 실제로 좋은 베이스·신고가를 만드는지 같이 확인하며 점진적으로 비중 확대." + tail)


# ─────────────────────────── 리 태너 초기 매수 지점(Early Entry) ───────────────────────────
EE_DEFAULTS = {"base_min": 15, "base_max": 65, "depth_min": 8.0, "depth_max": 40.0, "shake_pct": 5.0, "near_pivot": 15.0,
               "quiet": 0.75}


def _swing_highs(h: np.ndarray, k: int = 2) -> list[int]:
    return [i for i in range(k, len(h) - k) if h[i] == h[i - k:i + k + 1].max()]


def early_entry(d, o, h, l, c, v, p: dict | None = None) -> dict | None:
    """베이스 안에서 표준 피벗보다 싸게 사는 자리(리 태너): 하락 추세선 돌파 · 손잡이 저점 조용한 거래량 · Shakeout +3.
    마지막 칸 = 지금 세션. 신호가 없으면 None."""
    p = {**EE_DEFAULTS, **(p or {})}
    n = len(c)
    if n < 120 or not c[-1] > 0:
        return None
    win = p["base_max"]
    seg_h = h[-win:-p["base_min"] // 3]
    if not len(seg_h):
        return None
    k_hi = int(np.argmax(seg_h))
    pi = n - win + k_hi                                   # 베이스 왼쪽 고점(표준 피벗의 기준)
    pivot = float(h[pi])
    blen = n - 1 - pi
    if blen < 5:
        return None
    base_low_i = pi + int(np.argmin(l[pi:]))
    depth = (pivot - float(l[base_low_i])) / pivot * 100
    price = float(c[-1])
    if not (p["depth_min"] <= depth <= p["depth_max"]):
        return None
    below = (pivot / price - 1) * 100
    if below < 0 or below > p["near_pivot"]:
        return None                                       # 이미 피벗 위(표준 돌파) 또는 너무 멀리
    ma50 = np.mean(c[-50:])
    vol50 = np.nanmean(v[-51:-1]) if n > 51 else np.nanmean(v)
    sigs = []
    # ① 하락 추세선 돌파: 피벗 고점과 그 뒤 가장 높은 낮은 고점(스윙 고점)을 잇는 선
    sw = [i for i in _swing_highs(h[pi:], 2)]
    sw = [pi + i for i in sw if pi + i > pi + 2 and pi + i < n - 1]
    if sw:
        j = max(sw, key=lambda i: h[i])
        if h[j] < pivot:
            slope = (h[j] - pivot) / (j - pi)
            line = lambda i: pivot + slope * (i - pi)
            if c[-1] > line(n - 1) and c[-2] <= line(n - 2) * 1.002:
                sigs.append(("추세선 돌파", float(line(n - 1)),
                             f"피벗 {pivot:,.0f}에서 내려오는 고점 연결선({line(n - 1):,.0f})을 종가로 돌파"))
    # ② 손잡이 저점 + 조용한 거래량: 베이스 위쪽 절반, 최근 10일 저점 근처, 거래량 마름, 50일선 위
    lo10 = float(np.min(l[-10:]))
    upper = float(l[base_low_i]) + (pivot - float(l[base_low_i])) * 0.5
    dry = float(np.nanmean(v[-3:]) / vol50) if vol50 else np.nan
    if (blen >= 10 and price >= upper and price <= lo10 * 1.03 and dry == dry and dry <= p["quiet"] and price > ma50
            and float(np.min(l[-3:])) >= lo10):
        sigs.append(("손잡이 저점·조용한 거래량", lo10, f"최근 10일 저점 {lo10:,.0f} 근처에서 거래량이 평소의 {dry:.2f}배로 마름"))
    # ③ Shakeout +3: 두 번째 저점이 첫 저점을 살짝 깨고(털기) 첫 저점 +k% 위로 회복
    if blen >= 15:
        mid = pi + blen // 2
        l1_i = pi + 1 + int(np.argmin(l[pi + 1:mid + 1]))
        l2_i = mid + 1 + int(np.argmin(l[mid + 1:])) if mid + 1 < n else None
        if l2_i is not None and l2_i < n - 1:
            l1, l2 = float(l[l1_i]), float(l[l2_i])
            trig = l1 * (1 + p["shake_pct"] / 100)
            if l2 < l1 and (l1 - l2) / l1 * 100 <= 10 and c[-1] >= trig and c[-2] < trig:
                sigs.append(("Shakeout +3", trig, f"첫 저점 {l1:,.0f}을 {l2:,.0f}까지 깼다가(털기) 첫 저점 +{p['shake_pct']:.0f}% "
                             f"{trig:,.0f} 회복"))
    if not sigs:
        return None
    entry = price
    return {"ee_sigs": sigs, "ee_kind": " · ".join(s[0] for s in sigs), "ee_pivot": pivot, "ee_below": below,
            "ee_depth": depth, "ee_len": blen, "ee_stop": entry * 0.93, "ee_dry": dry, "ee_trigger": sigs[0][1],
            "ee_why": " / ".join(s[2] for s in sigs), "ee_pivot_date": d[pi]}
