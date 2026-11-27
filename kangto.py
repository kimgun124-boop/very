"""깡토 학습 규칙 두 가지를 계산으로 옮긴 모듈 (2026.11.27 학습).

① 🧺 하락장 바닥 줍기 — 깡토 블로그 「하락장에서 할 수 있는 매매」(2026.11.27) 최종 규칙
   - 출동: 코스피 종가가 최근 1년(250거래일) 최고 종가보다 12% 넘게 낮은 날만
   - 종목: 시가총액 1조 원↑ 보통주 · 종가가 20일선보다 22% 넘게 아래 · 20일 평균 거래대금 10억↑ · 주가 1,000원↑ · 상장 1년↑
   - 사기: 조건에 걸린 다음 날 아침 시가에 한 번에 · 동시에 2종목까지(한 종목에 그날 계좌 평가액의 절반) · 후보가 여럿이면 20일 평균 거래대금 큰 순
   - 팔기: 종가가 5일선 위로 올라온 다음 날 시가에 전부 · 산 날부터 20거래일이 지나면 다음 날 시가에 정리
   - 안 하는 것: 손절 · 물타기 · 신용 (출동이 꺼져도 산 종목은 파는 규칙대로, 새로 사지만 않음)
   깡토 결론: 확실히 해 준 건 '하락장에서 덜 아프게 버티기' — 지수를 이긴다는 근거는 약함(과최적화·하루 늦으면 연 6.5%).

② 🚪 돌파 확인 — 깡토 5강 「진입 타점: 돌파매매」(효석 수업 정리)
   - 진짜 돌파: 거래량 폭발 · 윗꼬리 없는 장대양봉(DCR 높음) · 섹터 동반 · 신고가
   - 가짜 돌파: 거래량 없이 슬쩍 · 긴 윗꼬리 · 혼자만 상승 · 바닥권 장대양봉
   - 건전한 조정 = 1차 상승분의 절반 이상을 반납하지 않는 조정 · VCP = 흔들림·거래량이 줄다가 거래량 실은 돌파
   - 베이지안: 트리거 하나로 들어가지 않고, 여러 트리거가 같은 방향을 가리킬 때 확률(비중)을 올린다 — 리스크는 한도 안에서
   - 진입: 장중 돌파 순간 절반 · 종가까지 유지되면 나머지 절반 · 밀리면 −1R 손절 · 다시 돌파하면 재진입 · 오늘 못 샀으면 내일 OK, 일주일 뒤는 X
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

import data

KT5_VERSION = "2026-11-27-kt5env"

# ─────────────────────────── ① 🧺 하락장 바닥 줍기 ───────────────────────────
BF_DEFAULTS = {"arm_dd": 12.0, "gap20": 22.0, "min_cap": 1e12, "min_tv": 10e8, "min_px": 1000.0, "min_bars": 250,
               "slots": 2, "exit_ma": 5, "time_stop": 20, "fee": 0.00015, "tax": 0.0020, "slip": 0.001}


def _num(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if np.isfinite(x) else None


def bf_market(kospi: pd.DataFrame | None, p: dict | None = None) -> dict:
    """출동 조건: 코스피 종가가 최근 250거래일 최고 종가보다 arm_dd% 넘게 낮은가."""
    p = {**BF_DEFAULTS, **(p or {})}
    if kospi is None or len(kospi) < 20 or "close" not in kospi:
        return {"armed": None, "dd": None, "peak": None, "close": None, "peak_date": None}
    c = pd.to_numeric(kospi["close"], errors="coerce").to_numpy(dtype=float)
    win = c[-250:]
    i = int(np.nanargmax(win))
    peak = float(win[i])
    last = float(c[-1])
    dd = (last / peak - 1) * 100 if peak else None
    pdt = pd.to_datetime(kospi["date"].iloc[-len(win) + i]).date() if "date" in kospi else None
    return {"armed": bool(dd is not None and dd <= -p["arm_dd"]), "dd": dd, "peak": peak, "close": last, "peak_date": pdt}


def _is_common(code: str, name: str) -> bool:
    import re
    return data.is_kr(code) and str(code).endswith("0") and "스팩" not in str(name) \
        and not re.search(r"(ETF|ETN|리츠)$", str(name or ""))


def bf_scan(df: pd.DataFrame, histories: dict, quotes: dict, p: dict | None = None, today: date | None = None) -> pd.DataFrame:
    """오늘(장중이면 지금 가격) 기준 바닥 줍기 규칙에 걸린 종목. 규칙 전부 통과면 ok=True."""
    p = {**BF_DEFAULTS, **(p or {})}
    today = today or data.now_kst().date()
    rows = []
    if df is None or df.empty:
        return pd.DataFrame()
    for r in df.drop_duplicates("code").itertuples():
        code, name = r.code, getattr(r, "name", "")
        if not _is_common(code, name):
            continue
        hist = (histories.get(code) or (None, None, None))[1]
        if hist is None or len(hist) < 30:
            continue
        q = (quotes or {}).get(code)
        if data.is_halted(hist, q):
            continue
        d, o, h, l, c, v = data._session_bars(hist, q, today)
        n = len(c)
        if n < 25:
            continue
        price = float(c[-1])
        ma20 = float(np.nanmean(c[-20:]))
        ma5 = float(np.nanmean(c[-5:]))
        gap = (price / ma20 - 1) * 100 if ma20 else None
        tv = (c * v)[-20:]
        tv = tv[np.isfinite(tv)]
        tv20 = float(tv.mean()) if len(tv) >= 10 else None
        cap = _num(getattr(r, "cap_krw", None))
        checks = {"gap": gap is not None and gap <= -p["gap20"],
                  "cap": cap is not None and cap >= p["min_cap"],
                  "tv": tv20 is not None and tv20 >= p["min_tv"],
                  "px": price >= p["min_px"],
                  "age": n >= p["min_bars"]}
        if gap is None or gap > -p["gap20"] * 0.6:          # 20일선에서 많이 떨어진 종목만 표에(-13%↓)
            continue
        rows.append({"code": code, "name": name, "group": getattr(r, "group", ""), "price": price,
                     "change": _num(getattr(r, "change", None)), "ma20": ma20, "ma5": ma5, "gap20": gap,
                     "cap_krw": cap, "tv20": tv20, "bars": n, **{f"c_{k}": v_ for k, v_ in checks.items()},
                     "ok": all(checks.values()), "url": getattr(r, "url", data.quote_url(code))})
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["_o"] = ~out["ok"]
    return out.sort_values(["_o", "tv20"], ascending=[True, False]).drop(columns="_o").reset_index(drop=True)


def _frames(df: pd.DataFrame, histories: dict, p: dict) -> dict:
    """종목별 날짜 기준 표(시가·종가·5일선·20일선·20일 평균 거래대금·그날 시총 추정)."""
    out = {}
    for r in df.drop_duplicates("code").itertuples():
        code, name = r.code, getattr(r, "name", "")
        if not _is_common(code, name):
            continue
        cap, price = _num(getattr(r, "cap_krw", None)), _num(getattr(r, "price", None))
        if not cap or not price:
            continue
        hist = (histories.get(code) or (None, None, None))[1]
        if hist is None or len(hist) < 60:
            continue
        h = hist[["date", "open", "close", "volume"]].copy()
        h["date"] = pd.to_datetime(h["date"]).dt.normalize()
        h = h.drop_duplicates("date", keep="last").set_index("date").astype(float)
        if cap / price * float(h["close"].max()) < p["min_cap"]:     # 1년 내내 1조가 안 됐던 종목은 건너뛰기(속도)
            continue
        c = h["close"]
        h["ma5"] = c.rolling(p["exit_ma"]).mean()
        h["ma20"] = c.rolling(20).mean()
        h["tv20"] = (c * h["volume"]).rolling(20, min_periods=10).mean()
        h["cap"] = cap / price * c                                   # 지금 주식 수 × 그날 종가(근사)
        h["nbar"] = np.arange(1, len(h) + 1)
        full = len(hist) >= data.HIST_COUNT - 5                      # 일봉을 끝까지 받았으면 그 전부터 상장
        h["aged"] = (h["nbar"] >= p["min_bars"]) | full
        h["sig"] = ((c / h["ma20"] - 1) * 100 <= -p["gap20"]) & (h["cap"] >= p["min_cap"]) & \
                   (h["tv20"] >= p["min_tv"]) & (c >= p["min_px"]) & h["aged"]
        out[code] = (name, getattr(r, "group", ""), h)
    return out


def bf_sim(df: pd.DataFrame, histories: dict, kospi: pd.DataFrame, start: date, seed: float = 1e7,
           p: dict | None = None) -> dict | None:
    """바닥 줍기 최종 규칙을 일봉으로 그대로 돌린 모의 계좌(종가 판단 → 다음 날 시가 체결)."""
    p = {**BF_DEFAULTS, **(p or {})}
    if kospi is None or len(kospi) < 60:
        return None
    k = kospi[["date", "close"]].copy()
    k["date"] = pd.to_datetime(k["date"]).dt.normalize()
    k = k.drop_duplicates("date", keep="last").set_index("date")["close"].astype(float)
    k_peak = k.rolling(250, min_periods=1).max()
    armed = (k / k_peak - 1) * 100 <= -p["arm_dd"]
    days = [d for d in k.index if d >= pd.Timestamp(start)]
    if len(days) < 2:
        return None
    fr = _frames(df, histories, p)
    buy_cost = 1 + p["fee"] + p["slip"]
    sell_keep = 1 - p["fee"] - p["tax"] - p["slip"]
    cash, hold, pend_buy, pend_sell = float(seed), {}, [], {}
    journal, trades, curve = [], [], []
    peak_eq, mdd, armed_days = float(seed), 0.0, 0

    def row(code, d):
        h = fr[code][2]
        return h.loc[d] if d in h.index else None

    def value(d):
        tot = cash
        for code, x in hold.items():
            r_ = row(code, d)
            px = float(r_["close"]) if r_ is not None and r_["close"] == r_["close"] else x["last"]
            x["last"] = px
            tot += px * x["qty"]
        return tot

    prev_eq = float(seed)
    for di, d in enumerate(days):
        # 1) 아침 시가: 어제 종가로 정한 매도 → 매수
        for code, why in list(pend_sell.items()):
            x, r_ = hold.get(code), row(code, d)
            if x is None:
                pend_sell.pop(code)
                continue
            if r_ is None or not r_["open"] > 0:                  # 거래정지 등으로 시가가 없으면 다음 날 다시
                continue
            px = float(r_["open"])
            amt = px * x["qty"] * sell_keep
            pnl = amt - x["cost"]
            cash += amt
            trades.append({"date": d, "code": code, "name": x["name"], "side": "매도", "why": why, "price": px,
                           "qty": x["qty"], "amount": px * x["qty"], "pnl": pnl})
            j = x["j"]
            j.update(status="청산", exit_date=d, exit_px=px, exit_why=why, days=x["held"], pnl=pnl,
                     ret=(amt / x["cost"] - 1) * 100)
            hold.pop(code)
            pend_sell.pop(code)
        for code, why_in in pend_buy:
            if code in hold or len(hold) >= p["slots"]:
                continue
            r_ = row(code, d)
            if r_ is None or not r_["open"] > 0:
                continue
            px = float(r_["open"])
            budget = min(prev_eq / p["slots"], cash)
            qty = int(budget // (px * buy_cost))
            if qty <= 0:
                continue
            cost = px * qty * buy_cost
            cash -= cost
            name, grp = fr[code][0], fr[code][1]
            j = {"status": "보유", "code": code, "name": name, "group": grp, "entry_date": d, "entry_px": px, "qty": qty,
                 "weight": cost / prev_eq * 100 if prev_eq else None, "why": why_in}
            journal.append(j)
            hold[code] = {"name": name, "qty": qty, "cost": cost, "held": 0, "last": px, "j": j}
            trades.append({"date": d, "code": code, "name": name, "side": "매수", "why": why_in, "price": px, "qty": qty,
                           "amount": px * qty, "pnl": None})
        pend_buy = []
        # 2) 장 끝 종가: 파는 규칙 → 사는 규칙(출동 중일 때만)
        for code, x in hold.items():
            x["held"] += 1
            r_ = row(code, d)
            if r_ is None or code in pend_sell:
                continue
            if r_["close"] > r_["ma5"]:
                pend_sell[code] = f"종가가 {p['exit_ma']}일선 위({r_['close']:,.0f} > {r_['ma5']:,.0f}) → 다음 날 시가"
            elif x["held"] >= p["time_stop"]:
                pend_sell[code] = f"{p['time_stop']}거래일 지남 → 다음 날 시가 정리"
        is_armed = bool(armed.get(d, False))
        armed_days += is_armed
        if is_armed:
            free = p["slots"] - sum(1 for c_ in hold if c_ not in pend_sell)
            if free > 0:
                cands = []
                for code, (_, _, h) in fr.items():
                    if code in hold or d not in h.index:
                        continue
                    r_ = h.loc[d]
                    if bool(r_["sig"]):
                        cands.append((float(r_["tv20"]), code, (r_["close"] / r_["ma20"] - 1) * 100))
                cands.sort(reverse=True)
                kdd = (k[d] / k_peak[d] - 1) * 100
                pend_buy = [(c_, f"20일선 {g:+.1f}% · 코스피 1년 고점 {kdd:+.1f}% · 거래대금 {tv / 1e8:,.0f}억")
                            for tv, c_, g in cands[:free]]
        eq = value(d)
        prev_eq = eq
        peak_eq = max(peak_eq, eq)
        mdd = min(mdd, (eq / peak_eq - 1) * 100)
        curve.append({"date": d, "equity": eq, "kospi": seed * float(k[d]) / float(k[days[0]])})
    for code, x in hold.items():
        x["j"].update(cur_px=x["last"], days=x["held"], pnl=x["last"] * x["qty"] * sell_keep - x["cost"],
                      ret=(x["last"] * x["qty"] * sell_keep / x["cost"] - 1) * 100,
                      exit_why=pend_sell.get(code) or "보유 중")
    closed = [j for j in journal if j["status"] == "청산"]
    wins = sum(1 for j in closed if j["pnl"] > 0)
    eq = curve[-1]["equity"]
    return {"seed": seed, "start": days[0].date(), "end": days[-1].date(), "equity": eq, "pnl": eq - seed,
            "ret": (eq / seed - 1) * 100, "kospi_ret": (float(k[days[-1]]) / float(k[days[0]]) - 1) * 100,
            "mdd": mdd, "closed": len(closed), "wins": wins, "cash": cash, "armed_days": armed_days, "days": len(days),
            "journal": journal[::-1], "trades": trades[::-1], "curve": pd.DataFrame(curve), "universe": len(fr),
            "pending_buy": [c_ for c_, _ in pend_buy], "pending_sell": dict(pend_sell)}


# ─────────────────────────── ② 🚪 돌파 확인 — 깡토 5강 ───────────────────────────
BO_DEFAULTS = {"vol_x": 1.5, "vol_big": 2.0, "dcr_min": 70.0, "dcr_bad": 50.0, "body_min": 0.55, "bottom": 25.0,
               "retr_max": 0.5, "base_days": 60, "near": 3.0, "recent": 5, "min_trig": 5}
BO_TRIGGERS = [("vol", "거래량 폭발"), ("bull", "장대양봉·DCR 높음"), ("nh", "52주 신고가 종가 돌파"), ("sector", "섹터 동반"),
               ("base", "건전한 조정(절반 이내)"), ("vcp", "VCP(흔들림·거래량 축소)"), ("atr", "평균 ATR 이상 움직임"),
               ("mkt", "시장 분위기(지수 20일선 위)")]
BO_FAKES = [("f_vol", "거래량 없이 슬쩍"), ("f_wick", "긴 윗꼬리"), ("f_alone", "혼자만 상승"), ("f_bottom", "바닥권 장대양봉")]
BO_VERDICTS = ["진짜 돌파", "애매 — 종가 확인", "가짜 돌파 의심", "돌파 전"]


def bo_quality(d, o, h, l, c, v, sector_ok: bool | None = None, market_ok: bool | None = None,
               p: dict | None = None, today: date | None = None) -> dict | None:
    """마지막 칸 = 지금 세션. 5강 기준으로 오늘 봉(또는 최근 돌파)을 점검해요."""
    p = {**BO_DEFAULTS, **(p or {})}
    today = today or data.now_kst().date()
    o, h, l, c, v = (np.asarray(x, dtype=float) for x in (o, h, l, c, v))
    n = len(c)
    if n < 80:
        return None
    is_live = len(d) and d[-1] == np.datetime64(today, "D") and data._is_session_now(today)
    frac = data.session_frac() if is_live else 1.0
    v50 = np.nanmean(v[-51:-1])
    vol_x = (v[-1] / frac) / v50 if v50 and v50 > 0 and np.isfinite(v[-1]) else None
    rng = h[-1] - l[-1]
    dcr = (c[-1] - l[-1]) / rng * 100 if rng > 0 else 50.0
    body = abs(c[-1] - o[-1])
    upper = h[-1] - max(o[-1], c[-1])
    chg = (c[-1] / c[-2] - 1) * 100 if c[-2] > 0 else 0.0
    atr = data._atr_np(h[:-1][-41:], l[:-1][-41:], c[:-1][-41:], 20)
    prior = h[max(0, n - 251):n - 1]
    hi52 = float(np.nanmax(prior))
    nh_close = bool(c[-1] > hi52)
    nh_touch = bool(h[-1] > hi52)
    dist = (hi52 / c[-1] - 1) * 100
    # 최근 recent일 안에 종가로 52주 고점을 처음 넘은 날(오늘 못 샀으면 내일 OK, 일주일 뒤는 X)
    brk_ago = None
    for k_ in range(0, min(p["recent"], n - 252)):
        i = n - 1 - k_
        ref = np.nanmax(h[i - 250:i])
        if c[i] > ref and not (c[i - 1] > np.nanmax(h[i - 251:i - 1])):
            brk_ago = k_
            break
    bull = bool(c[-1] > o[-1] and rng > 0 and body >= p["body_min"] * rng and dcr >= p["dcr_min"])
    atr_move = bool(atr is not None and chg >= atr)
    # 베이스(조정): 오늘 전 base_days일 고점(피벗) → 그 뒤 저점, 피벗 전 120일 저점(1차 상승 시작)
    hb, lb = h[-p["base_days"] - 1:-1], l[-p["base_days"] - 1:-1]
    pi = int(np.nanargmax(hb))
    piv = float(hb[pi])
    after = lb[pi:]
    base_low = float(np.nanmin(after)) if len(after) else None
    gpi = n - 1 - len(hb) + pi
    pre = l[max(0, gpi - 120):gpi]
    s1_low = float(np.nanmin(pre)) if len(pre) else None
    base_len = len(hb) - pi
    retr = None
    if base_low is not None and s1_low is not None and piv > s1_low and base_len >= 5:
        retr = (piv - base_low) / (piv - s1_low)
    base_ok = None if retr is None else bool(retr <= p["retr_max"])
    vcp = None
    if base_len >= 15:
        seg = np.array_split(np.arange(n - 1 - base_len, n - 1), 3)
        rr = [(np.nanmax(h[s]) - np.nanmin(l[s])) / np.nanmax(h[s]) for s in seg if len(s)]
        dry = np.nanmean(v[-11:-1]) < v50 * 0.85 if v50 else False
        vcp = bool(len(rr) == 3 and rr[0] > rr[1] > rr[2] and dry)
    bottom = bool(c[-1] < hi52 * (1 - p["bottom"] / 100))
    t = {"vol": bool(vol_x is not None and vol_x >= p["vol_x"]), "bull": bull, "nh": nh_close, "sector": sector_ok,
         "base": base_ok, "vcp": vcp, "atr": atr_move, "mkt": market_ok}
    f = {"f_vol": bool(vol_x is not None and vol_x < 1.2), "f_wick": bool(rng > 0 and (dcr < p["dcr_bad"] or upper > body)),
         "f_alone": sector_ok is False, "f_bottom": bool(bottom and c[-1] > o[-1] and body >= p["body_min"] * rng)}
    n_trig = sum(1 for x in t.values() if x is True)
    n_fake = sum(1 for x in f.values() if x)
    if not (nh_touch or brk_ago is not None):
        verdict = "돌파 전"
    elif f["f_wick"] or f["f_bottom"] or n_fake >= 2:
        verdict = "가짜 돌파 의심"
    elif (t["vol"] and t["bull"] and (nh_close or brk_ago is not None) and n_fake == 0):
        verdict = "진짜 돌파"
    else:
        verdict = "애매 — 종가 확인"
    return {"vol_x": vol_x, "dcr": dcr, "chg": chg, "atr": atr, "hi52": hi52, "dist": dist, "retr": retr,
            "base_len": base_len, "pivot": piv, "brk_ago": brk_ago, "nh_touch": nh_touch, **t, **f,
            "n_trig": n_trig, "n_fake": n_fake, "verdict": verdict, "live": bool(is_live)}


def bo_reason(q: dict) -> str:
    """한 줄 근거: 트리거 n/8 + 가짜 신호."""
    if not q:
        return ""
    on = [lbl for k_, lbl in BO_TRIGGERS if q.get(k_) is True]
    bad = [lbl for k_, lbl in BO_FAKES if q.get(k_)]
    s = f"5강 트리거 {q['n_trig']}/8"
    if bad:
        s += " · ⚠️ " + "·".join(bad)
    elif on:
        s += " · " + "·".join(on[:3])
    return s


def sector_moves(frame: pd.DataFrame, up: float = 3.0) -> dict:
    """섹터별 오늘 +up% 이상 오른 종목 수(섹터 동반 판단용)."""
    if frame is None or frame.empty or "group" not in frame:
        return {}
    ch = pd.to_numeric(frame["change"], errors="coerce")
    return frame[ch >= up].groupby("group").size().to_dict()


def bo_scan(frame: pd.DataFrame, hist_of, quotes: dict, leaders: set, market_ok: bool | None,
            p: dict | None = None, codes=None) -> pd.DataFrame:
    """52주 고가 근처·돌파 종목만 5강 점검. hist_of(code) → 일봉 표."""
    p = {**BO_DEFAULTS, **(p or {})}
    today = data.now_kst().date()
    mv = sector_moves(frame)
    info = frame.drop_duplicates("code").set_index("code")
    rows = []
    for code in (codes if codes is not None else info.index):
        if code not in info.index:
            continue
        r = info.loc[code]
        hist = hist_of(code)
        if hist is None or len(hist) < 80:
            continue
        d, o, h, l, c, v = data._session_bars(hist, (quotes or {}).get(code), today)
        grp = r.get("group") or ""
        chg = (c[-1] / c[-2] - 1) * 100 if len(c) > 1 and c[-2] > 0 else 0
        mates = mv.get(grp, 0) - (1 if chg >= 3 else 0)
        sec_ok = bool(grp in leaders or mates >= 2) if grp else None
        q = bo_quality(d, o, h, l, c, v, sec_ok, market_ok, p, today)
        if q is None or q["verdict"] == "돌파 전" and q["dist"] > p["near"]:
            continue
        rows.append({"code": code, "name": r.get("name", code), "group": grp, "price": float(c[-1]),
                     "rs": r.get("rs"), "cap_krw": r.get("cap_krw"), "mates": max(mates, 0), "leading": grp in leaders,
                     "url": r.get("url") or data.quote_url(code), **q})
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    order = {k_: i for i, k_ in enumerate(BO_VERDICTS)}
    out["_o"] = out["verdict"].map(order)
    return out.sort_values(["_o", "n_trig", "vol_x"], ascending=[True, False, False]).drop(columns="_o").reset_index(drop=True)


# ─────────────────────────── 🧺 비교용: 남들 하는 엔벨로프 하단 매매(깡토 글 0단계) ───────────────────────────
# 20일선보다 20%↓ 종목 → 다음 날 시가에 1/3 · 첫 매수가 대비 -10%·-20% 더 빠지면 1/3씩 물타기 ·
# 종가가 20일선 위면 다음 날 시가에 전부 · 손절 없음 · 동시에 5종목 · 신호가 여럿이면 거래대금 큰 순 · 시장 상관없이 언제나
ENV_DEFAULTS = {"gap20": 20.0, "slots": 5, "adds": (10.0, 20.0), "min_cap": 0.0, "min_tv": 0.0, "min_px": 0.0, "min_bars": 0,
                "exit_ma": 5, "fee": 0.00015, "tax": 0.0020, "slip": 0.001}


def env_sim(df: pd.DataFrame, histories: dict, kospi: pd.DataFrame, start: date, seed: float = 1e7,
            p: dict | None = None) -> dict | None:
    """엔벨로프 하단 + 물타기 모의 계좌(같은 기간·같은 비용으로 바닥 줍기 최종 규칙과 비교)."""
    p = {**ENV_DEFAULTS, **(p or {})}
    if kospi is None or len(kospi) < 60:
        return None
    k = kospi[["date", "close"]].copy()
    k["date"] = pd.to_datetime(k["date"]).dt.normalize()
    k = k.drop_duplicates("date", keep="last").set_index("date")["close"].astype(float)
    days = [d for d in k.index if d >= pd.Timestamp(start)]
    if len(days) < 2:
        return None
    fr = _frames(df, histories, p)
    buy_cost = 1 + p["fee"] + p["slip"]
    sell_keep = 1 - p["fee"] - p["tax"] - p["slip"]
    cash, hold, pend_buy, pend_sell = float(seed), {}, [], {}
    journal, trades, curve = [], [], []
    peak_eq, mdd, prev_eq = float(seed), 0.0, float(seed)

    def row(code, d):
        h = fr[code][2]
        return h.loc[d] if d in h.index else None

    for d in days:
        for code, why in list(pend_sell.items()):
            x, r_ = hold.get(code), row(code, d)
            if x is None:
                pend_sell.pop(code)
                continue
            if r_ is None or not r_["open"] > 0:
                continue
            px = float(r_["open"])
            amt = px * x["qty"] * sell_keep
            pnl = amt - x["cost"]
            cash += amt
            trades.append({"date": d, "code": code, "name": x["name"], "side": "매도", "why": why, "price": px,
                           "qty": x["qty"], "amount": px * x["qty"], "pnl": pnl})
            x["j"].update(status="청산", exit_date=d, exit_px=px, exit_why=why, days=x["held"], pnl=pnl,
                          ret=(amt / x["cost"] - 1) * 100, buys=x["n"])
            hold.pop(code)
            pend_sell.pop(code)
        for code, why_in, step in pend_buy:
            r_ = row(code, d)
            if r_ is None or not r_["open"] > 0:
                continue
            if step == 0 and (code in hold or len(hold) >= p["slots"]):
                continue
            if step > 0 and code not in hold:
                continue
            px = float(r_["open"])
            unit = (prev_eq / p["slots"] / 3) if step == 0 else hold[code]["unit"]
            qty = int(min(unit, cash) // (px * buy_cost))
            if qty <= 0:
                continue
            cost = px * qty * buy_cost
            cash -= cost
            if step == 0:
                name, grp = fr[code][0], fr[code][1]
                j = {"status": "보유", "code": code, "name": name, "group": grp, "entry_date": d, "entry_px": px,
                     "why": why_in, "buys": 1}
                journal.append(j)
                hold[code] = {"name": name, "qty": qty, "cost": cost, "held": 0, "last": px, "j": j, "first": px,
                              "unit": unit, "n": 1}
            else:
                x = hold[code]
                x["qty"] += qty
                x["cost"] += cost
                x["n"] += 1
                x["j"]["buys"] = x["n"]
            x = hold[code]
            x["j"].update(qty=x["qty"], avg=x["cost"] / x["qty"])
            trades.append({"date": d, "code": code, "name": x["name"], "side": "매수",
                           "why": why_in if step == 0 else f"물타기 {x['n']}차 — {why_in}", "price": px, "qty": qty,
                           "amount": px * qty, "pnl": None})
        pend_buy = []
        for code, x in hold.items():
            x["held"] += 1
            r_ = row(code, d)
            if r_ is None or code in pend_sell:
                continue
            if r_["close"] >= r_["ma20"]:
                pend_sell[code] = f"종가가 20일선 회복({r_['close']:,.0f} ≥ {r_['ma20']:,.0f}) → 다음 날 시가"
            elif x["n"] < 3:
                lvl = p["adds"][x["n"] - 1]
                if r_["close"] <= x["first"] * (1 - lvl / 100):
                    pend_buy.append((code, f"첫 매수가 대비 -{lvl:.0f}%↓", x["n"]))
        free = p["slots"] - sum(1 for c_ in hold if c_ not in pend_sell)
        if free > 0:
            cands = []
            for code, (_, _, h) in fr.items():
                if code in hold or d not in h.index:
                    continue
                r_ = h.loc[d]
                if r_["ma20"] > 0 and (r_["close"] / r_["ma20"] - 1) * 100 <= -p["gap20"]:
                    cands.append((float(np.nan_to_num(r_["tv20"])), code, (r_["close"] / r_["ma20"] - 1) * 100))
            cands.sort(reverse=True)
            pend_buy += [(c_, f"20일선 {g:+.1f}% · 1/3 매수", 0) for _, c_, g in cands[:free]]
        eq = cash
        for code, x in hold.items():
            r_ = row(code, d)
            if r_ is not None and r_["close"] == r_["close"]:
                x["last"] = float(r_["close"])
            eq += x["last"] * x["qty"]
        prev_eq = eq
        peak_eq = max(peak_eq, eq)
        mdd = min(mdd, (eq / peak_eq - 1) * 100)
        curve.append({"date": d, "equity": eq})
    for code, x in hold.items():
        x["j"].update(cur_px=x["last"], days=x["held"], pnl=x["last"] * x["qty"] * sell_keep - x["cost"],
                      ret=(x["last"] * x["qty"] * sell_keep / x["cost"] - 1) * 100,
                      exit_why=pend_sell.get(code) or f"보유 중 · {x['n']}번 매수")
    closed = [j for j in journal if j["status"] == "청산"]
    eq = curve[-1]["equity"]
    by_n = {}
    for j in closed:
        b = by_n.setdefault(j.get("buys", 1), [0, 0])
        b[0] += 1
        b[1] += j["pnl"] > 0
    return {"seed": seed, "start": days[0].date(), "end": days[-1].date(), "equity": eq, "pnl": eq - seed,
            "ret": (eq / seed - 1) * 100, "mdd": mdd, "closed": len(closed), "wins": sum(1 for j in closed if j["pnl"] > 0),
            "cash": cash, "journal": journal[::-1], "trades": trades[::-1], "curve": pd.DataFrame(curve),
            "universe": len(fr), "by_buys": by_n}
