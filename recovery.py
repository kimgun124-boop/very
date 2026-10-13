"""🌱 회복 리더: 지수가 빠졌다가 다시 오를 때, 어디가 먼저 싹트는지.

생각의 틀
  - 시장이 무너질 땐 다 같이 빠지지만, 회복할 땐 같은 속도로 오르지 않아요.
  - 그래서 '지수 고점 → 저점(하락 구간)'에서 얼마나 덜 빠졌는지와
    '저점 → 지금(회복 구간)'에서 지수보다 얼마나 빨리 오르는지를 섹터·종목별로 같은 날짜 기준으로 비교해요.
  - 여기에 저점 이후 외국인·기관이 사는지(수급), 오르는 종목이 넓게 퍼졌는지(확산), 신고가가 나오는지를 더해요.

화면(app.py의 render_recovery)과 분리해서 계산만 담당해요.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

LOOKBACKS = {"20거래일": 20, "60거래일": 60, "120거래일": 120}
# 새싹 점수 비중(섹터끼리 백분위를 가중 평균). 비중은 정한 값이라 바꿔도 돼요.
SECTOR_W = {"excess": 0.40, "flow": 0.25, "vs_hi": 0.20, "breadth": 0.15}
STOCK_W = {"excess": 0.40, "flow": 0.30, "high": 0.30}


def _series(h: pd.DataFrame | None) -> pd.Series:
    if h is None or h.empty:
        return pd.Series(dtype=float)
    s = pd.Series(pd.to_numeric(h["close"], errors="coerce").to_numpy(dtype=float),
                  index=pd.to_datetime(h["date"]).dt.normalize())
    return s[~s.index.duplicated(keep="last")].dropna().sort_index()


def _asof(s: pd.Series, d) -> float | None:
    s = s.dropna()
    if s.empty:
        return None
    v = s.loc[:d]
    return float(v.iloc[-1]) if len(v) else None


# ─────────────────────────── ① 시장 국면 ───────────────────────────
PHASES = {
    "calm": ("🌤️ 큰 하락 없음", "#51616C", "지수 낙폭이 작아요. 그래도 같은 저점 이후 누가 앞서는지는 계속 볼 수 있어요."),
    "falling": ("🌊 물살 빠지는 중", "#C0262E", "버티기보다 물 밖에서 지켜볼 때예요. 이 와중에 덜 빠지는 섹터를 표에서 기록해 두세요."),
    "base": ("🪨 바닥 다지기", "#C27C0E", "새 저점은 멈췄지만 반등은 아직 약해요. 누가 먼저 저점을 높이고 수급이 들어오는지 보세요."),
    "sprout": ("🌱 새싹 돋는 중", "#1B8A4B", "지수가 20일선을 되찾았어요. 아래 표 위쪽이 가장 먼저 싹트는 곳이에요."),
    "pause": ("🌬️ 회복 중 숨 고르기", "#A87400", "반등하던 지수가 20일선 아래로 쉬고 있어요. 이때 덜 밀리고 수급이 유지되는 섹터가 다음 주도 후보예요. 최근 하락만 보려면 '20거래일'로 바꿔 보세요."),
    "recover": ("🌳 회복 진행", "#1B6FD6", "하락분의 절반 이상을 되찾았어요. 먼저 오른 섹터가 계속 앞서는지(주도 압축) 확인하세요."),
}


def market_phase(idx: pd.DataFrame | None, lookback: int = 60) -> dict | None:
    """지수 일봉 → 최근 lookback 거래일 저점, 그 전 고점, 낙폭·반등·회복률·이평선·국면."""
    c = _series(idx)
    if len(c) < 25:
        return None
    n = len(c)
    lo_pos = n - min(lookback, n) + int(np.argmin(c.iloc[-min(lookback, n):].to_numpy()))
    hi_from = max(0, lo_pos - max(lookback, 20))
    hi_pos = hi_from + int(np.argmax(c.iloc[hi_from:lo_pos + 1].to_numpy()))
    hi, lo, last = float(c.iloc[hi_pos]), float(c.iloc[lo_pos]), float(c.iloc[-1])
    ma20 = float(c.tail(20).mean())
    ma60 = float(c.tail(60).mean()) if n >= 60 else None
    ma20_prev = float(c.iloc[-25:-5].mean()) if n >= 25 else None
    dd = (lo / hi - 1) * 100
    reb = (last / lo - 1) * 100
    recov = (last - lo) / (hi - lo) * 100 if hi > lo else None
    since = n - 1 - lo_pos
    if dd > -5:
        key = "calm"
    elif since <= 2 and last < ma20:
        key = "falling"
    elif reb < 3 or (last < ma20 and (recov is None or recov < 30)):
        key = "base"
    elif last < ma20:
        key = "pause"
    elif recov is not None and recov >= 50 and (ma60 is None or last > ma60):
        key = "recover"
    else:
        key = "sprout"
    return {"key": key, "label": PHASES[key][0], "color": PHASES[key][1], "advice": PHASES[key][2],
            "high_date": c.index[hi_pos], "low_date": c.index[lo_pos], "high": hi, "low": lo, "last": last,
            "dd": dd, "reb": reb, "recov": recov, "since": since, "now_vs_high": (last / hi - 1) * 100,
            "above20": last > ma20, "above60": (last > ma60) if ma60 else None,
            "ma20_up": (ma20 > ma20_prev) if ma20_prev else None, "series": c}


def index_ret(idx: pd.DataFrame | None, d0, d1=None) -> float | None:
    c = _series(idx)
    a, b = _asof(c, d0), (_asof(c, d1) if d1 is not None else (float(c.iloc[-1]) if len(c) else None))
    return (b / a - 1) * 100 if a and b else None


# ─────────────────────────── ② 시장 수급 ───────────────────────────
def _streak(vals) -> int:
    n, sign = 0, None
    for v in reversed(list(vals)):
        if v is None or pd.isna(v) or v == 0:
            break
        s = v > 0
        if sign is None:
            sign = s
        elif s != sign:
            break
        n += 1
    return (n if sign else -n) if n else 0


def market_flow(fl: pd.DataFrame | None, low_date=None) -> dict | None:
    """시장 일별 투자자 순매수(억) → 5일 합, 그 전 5일 합, 저점 이후 합, 연속일수, 외국인 복귀 신호."""
    if fl is None or fl.empty:
        return None
    d = fl.copy()
    d["date"] = pd.to_datetime(d["date"]).dt.normalize()
    d = d.sort_values("date")
    out = {"daily": d}
    for k in ("외국인", "기관"):
        s = pd.to_numeric(d[k], errors="coerce")
        out[f"{k}5"] = float(s.tail(5).sum())
        out[f"{k}5_prev"] = float(s.iloc[-10:-5].sum()) if len(s) >= 10 else None
        out[f"{k}_streak"] = _streak(s.tolist())
        if low_date is not None:
            after = s[d["date"] > low_date]
            out[f"{k}_since"] = float(after.sum()) if len(after) else 0.0
            out[f"{k}_since_days"] = int(len(after))
            out["since_partial"] = bool(d["date"].iloc[0] > low_date)
    f5, fp = out["외국인5"], out.get("외국인5_prev")
    out["foreign_back"] = bool(out["외국인_streak"] >= 3 or (f5 > 0 and fp is not None and fp < 0))
    out["inst_back"] = bool(out["기관_streak"] >= 3 or (out["기관5"] > 0 and (out.get("기관5_prev") or 0) < 0))
    return out


# ─────────────────────────── ③ 종목·섹터 ───────────────────────────
def _flow_since(t: pd.DataFrame | None, low_date) -> tuple[float | None, int, int]:
    """저점 다음 날부터 외국인+기관 순매수 합(억), 외국인·기관 연속 순매수 일수."""
    if t is None or t.empty:
        return None, 0, 0
    d = pd.to_datetime(t["date"]).dt.normalize()
    m = d > low_date
    f = pd.to_numeric(t["외국인금액"], errors="coerce")
    i = pd.to_numeric(t["기관금액"], errors="coerce")
    val = float((f[m].fillna(0) + i[m].fillna(0)).sum()) if m.any() else 0.0
    return val, _streak(t["외국인"].tolist()), _streak(t["기관"].tolist())


def stock_recovery(df: pd.DataFrame, closes: pd.DataFrame, trends: dict | None, high_date, low_date,
                   idx_reb: float | None) -> pd.DataFrame:
    """종목별 하락 구간 등락, 저점 이후 등락, 지수 대비 초과, 회복률, 20일선 위, 저점 이후 신고가, 수급."""
    if closes is None or closes.empty:
        return pd.DataFrame()
    cl = closes.sort_index()
    before = cl.loc[:low_date]
    if before.empty:
        return pd.DataFrame()
    p_hi = cl.loc[:high_date].iloc[-1] if len(cl.loc[:high_date]) else cl.iloc[0]
    p_lo = before.iloc[-1]
    last = cl.ffill().iloc[-1]
    fall = (p_lo / p_hi - 1) * 100
    reb = (last / p_lo - 1) * 100
    vs_hi = (last / p_hi - 1) * 100            # 하락 전 고점 날짜 가격 대비 지금(0 이상이면 하락분 다 회복)
    above20 = last > cl.tail(20).mean()
    # 저점 이후 한 번이라도 직전 250거래일 최고 종가를 넘었는지(먼저 고개 드는 신호)
    prior_max = cl.rolling(250, min_periods=120).max().shift(1)
    after = cl.index > low_date
    nh = ((cl[after] >= prior_max[after]) & prior_max[after].notna()).any() if after.any() else pd.Series(False, index=cl.columns)
    base = df.set_index("code")
    rows = []
    for code in cl.columns:
        if code not in base.index:
            continue
        r = base.loc[code]
        if isinstance(r, pd.DataFrame):
            r = r.iloc[0]
        fi, sf, si = _flow_since((trends or {}).get(code), low_date)
        cap = pd.to_numeric(r.get("cap_krw"), errors="coerce")
        rows.append({
            "code": code, "name": r.get("name"), "group": r.get("group"), "sector": r.get("sector"),
            "cap": cap, "price": r.get("price"), "change": r.get("change"), "to_high": r.get("to_high"),
            "fall": fall.get(code), "reb": reb.get(code),
            "excess": (reb.get(code) - idx_reb) if idx_reb is not None and pd.notna(reb.get(code)) else np.nan,
            "vs_hi": vs_hi.get(code), "above20": bool(above20.get(code, False)), "nh": bool(nh.get(code, False)),
            "fi": fi, "fi_cap": (fi * 1e8 / cap * 1e4) if fi is not None and cap and cap > 0 else np.nan,   # 시총 대비 bp
            "streak_f": sf, "streak_i": si,
        })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out = out[out["reb"].notna() & out["fall"].notna()].copy()
    pr = lambda s: s.rank(pct=True) * 100  # noqa: E731
    hi_score = np.where(out["nh"], 100.0, pr(-pd.to_numeric(out["to_high"], errors="coerce")).fillna(50))
    out["score"] = (STOCK_W["excess"] * pr(out["excess"]).fillna(50) + STOCK_W["flow"] * pr(out["fi_cap"]).fillna(50)
                    + STOCK_W["high"] * hi_score)
    return out


def sector_recovery(st_df: pd.DataFrame, by: str = "group", min_n: int = 2, idx_fall: float | None = None) -> pd.DataFrame:
    """종목 결과를 섹터로 묶어요. 수익률은 시총가중(시총 모르면 중앙값으로 채움)."""
    if st_df is None or st_df.empty:
        return pd.DataFrame()
    rows = []
    for g, s in st_df.groupby(by):
        if not g or len(s) < min_n:
            continue
        w = s["cap"].where(s["cap"] > 0)
        w = w.fillna(w.median()) if w.notna().any() else pd.Series(1.0, index=s.index)
        wa = lambda col: float((s[col] * w).sum() / w[s[col].notna()].sum()) if s[col].notna().any() else np.nan  # noqa: E731
        fall, reb = wa("fall"), wa("reb")
        N = (1 + fall / 100) * (1 + reb / 100)
        fi_known = s["fi"].notna()
        fi_sum = float(s.loc[fi_known, "fi"].sum()) if fi_known.any() else np.nan
        cap_known = s.loc[fi_known, "cap"].sum()
        top = s.sort_values("reb", ascending=False).head(3)
        rows.append({
            "sector": g, "n": len(s), "fall": fall, "reb": reb, "excess": wa("excess"),
            "vs_hi": (N - 1) * 100,
            "breadth": float((s["reb"] > 0).mean() * 100), "above20": float(s["above20"].mean() * 100),
            "n_nh": int(s["nh"].sum()), "fi": fi_sum, "fi_n": int(fi_known.sum()),
            "fi_cap": (fi_sum * 1e8 / cap_known * 1e4) if fi_known.any() and cap_known > 0 else np.nan,
            "n_buy": int((s["fi"] > 0).sum()),
            "top": " · ".join(f"{a} {b:+.0f}%" for a, b in zip(top["name"], top["reb"])),
        })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    pr = lambda c: out[c].rank(pct=True) * 100  # noqa: E731
    out["score"] = (SECTOR_W["excess"] * pr("excess").fillna(50) + SECTOR_W["flow"] * pr("fi_cap").fillna(50)
                    + SECTOR_W["vs_hi"] * pr("vs_hi").fillna(50) + SECTOR_W["breadth"] * pr("breadth").fillna(50))
    ref = idx_fall if idx_fall is not None else out["fall"].median()     # 지수보다 덜 빠졌으면 '버팀'
    out["kind"] = np.select(
        [(out["excess"] > 0) & (out["fall"] >= ref),
         (out["excess"] > 0),
         (out["fall"] >= ref)],
        ["🌱 덜 빠지고 먼저 오름", "🚀 많이 빠졌지만 빨리 회복", "🛡️ 버텼지만 회복은 느림"], "🥀 많이 빠지고 회복도 느림")
    return out.sort_values("score", ascending=False).reset_index(drop=True)
