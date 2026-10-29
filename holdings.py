"""💼 내 보유 종목: 평단가 저장 · 내 매매 원칙으로 판단 · 뉴스/리포트 근거 모으기.

판단은 효석 매매 원칙을 그대로 옮긴 규칙이에요(매수·매도 추천이 아니라 원칙 점검표).
- 1R 손절 = 평단 -8% (ATR이 8% 이상이면 ATR까지), 2R 이상 갔던 종목은 손절선을 본전으로, 4R이면 +1R, 5R이면 +2R …
- 3R(+24%) 도달 → 절반 익절, 나머지는 5일선이 50일선에 닿으면 정리
- 막 돌파한 종목은 5일선, 추세 종목은 20일선, 느린 종목은 50일선으로 추세 이탈을 봄
- 클라이맥스(신고가 대량 윗꼬리, 고점 -10% 대량 음봉)는 비중 축소 신호
- 시장(코스피·코스닥 60일선), 주도섹터, RS(70↑·지수 RS 위), 돌파 유지/이탈, 수급(거래량)
"""
from __future__ import annotations

import html as _html
import json
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import numpy as np
import pandas as pd
import requests

HOLDINGS_VERSION = "2026-10-29-zb"
HOLDINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_holdings.json")
KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0",
      "Referer": "https://m.stock.naver.com/"}
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


def now_kst() -> datetime:
    return datetime.now(KST)


# ─────────────────────────── 저장 ───────────────────────────
def load() -> dict:
    try:
        with open(HOLDINGS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and isinstance(d.get("holdings"), list) else {"holdings": []}
    except (OSError, ValueError):
        return {"holdings": []}


def save_local(store: dict) -> None:
    with open(HOLDINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=1)


def new_holding(code: str, name: str, avg: float, qty: float, buy_date: str, stop_pct: float | None,
                half_taken: bool, memo: str, account: str = "추세") -> dict:
    return {"id": uuid.uuid4().hex[:10], "code": code, "name": name, "avg": float(avg), "qty": float(qty or 0),
            "buy_date": buy_date, "stop_pct": stop_pct, "half_taken": bool(half_taken), "memo": memo,
            "account": account if account in ("추세", "가치") else "추세",
            "added_at": now_kst().strftime("%Y-%m-%d %H:%M")}


# ─────────────────────────── 자본관리(계좌 BEP) ───────────────────────────
# 스승님 원칙: ① 계좌 원금의 5%가 훼손되지 않게(최대 10%) ② 계좌가 커지면 BEP도 단계적으로 올리고
# ③ 계좌가 BEP에 닿으면 전액 현금화하고 시장을 다시 관찰한다.
def account_bep(principal: float, peak: float, step: float = 0.10, base_loss: float = 0.05) -> dict:
    """원금·최고 평가액으로 지금 지켜야 할 BEP(멈춤선)를 계산해요.
    최고 평가액이 원금보다 10% 오를 때마다 BEP를 한 칸씩 올려요(+10% → 본전, +20% → 원금+10% …)."""
    if not principal or principal <= 0:
        return {}
    peak = max(peak or principal, principal)
    k = int(((peak / principal) - 1) // step)
    bep = principal * (1 - base_loss) if k <= 0 else principal * (1 + step * (k - 1))
    return {"bep": bep, "hard": principal * 0.90, "level": k, "peak": peak}


def value_estimate(net_cash: float | None, op: float | None, opm: float | None) -> float | None:
    """스승님 절대가치: 순현금 + 영업이익 × (영업이익률의 절반) 배.
    예) 순현금 1,000억 + 영업이익 100억 × 5배(영업이익률 10%의 절반) = 1,500억. 단위는 넣은 그대로(억원)."""
    if op is None or opm is None:
        return None
    return float(net_cash or 0) + float(op) * max(float(opm), 0) / 2


# ─────────────────────────── 비밀번호(보유 종목 잠금) ───────────────────────────
# 비밀번호·복구 답·복구 코드는 그대로 저장하지 않고 소금(salt)을 친 해시로만 저장해요. 그래서 '보여주기'는 못 하고 '다시 정하기'로 찾아요.
import hashlib
import hmac
import secrets as _secrets

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_settings.json")
PW_ITER = 200_000
RECOVERY_QUESTIONS = ["처음 담임했던 학교 이름은?", "어릴 때 살던 동네 이름은?", "가장 좋아하는 책 제목은?",
                      "처음 산 주식 종목은?", "기억에 남는 선생님 성함은?", "직접 질문 쓰기"]


def load_settings() -> dict:
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings_local(d: dict) -> None:
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


def _hash(text: str, salt: str | None = None) -> dict:
    salt = salt or _secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", text.encode("utf-8"), bytes.fromhex(salt), PW_ITER).hex()
    return {"salt": salt, "hash": h, "iter": PW_ITER}


def _check(text: str, rec: dict | None) -> bool:
    if not rec or not text:
        return False
    h = hashlib.pbkdf2_hmac("sha256", text.encode("utf-8"), bytes.fromhex(rec["salt"]), int(rec.get("iter", PW_ITER))).hex()
    return hmac.compare_digest(h, rec["hash"])


def _norm_answer(a: str) -> str:
    return re.sub(r"\s+", "", (a or "")).lower()


def new_recovery_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # 헷갈리는 0·O·1·I 빼기
    raw = "".join(_secrets.choice(alphabet) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def has_password(st_: dict) -> bool:
    return bool((st_.get("lock") or {}).get("pw"))


def set_password(st_: dict, pw: str, hint: str | None = None, question: str | None = None,
                 answer: str | None = None, new_code: bool = False) -> str | None:
    """비밀번호를 정하거나 바꿔요. new_code=True면 새 복구 코드를 만들어 돌려줘요(한 번만 보여 줌)."""
    lock = dict(st_.get("lock") or {})
    lock["pw"] = _hash(pw)
    lock["changed_at"] = now_kst().strftime("%Y-%m-%d %H:%M")
    if hint is not None:
        lock["hint"] = hint.strip()
    if question is not None and answer:
        lock["question"] = question.strip()
        lock["answer"] = _hash(_norm_answer(answer))
    code = None
    if new_code or not lock.get("code"):
        code = new_recovery_code()
        lock["code"] = _hash(code.replace("-", "").upper())
    st_["lock"] = lock
    return code


def check_password(st_: dict, pw: str) -> bool:
    return _check(pw, (st_.get("lock") or {}).get("pw"))


def check_answer(st_: dict, answer: str) -> bool:
    return _check(_norm_answer(answer), (st_.get("lock") or {}).get("answer"))


def check_code(st_: dict, code: str) -> bool:
    return _check((code or "").replace("-", "").replace(" ", "").upper(), (st_.get("lock") or {}).get("code"))


# ─────────────────────────── 원칙 판단 ───────────────────────────
def _f(v):
    try:
        v = float(v)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def judge(h: dict, m: dict, bars: pd.DataFrame | None, ctx: dict) -> dict:
    """보유 종목 하나를 내 원칙으로 점검해요.
    h: 보유 정보(avg, buy_date, stop_pct, half_taken) / m: 지표(price, atr_pct, bo_*, ma5·20·50, rs …)
    bars: 일봉(오늘 봉 포함) / ctx: market_ok, market_text, leaders, idx_rs, sector_x(분류별 평소 대비 거래대금)
    """
    if h.get("account") == "가치":
        return judge_value(h, m, bars, ctx)
    price, avg = _f(m.get("price")), _f(h.get("avg"))
    out = {"verdict": "판단 불가", "level": "gray", "pnl": None, "r": None, "stop_pct": None, "stop_price": None,
           "stop_dist": None, "target3r": None, "max_r": None, "checks": [], "actions": []}
    if not price or not avg:
        return out
    atr = _f(m.get("atr_pct"))
    base_stop = _f(h.get("stop_pct")) or max(8.0, atr or 0.0)
    pnl = (price / avg - 1) * 100
    # 깡토: 수익 쿠션(1R 이상 수익)이 있으면 추세가 꺾이기 전까지 홀딩, 늦게 산 후발은 타이트하게
    cushion = pnl >= base_stop
    vol_mode = bool(ctx.get("vol_mode"))
    tight = vol_mode and not cushion and not h.get("stop_pct")
    stop_pct = min(base_stop, 6.0) if tight else base_stop   # 변동성 장세 후발: -5~6% (손익비 1:3 유지 → 목표 +18%)
    r_now = pnl / stop_pct
    # 매수일 이후 최고가 → 2R·3R 도달 이력
    max_r, hi_since = None, None
    if bars is not None and not bars.empty:
        b = bars
        if h.get("buy_date"):
            try:
                b = bars[pd.to_datetime(bars["date"]) >= pd.Timestamp(h["buy_date"])]
            except (ValueError, TypeError):
                pass
        if len(b):
            hi_since = float(pd.to_numeric(b["high"], errors="coerce").max())
            max_r = ((hi_since / avg - 1) * 100) / stop_pct
    # R 사다리(트레일링 스톱): 1R 간격 → 1R에 본전, 2R에 +1R … / 2R 간격 → 2R에 본전, 4R에 +2R … / 끄기
    ladder = ctx.get("ladder") or "원칙"
    step = {"1R": 1, "2R": 2}.get(ladder)
    lock_r = None                      # 손절선을 올려 둔 R(0 = 본전)
    if ladder == "원칙" and max_r is not None:
        # 효석 원칙: +2R → 본전, +3R → 본전 유지, +4R → +1R, +5R → +2R … (최고 R − 3). 계좌 관리 앱(core.trail_lock_r)과 같은 식
        lvl = int(np.floor(max_r + 1e-9))
        lock_r = lvl - 3 if lvl >= 4 else (0 if lvl >= 2 else None)
    elif step and max_r is not None and max_r >= step:
        lock_r = (int(max_r // step) - 1) * step
    if h.get("half_taken"):
        lock_r = max(lock_r or 0, 0)
    breakeven = lock_r is not None
    stop_price = avg * (1 + lock_r * stop_pct / 100) if breakeven else avg * (1 - stop_pct / 100)
    stop_label = ("본전" if lock_r == 0 else f"+{lock_r}R") if breakeven else f"-{stop_pct:.0f}%"
    target3r = avg * (1 + 3 * stop_pct / 100)
    out.update(pnl=pnl, r=r_now, stop_pct=stop_pct, stop_price=stop_price, stop_dist=(price / stop_price - 1) * 100,
               target3r=target3r, max_r=max_r, cushion=cushion, tight=tight, ladder=ladder, lock_r=lock_r)

    checks = []   # (상태 good/warn/bad/info, 항목, 설명)

    def add(state, item, text):
        checks.append((state, item, text))

    # 1) 손절
    if price <= stop_price:
        add("bad", "손절선", f"현재가가 손절선 {stop_price:,.0f} 아래 — "
            + (f"사다리 손절({stop_label}, 매수 후 최고 {max_r:.1f}R)" if breakeven and max_r is not None
               else ("본전 손절(절반 익절 종목)" if breakeven else f"1R({stop_label})")) + " 도달")
    else:
        add("good" if out["stop_dist"] > 3 else "warn", "손절선",
            f"{stop_label} {stop_price:,.0f} · 여유 {out['stop_dist']:.1f}%"
            + (f" (🪜 {ladder} 사다리로 올린 손절선)" if lock_r and lock_r > 0 else "")
            + (" (변동성 장세 후발 종목이라 -6%로 좁힘)" if tight else "")
            + (" (ATR이 8%보다 커서 ATR로 넓힘)" if not tight and not h.get("stop_pct") and atr and atr > 8 else ""))
    if vol_mode:
        add("good" if cushion else "warn", "쿠션" if cushion else "후발",
            f"수익 +{pnl:.1f}% — 쿠션 있음: 추세가 무너지기 전까지 홀딩" if cushion
            else f"수익 {pnl:+.1f}% — 후발 종목: 변동성 장세라 손절 -{stop_pct:.0f}% · 목표 +{3 * stop_pct:.0f}%로 타이트하게")
    # 2) 익절(3R) · 끌고 가기
    ma5, ma50, ma20 = _f(m.get("ma5")), _f(m.get("ma50")), _f(m.get("ma20"))
    if r_now >= 3 and not h.get("half_taken"):
        add("info", "3R 익절", f"+{pnl:.1f}% = {r_now:.1f}R — 원칙상 절반 익절 구간(목표 {target3r:,.0f})")
    elif h.get("half_taken"):
        add("info", "3R 익절", "절반 익절 완료 — 나머지는 5일선이 50일선에 닿을 때까지 끌고 가기")
    else:
        add("info", "3R 목표", f"{target3r:,.0f} (지금 {r_now:+.1f}R · 매수 후 최고 {max_r:+.1f}R)" if max_r is not None
            else f"{target3r:,.0f} (지금 {r_now:+.1f}R)")
    if ma5 and ma50:
        gap = (ma5 / ma50 - 1) * 100
        if gap <= 0.5:
            add("bad" if h.get("half_taken") or r_now > 0 else "warn", "5일선·50일선",
                f"5일선이 50일선에 닿음({gap:+.1f}%) — 원칙상 나머지 정리 신호")
        else:
            add("good", "5일선·50일선", f"5일선이 50일선보다 {gap:.1f}% 위")
    # 3) 돌파 유지 / 이탈
    st_ = m.get("bo_status")
    lvl, days = _f(m.get("bo_level")), _f(m.get("bo_days"))
    if st_ == "유지":
        add("good", "돌파 유지", f"직전 52주 최고 {lvl:,.0f} 위에서 {int(days or 0)}일째 마감" if lvl else "돌파 유지 중")
    elif st_ == "이탈":
        add("warn", "돌파 이탈", f"돌파선 {lvl:,.0f} 아래로 마감 {int(days or 0)}일째 — 되시험인지 실패인지 거래량으로 확인" if lvl
            else "돌파선 아래로 마감")
    # 4) 이동평균(추세 속도에 맞춰)
    fast = st_ == "유지" and (days or 99) <= 15
    ma_key, ma_v = ("5일선", ma5) if fast else ("20일선", ma20)
    if ma_v:
        d = (price / ma_v - 1) * 100
        add("good" if d >= 0 else "warn", ma_key, f"{ma_key} {ma_v:,.0f} 대비 {d:+.1f}%"
            + (" (막 돌파한 종목이라 5일선 기준)" if fast else " (추세 종목 20일선 기준)"))
    # 5) 클라이맥스 · 대량 음봉 · 거래량
    if bars is not None and len(bars) >= 55:
        b = bars.tail(60).reset_index(drop=True)
        o, hi, lo, c, v = (pd.to_numeric(b[k], errors="coerce").to_numpy(dtype=float)
                           for k in ("open", "high", "low", "close", "volume"))
        vavg = np.nanmean(v[-51:-1]) if len(v) > 51 else np.nanmean(v[:-1])
        prev = c[-2]
        chg = (c[-1] / prev - 1) * 100 if prev else 0
        vr = v[-1] / vavg if vavg else None
        rng = hi[-1] - lo[-1]
        pos = (c[-1] - lo[-1]) / rng if rng > 0 else 0.5
        hi52 = _f(m.get("high52"))
        if chg <= -10 and vr and vr >= 1.5:
            add("bad", "대량 음봉", f"오늘 {chg:.1f}% · 거래량 {vr:.1f}배 — 원칙상 비중 축소 신호")
        elif hi52 and hi[-1] >= hi52 * 0.995 and pos <= 0.4 and vr and vr >= 2:
            add("bad", "클라이맥스", f"신고가에서 긴 윗꼬리 + 거래량 {vr:.1f}배 — 클라이맥스 의심(비중 축소 신호)")
        elif chg < 0 and vr:
            add("good" if vr < 1 else "warn", "하락 거래량",
                f"오늘 {chg:.1f}% · 거래량 평소의 {vr:.1f}배 — " + ("거래 줄며 눌림(정상 되시험 쪽)" if vr < 1
                                                              else "거래 늘며 하락(물량 나오는 쪽)"))
        elif vr:
            add("info", "거래량", f"오늘 {chg:+.1f}% · 거래량 평소의 {vr:.1f}배")
    # 6) RS · 시장 · 섹터
    rs = _f(m.get("rs"))
    idx = {k: v for k, v in (ctx.get("idx_rs") or {}).items() if v is not None}
    if rs is not None:
        beat = all(rs > v for v in idx.values()) if idx else None
        add("good" if rs >= 70 and beat is not False else "warn", "RS",
            f"RS {rs:.0f}" + (f" (지수 {' · '.join(f'{k} {v:.0f}' for k, v in idx.items())})" if idx else ""))
    mk = ctx.get("market_ok")
    if mk is not None:
        add("good" if mk else "warn", "시장", ctx.get("market_text", ""))
    grp = m.get("group")
    if grp:
        lead = grp in (ctx.get("leaders") or set())
        sx = (ctx.get("sector_x") or {}).get(grp)
        txt = f"{grp} — " + ("주도섹터" if lead else "주도섹터 아님")
        if sx is not None:
            txt += f" · 섹터 거래대금 평소의 {sx:.1f}배"
        add("good" if lead else "info", "섹터", txt)
        lr, ln = _f(m.get("lead_rank")), _f(m.get("lead_n"))
        if lr:
            add("good" if lr == 1 and m.get("lead_ok") else "info", "섹터 순위",
                ("👑 대장주" if lr == 1 and m.get("lead_ok") else f"섹터 {int(lr)}위") + (f"/{int(ln)}" if ln else ""))
    # 스승님·깡토 원칙 보태기
    if st_ == "이탈" and ma_v and price >= ma_v:
        add("info", "반등 ≠ 돌파", "이탈 뒤 오른 건 되돌림일 수 있어요 — 다시 돌파(신고가·장대양봉·거래량)가 나와야 새 진입 조건")
    if 0 < r_now < 3 and not h.get("half_taken"):
        add("info", "손익비", f"지금 {r_now:.1f}R — 승률 30%면 2.33R가 손익분기, 3R부터 '출발선'이라 작은 수익에 먼저 팔지 않는 게 원칙")
    out["checks"] = checks

    bad = [c for c in checks if c[0] == "bad"]
    warn = [c for c in checks if c[0] == "warn"]
    if any(c[1] == "손절선" for c in bad):
        out.update(verdict="🛑 손절 조건 도달", level="red")
        out["actions"] = [f"원칙: 손절선 {stop_price:,.0f} 이탈 — 정리 검토", "예외를 두지 않는 게 원칙(리스크관리 > 멘탈관리)"]
    elif r_now >= 3 and not h.get("half_taken"):
        out.update(verdict="💰 3R 익절 구간", level="blue")
        out["actions"] = ["원칙: 절반 익절 후 손절선을 본전 이상으로(사다리 설정 따라)", "나머지는 5일선이 50일선에 닿을 때까지"]
    elif any(c[1] == "5일선·50일선" for c in bad):
        out.update(verdict="📉 끌고 가기 종료 신호", level="red")
        out["actions"] = ["원칙: 5일선이 50일선에 닿음 — 나머지 정리 검토"]
    elif bad:
        out.update(verdict="⚠️ 비중 축소 신호", level="orange")
        out["actions"] = [f"{c[1]}: {c[2]}" for c in bad] + [f"손절선 {stop_price:,.0f}은 그대로 유지"]
    elif len(warn) >= 2:
        out.update(verdict="👀 주의 — 대응 준비", level="yellow")
        out["actions"] = [f"손절선 {stop_price:,.0f}까지 {out['stop_dist']:.1f}% 남음 — 원칙상 아직 보유 구간",
                          "종가에 거래량·섹터 동반 여부 확인",
                          "선택지: 원칙대로 보유 / 종가 이탈+대량이면 절반 축소 / 새 규칙이면 앞으로도 똑같이 적용"]
    else:
        out.update(verdict="✅ 원칙상 보유", level="green")
        out["actions"] = [f"손절선 {stop_price:,.0f} · 3R 목표 {target3r:,.0f}"]
    _deepen(out, h, m, bars, ctx, stop_pct, pnl, stop_price, target3r, ma_key, ma_v)
    return out


def _deepen(out, h, m, bars, ctx, stop_pct, pnl, stop_price, target3r, ma_key, ma_v):
    """추세 계좌: 펀더멘털·수급·거래량 분석, 불타기 판정, 점수, 상황별 대응 시나리오를 더해요."""
    fund = analyze_fundamental(m.get("_fins"))
    flow = analyze_flow(m.get("_flow"), bars)
    vol = analyze_volume(bars, m)
    pyr = pyramid_plan(h, m, vol, fund, flow, ctx, stop_pct, pnl)
    trend_names = ("손절선", "5일선·50일선", "돌파 유지", "돌파 이탈", "5일선", "20일선", "RS")
    sections = {"펀더멘털": fund["state"], "수급": flow["state"], "거래량": vol["state"],
                "추세": _state_of(out["checks"], trend_names),
                "섹터·시장": _state_of(out["checks"], ("시장", "섹터", "섹터 순위"))}
    sc, grade = score_card(sections)
    masters = masters_view(h, m, bars, ctx, m.get("_flow"), stop_pct, out.get("max_r"))
    pyr["schedule"] = pyramid_schedule(_f(h.get("avg")), stop_pct)
    out.update(fund=fund["items"], flow=flow["items"], vol=vol["items"], pyramid=pyr, sections=sections,
               score=sc, grade=grade, masters=masters)
    # 판정 보정: 펀더멘털이 가장 중요
    if fund.get("ok") is False and out["level"] in ("green", "yellow"):
        out.update(verdict="⚠️ 펀더멘털 약화 — 비중 점검", level="orange")
        out["actions"] = ["영업이익이 줄거나 적자 전망 — 추세 원칙상 '이익 증가 없는 강세'는 배제 대상",
                          f"손절선 {stop_price:,.0f} 유지, 반등 시 비중 축소 검토"] + out["actions"]
    elif fund.get("ok") is False:
        out["actions"] = out["actions"] + ["펀더멘털 약화(영업이익 감소·적자 전망) — 비중을 늘리지 말고, 익절·축소 쪽으로 무게"]
    elif fund.get("ok") is None and out["level"] in ("green", "yellow", "purple"):
        out["actions"] = out["actions"] + ["넘버스(영업이익 증가)가 아직 확인 안 됨 — 내러티브만으로 오르는 종목이면 비중 축소 권장, "
                                           "넘버스+내러티브가 같이 있는 종목이 우선"]
    elif pyr["ok"] and out["level"] in ("green", "yellow", "blue"):
        out.update(verdict="🔥 불타기 조건 충족", level="purple")
        out["actions"] = [f"오늘 새 돌파 + 거래량·장대양봉·주도섹터·RS·이익 증가 모두 충족 → 1유닛 추가 검토",
                          f"추가 후 손절선을 {pyr['new_stop']:,.0f}(본전 또는 돌파선 -{stop_pct:.0f}%)로 올리기"] + out["actions"]
    # 상황별 대응 시나리오
    plan = []
    if pyr.get("trigger"):
        plan.append(("📈 오르면", ("지금 조건 충족 — " if pyr["ok"] else "불타기 조건: ") + pyr["trigger"]
                     + f" → 1유닛 추가, 손절선 {pyr['new_stop']:,.0f}로 상향"))
    if target3r:
        plan.append(("💰 3R 도달", f"{target3r:,.0f} 도달 시 절반 익절, 손절선 본전 · 나머지는 5일선이 50일선에 닿을 때까지"))
    if ma_v:
        plan.append(("➖ 쉬면", f"{ma_key} {ma_v:,.0f} 위에서 거래량이 마르며 쉬면 보유 — 조정 거래량이 늘면 경계"))
    plan.append(("📉 내리면", f"손절선 {stop_price:,.0f} 이탈 시 정리(예외 없음)"
                 + (f" · {ma_key} {ma_v:,.0f} 대량 이탈이면 절반 축소 검토" if ma_v and ma_v > stop_price else "")))
    sched = pyr.get("schedule") or []
    if sched and pnl > 0:
        nxt = [x for x in sched if x[1] > (_f(m.get("price")) or 0)]
        if nxt:
            plan.append(("🪜 피라미드", " · ".join(f"{k} {p:,.0f}원(첫 매수의 {q}%)" for k, p, q in nxt[:2])
                         + " — 새 돌파·거래량 동반일 때만, 더 산 뒤엔 손절선도 같이 올리고 목표는 첫 매수가 기준 유지"))
    if any(x[1] == "깡토 · 시간 손절" for x in masters):
        plan.append(("⏱️ 시간", "목표에 못 닿고 옆으로 2~3주 — 현금이 필요하면 보유 기간 대비 수익이 낮은 순으로 정리"))
    if flow.get("smart5") is not None and (flow.get("smart5") or 0) < 0 and (flow.get("smart20") or 0) < 0:
        plan.append(("🧭 수급", "외국인+기관이 5일·20일 모두 순매도 — 수급은 보너스라 이것만으로 팔진 않되, 불타기는 추세·거래량이 확실할 때만"))
    if out.get("tight"):
        plan.append(("🌪️ 변동성", f"변동성 장세 후발 종목 — 손절 -{stop_pct:.0f}% · 목표 +{3 * stop_pct:.0f}%(손익비 1:3 유지), 쿠션이 생기면 원래 기준으로"))
    out["plan"] = plan


# ─────────────────────────── 깊은 분석: 펀더멘털 · 수급 · 거래량 · 불타기 · 시나리오 ───────────────────────────
def analyze_fundamental(fins: pd.DataFrame | None) -> dict:
    """영업이익·EPS 실적과 컨센서스(E)로 이익이 늘고 있는지. 추세 원칙: 영업이익이 늘지 않으면 강해도 배제."""
    out = {"state": "info", "items": [], "op_yoy": None, "op_next": None, "ok": None}
    if fins is None or fins.empty or "op" not in fins:
        out["items"].append(("info", "실적", "실적 자료를 받지 못했어요(해외 종목이거나 자료 없음)"))
        return out
    act = fins[~fins["is_est"]].dropna(subset=["op"])
    est = fins[fins["is_est"]].dropna(subset=["op"])
    items = []
    if len(act) >= 2:
        a0, a1 = float(act["op"].iloc[-2]), float(act["op"].iloc[-1])
        yoy = (a1 / a0 - 1) * 100 if a0 > 0 else None
        out["op_yoy"] = yoy
        if a1 <= 0:
            items.append(("bad", "영업이익", f"최근 실적({act['period'].iloc[-1]}) 적자 {a1:,.0f}억"))
        elif yoy is not None:
            items.append(("good" if yoy >= 15 else ("warn" if yoy < 0 else "info"), "영업이익(실적)",
                          f"{act['period'].iloc[-2]} {a0:,.0f}억 → {act['period'].iloc[-1]} {a1:,.0f}억 ({yoy:+.0f}%)"))
        if len(est):
            e0 = float(est["op"].iloc[0])
            nxt = (e0 / a1 - 1) * 100 if a1 > 0 else None
            out["op_next"] = nxt
            if nxt is not None:
                items.append(("good" if nxt >= 15 else ("bad" if nxt < 0 else "info"), "영업이익(전망)",
                              f"{est['period'].iloc[0]} 컨센서스 {e0:,.0f}억 ({nxt:+.0f}%)"
                              + (f" → {est['period'].iloc[-1]} {float(est['op'].iloc[-1]):,.0f}억" if len(est) > 1 else "")))
        else:
            items.append(("info", "영업이익(전망)", "컨센서스(E) 없음 — 앞으로도 늘지 확인 불가"))
    if "eps" in fins:
        seq = pd.concat([act.tail(3), est])["eps"].dropna().tolist()
        if len(seq) >= 3:
            rising = all(b > a for a, b in zip(seq, seq[1:]))
            items.append(("good" if rising else "info", "EPS", ("해마다 증가(정배열) " if rising else "들쭉날쭉 ")
                          + " → ".join(f"{v:,.0f}" for v in seq[-4:])))
    goods = sum(1 for x in items if x[0] == "good")
    bads = sum(1 for x in items if x[0] == "bad")
    out["ok"] = False if bads else (True if goods >= 2 else None)
    out["state"] = "bad" if bads else ("good" if goods >= 2 else "info")
    out["items"] = items
    return out


def analyze_flow(flow: pd.DataFrame | None, bars: pd.DataFrame | None) -> dict:
    """외국인·기관 수급: 5일·20일 누적(억원 추정), 연속 순매수, 가격과 같은 방향인지."""
    out = {"state": "info", "items": [], "smart5": None, "smart20": None}
    if flow is None or flow.empty or "외국인금액" not in flow:
        out["items"].append(("info", "수급", "수급 자료 없음(해외 종목이거나 아직 못 받음)"))
        return out
    f = flow.tail(20)
    fr5, in5 = f["외국인금액"].tail(5).sum(), f["기관금액"].tail(5).sum()
    fr20, in20 = f["외국인금액"].sum(), f["기관금액"].sum()
    ind20 = f["개인금액"].sum() if "개인금액" in f else None
    smart5, smart20 = fr5 + in5, fr20 + in20
    out.update(smart5=smart5, smart20=smart20)
    items = [("good" if smart5 > 0 else "warn", "외국인+기관 5일", f"{smart5:+,.0f}억 (외국인 {fr5:+,.0f} · 기관 {in5:+,.0f})"),
             ("good" if smart20 > 0 else "warn", "외국인+기관 20일", f"{smart20:+,.0f}억 (외국인 {fr20:+,.0f} · 기관 {in20:+,.0f})")]
    # 가격 방향과 수급 방향
    if bars is not None and len(bars) >= 21:
        c = pd.to_numeric(bars["close"], errors="coerce")
        ch20 = (c.iloc[-1] / c.iloc[-21] - 1) * 100
        if ch20 > 5 and smart20 < 0 and ind20 is not None and ind20 > 0:
            items.append(("bad", "수급 괴리", f"20일 {ch20:+.0f}% 올랐는데 외국인+기관은 팔고 개인만 삼({ind20:+,.0f}억) — 물량 넘기는 모양"))
        elif ch20 > 5 and smart20 > 0:
            items.append(("good", "수급 동행", f"20일 {ch20:+.0f}% 상승을 외국인+기관이 받쳐줌"))
        elif ch20 < -5 and smart20 > 0:
            items.append(("info", "수급 역행", f"20일 {ch20:+.0f}% 빠지는 동안 외국인+기관은 매수 — 매집 가능성, 가격 확인 필요"))
    bads = sum(1 for x in items if x[0] == "bad")
    goods = sum(1 for x in items if x[0] == "good")
    out["state"] = "bad" if bads else ("good" if goods >= 2 else ("warn" if goods == 0 else "info"))
    out["items"] = items
    return out


def analyze_volume(bars: pd.DataFrame | None, m: dict) -> dict:
    """거래량: 20일 상승일/하락일 거래량 비율(매집·분산), 눌림 거래량 감소, 오늘 캔들, 불타기 기준가(20일 고점)."""
    out = {"state": "info", "items": [], "pivot": None, "vol50": None, "big_candle": False, "vol_mult": None,
           "breakout_today": False}
    if bars is None or len(bars) < 60:
        return out
    b = bars.tail(80).reset_index(drop=True)
    o, h, l, c, v = (pd.to_numeric(b[k], errors="coerce").to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume"))
    vol50 = np.nanmean(v[-51:-1])
    out["vol50"] = vol50
    diff = np.diff(c[-21:])
    vv = v[-20:]
    upv, dnv = np.nansum(vv[diff > 0]), np.nansum(vv[diff < 0])
    udr = upv / dnv if dnv > 0 else None
    items = []
    if udr is not None:
        items.append(("good" if udr >= 1.3 else ("bad" if udr < 0.8 else "info"), "매집·분산(20일)",
                      f"상승일 거래량 ÷ 하락일 거래량 = {udr:.2f} " + ("(매집 우위)" if udr >= 1.3 else "(분산 우위)" if udr < 0.8 else "(중립)")))
    pivot = float(np.nanmax(h[-21:-1]))
    out["pivot"] = pivot
    rng = h[-1] - l[-1]
    dcr = (c[-1] - l[-1]) / rng if rng > 0 else 0.5
    chg = (c[-1] / c[-2] - 1) * 100 if c[-2] else 0
    atr = np.nanmean(np.maximum(h[-15:], np.roll(c, 1)[-15:]) - np.minimum(l[-15:], np.roll(c, 1)[-15:]))
    vm = v[-1] / vol50 if vol50 else None
    out["vol_mult"] = vm
    big = chg >= 3 and dcr >= 0.7 and rng >= atr * 1.2
    out["big_candle"] = bool(big)
    out["breakout_today"] = bool(c[-1] > pivot)
    if big:
        items.append(("good", "오늘 캔들", f"장대양봉 {chg:+.1f}% · 고가권 마감(DCR {dcr * 100:.0f}%) · 거래량 {vm:.1f}배"))
    elif chg < 0 and vm and vm >= 1.5:
        items.append(("bad", "오늘 캔들", f"{chg:+.1f}% 하락에 거래량 {vm:.1f}배 — 물량 출회"))
    else:
        items.append(("info", "오늘 캔들", f"{chg:+.1f}% · 종가 위치 {dcr * 100:.0f}% · 거래량 {vm:.1f}배" if vm else f"{chg:+.1f}%"))
    if c[-1] < pivot:
        pull = np.nanmean(v[-5:]) / vol50 if vol50 else None
        if pull is not None:
            items.append(("good" if pull < 0.8 else ("warn" if pull > 1.2 else "info"), "눌림 거래량",
                          f"20일 고점 아래 조정 중, 최근 5일 거래량 평소의 {pull:.2f}배 " + ("— 거래 마르며 쉬는 중(건강)" if pull < 0.8 else "— 조정에 거래가 많음" if pull > 1.2 else "")))
    bads = sum(1 for x in items if x[0] == "bad")
    goods = sum(1 for x in items if x[0] == "good")
    out["state"] = "bad" if bads else ("good" if goods >= 2 else "info")
    out["items"] = items
    return out


def pyramid_plan(h: dict, m: dict, vol: dict, fund: dict, flow: dict, ctx: dict, stop_pct: float, pnl: float) -> dict:
    """불타기(피라미딩) 조건: 수익 중 + 새 돌파(20일 고점) + 거래량 1.5배 + 장대양봉 + 주도섹터 + RS + 이익 증가 + 시장 60일선 위."""
    pivot, vol50 = vol.get("pivot"), vol.get("vol50")
    idx = [v for v in (ctx.get("idx_rs") or {}).values() if v is not None]
    rs = _f(m.get("rs"))
    conds = [
        ("수익 중", pnl > 0, f"{pnl:+.1f}%"),
        ("새 돌파(20일 고점)", vol.get("breakout_today", False), f"기준 {pivot:,.0f}" if pivot else "-"),
        ("거래량 1.5배↑", (vol.get("vol_mult") or 0) >= 1.5, f"{(vol.get('vol_mult') or 0):.1f}배"),
        ("장대양봉·고가권 마감", vol.get("big_candle", False), ""),
        ("주도섹터", (m.get("group") in (ctx.get("leaders") or set())), str(m.get("group") or "")),
        ("RS(지수 위·70↑)", rs is not None and rs >= 70 and all(rs > x for x in idx), f"{rs:.0f}" if rs is not None else "-"),
        ("이익 증가(펀더멘털)", fund.get("ok") is not False and (fund.get("op_next") or 0) >= 0, ""),
        ("시장 60일선 위", ctx.get("market_ok") is not False, ""),
        ("외국인+기관 5일 순매수", (flow.get("smart5") or 0) > 0, f"{(flow.get('smart5') or 0):+,.0f}억"),
    ]
    core = conds[:8]
    ok_core = all(c[1] for c in core)
    n_ok = sum(1 for c in conds if c[1])
    avg = _f(h.get("avg"))
    new_stop = max(avg or 0, (pivot or 0) * (1 - stop_pct / 100)) if pivot else avg
    trigger = None
    if pivot and vol50:
        trigger = f"종가가 {pivot:,.0f} 위로 마감 + 거래량 {vol50 * 1.5:,.0f}주(50일 평균 1.5배) 이상 + 장대양봉"
    return {"ok": ok_core, "n_ok": n_ok, "n": len(conds), "conds": conds, "new_stop": new_stop, "trigger": trigger}


def distribution_days(idx: pd.DataFrame | None, window: int = 25) -> int | None:
    """오닐의 '물량이 쏟아진 날': 지수가 전날보다 많은 거래량에 0.2% 넘게 내린 날(최근 25거래일). 4~5일이면 상승장 끝 신호."""
    if idx is None or len(idx) < window + 2 or "volume" not in idx:
        return None
    d = idx.tail(window + 1)
    c = pd.to_numeric(d["close"], errors="coerce").to_numpy(dtype=float)
    v = pd.to_numeric(d["volume"], errors="coerce").to_numpy(dtype=float)
    return int(sum(1 for i in range(1, len(c)) if c[i] < c[i - 1] * 0.998 and v[i] > v[i - 1]))


def masters_view(h: dict, m: dict, bars: pd.DataFrame | None, ctx: dict, flow_df, stop_pct: float, max_r) -> list:
    """거장 관점(유명 트레이더 매매법 정리본 기준) — 원칙 점검에 보태는 참고 항목. [복원] 값은 범위로 본 참고치예요."""
    items = []
    if bars is None or len(bars) < 210:
        return items
    c = pd.to_numeric(bars["close"], errors="coerce").to_numpy(dtype=float)
    hi = pd.to_numeric(bars["high"], errors="coerce").to_numpy(dtype=float)
    lo = pd.to_numeric(bars["low"], errors="coerce").to_numpy(dtype=float)
    v = pd.to_numeric(bars["volume"], errors="coerce").to_numpy(dtype=float)
    ma = lambda n: pd.Series(c).rolling(n).mean().to_numpy()
    m50, m150, m200, m20 = ma(50), ma(150), ma(200), ma(20)
    price = c[-1]
    # 미너비니 추세 템플릿(2017년 책 기준 8가지)
    rs = _f(m.get("rs"))
    hi52, lo52 = np.nanmax(hi[-250:]), np.nanmin(lo[-250:])
    tt = [price > m150[-1] and price > m200[-1], m150[-1] > m200[-1], m200[-1] > m200[-22],
          m50[-1] > m150[-1] and m50[-1] > m200[-1], price > m50[-1], price >= lo52 * 1.25,
          price >= hi52 * 0.75, rs is not None and rs >= 70]
    n = sum(bool(x) for x in tt)
    items.append(("good" if n == 8 else ("warn" if n >= 6 else "bad"), "미너비니 추세 템플릿",
                  f"{n}/8 충족 (150·200일선 위, 200일선 한 달 상승, 50일선 최상단, 52주 저점 +25%↑, 고점 -25% 이내, RS 70↑)"))
    bo_days = _f(m.get("bo_days"))
    if m.get("bo_status") == "유지" and bo_days is not None and bo_days <= 10 and price < m20[-1]:
        items.append(("warn", "미너비니 · 돌파 직후 20일선", "돌파한 지 얼마 안 돼 20일선 아래 마감 — 성공 확률이 절반으로 떨어진다고 봄"))
    # 와인스타인 단계(30주 ≈ 150일선)
    slope = (m150[-1] / m150[-21] - 1) * 100
    if price > m150[-1] and slope > 0.5:
        stage, st_ = "2단계(상승)", "good"
    elif price < m150[-1] and slope < -0.5:
        stage, st_ = "4단계(하락) — 사지 않는 구간", "bad"
    elif (c[-1] / np.nanmin(c[-250:]) - 1) > 0.6:
        stage, st_ = "3단계(천장 다지기) 가능성 — 평균선이 평평", "warn"
    else:
        stage, st_ = "1단계(바닥 다지기) — 30주선 저항 돌파 전", "info"
    items.append((st_, "와인스타인 단계", f"30주(150일)선 {slope:+.1f}%/20일 · {stage}"))
    # 오닐: 추격 매수 · 시장 물량일
    lvl, avg = _f(m.get("bo_level")), _f(h.get("avg"))
    if lvl and avg:
        ext = (avg / lvl - 1) * 100
        if ext > 5:
            items.append(("warn", "오닐 · 추격 매수", f"평단이 돌파선보다 {ext:+.1f}% 위 — 피벗 +5% 넘게 쫓아 사면 7~8% 손절이 제 역할을 못함"))
        elif ext >= -2:
            items.append(("good", "오닐 · 매수 자리", f"평단이 돌파선 {ext:+.1f}% 안 — 피벗 +5% 이내"))
    dd = ctx.get("dist_days")
    if dd:
        items.append(("bad" if max(dd.values()) >= 5 else ("warn" if max(dd.values()) >= 4 else "info"), "오닐 · 시장 물량일",
                      "최근 25거래일 " + " · ".join(f"{k} {v}일" for k, v in dd.items()) + " (4~5일이면 상승장 끝 신호)"))
    # 깡토: 주봉 정배열 · 수급 10% · 시간 손절
    wk = pd.Series(c, index=pd.to_datetime(bars["date"])).resample("W-FRI").last().dropna()
    if len(wk) >= 60:
        w5, w20, w60 = wk.rolling(5).mean().iloc[-1], wk.rolling(20).mean().iloc[-1], wk.rolling(60).mean().iloc[-1]
        ok = wk.iloc[-1] > w5 > w20 > w60
        items.append(("good" if ok else "warn", "깡토 · 주봉 정배열", "주봉 종가 > 5주 > 20주 > 60주선" + (" 충족" if ok else " 아님 — 주봉이 먼저")))
    if flow_df is not None and len(flow_df) and "외국인" in flow_df and "기관" in flow_df:
        last = flow_df.iloc[-1]
        q = (last.get("외국인") or 0) + (last.get("기관") or 0)
        dvol = None
        try:
            dd_ = pd.Timestamp(last["date"]).normalize()
            row = bars[pd.to_datetime(bars["date"]).dt.normalize() == dd_]
            dvol = float(row["volume"].iloc[-1]) if len(row) else None
        except (KeyError, ValueError, TypeError):
            dvol = None
        if dvol:
            share = q / dvol * 100
            items.append(("good" if share >= 10 else ("bad" if share <= -10 else "info"), "깡토 · 기관+외국인 비중",
                          f"최근 집계일 순매수가 거래량의 {share:+.0f}% (10% 이상이 최소 기준, 아래면 개인이 움직인 것)"))
    if h.get("buy_date"):
        try:
            held = int((pd.to_datetime(bars["date"]) >= pd.Timestamp(h["buy_date"])).sum())
            if held >= 15 and (max_r or 0) < 1:
                items.append(("warn", "깡토 · 시간 손절", f"매수 후 {held}거래일인데 1R도 못 감 — 목표 없이 옆으로 가면 2~3주에서 끊는 규칙"))
        except (ValueError, TypeError):
            pass
    # 너쟁이(추노) 관점: 수익 쿠션 · 타임컷 · 글로벌 피어 · 밸류 쿠션
    try:
        import views as _v
        items += nj_view(h, m, bars, ctx, stop_pct, max_r, _v)
    except Exception:  # noqa: BLE001
        pass
    # 깡토 급락장 Q&A: 같은 섹터 정찰병 우르르 · 쉬는 장의 정찰병
    try:
        items += kq_view(h, m, ctx, stop_pct, max_r)
    except Exception:  # noqa: BLE001
        pass
    # 리 태너(오닐 CAN SLIM) 매도·추가 규칙
    try:
        items += lt_view(h, m, bars, ctx)
    except Exception:  # noqa: BLE001
        pass
    # 쟁거: 돌파 당일 급등 · 거래량 실린 하락일
    if len(c) > 2:
        chg = (c[-1] / c[-2] - 1) * 100
        v50 = np.nanmean(v[-51:-1])
        if chg <= -3 and v50 and v[-1] >= v50 * 1.5:
            items.append(("bad", "쟁거 · 거래량 실린 하락일", f"{chg:+.1f}% · 거래량 {v[-1] / v50:.1f}배 — 절반 덜고, 되돌리는 거래량 실린 상승일이 나오면 다시 채움"))
    return items


def nj_view(h: dict, m: dict, bars: pd.DataFrame | None, ctx: dict, stop_pct: float, max_r, _v) -> list:
    """너쟁이 관점 항목. 손절·익절 기준은 효석 원칙 그대로 두고, '재량'으로 보태는 점검만 해요."""
    items = []
    price, avg = _f(m.get("price")), _f(h.get("avg"))
    if not price or not avg:
        return items
    pnl = (price / avg - 1) * 100
    r_now = pnl / stop_pct if stop_pct else None
    # ① 수익 쿠션: 한 번 1R 넘게 갔다가 평단 근처(0.3R 아래)로 밀리면 쿠션 훼손
    if max_r is not None and max_r >= 1 and r_now is not None and r_now < 0.3:
        items.append(("bad", "너쟁이 · 쿠션 훼손", f"매수 후 최고 {max_r:.1f}R → 지금 {r_now:+.1f}R — 쿠션이 평단을 위협하면 "
                      "희망 회로 없이 최대한 정리"))
    elif r_now is not None and r_now >= 1:
        items.append(("good", "너쟁이 · 수익 쿠션", f"쿠션 {pnl:+.1f}%({r_now:.1f}R) — 위에서 흔들려도 견딜 수 있는 자리"))
    elif r_now is not None and r_now < 0.5:
        items.append(("info", "너쟁이 · 쿠션 없음", f"쿠션 {pnl:+.1f}% — 휩소에 민감하게: 아이디어 훼손·시장 악화면 1~2%라도 덜어내기"))
    # ② 타임컷: 매수 후 5거래일 넘게 0.5R도 못 가면
    if h.get("buy_date") and bars is not None and len(bars):
        try:
            held = int((pd.to_datetime(bars["date"]) > pd.Timestamp(h["buy_date"])).sum())
        except (ValueError, TypeError):
            held = None
        if held is not None and held >= 5 and (max_r or 0) < 0.5:
            items.append(("warn", "너쟁이 · 타임컷", f"매수 후 {held}거래일, 최고 {max_r or 0:.1f}R — 아이디어대로면 며칠 안에 올라야. "
                          "안 오르면 2~3%라도 정리 검토"))
    # ③ 글로벌 피어: 내 분류의 해외 피어가 같이 꺾였는지
    pr = (ctx.get("peers") or {}).get(m.get("group"))
    if pr:
        lab = pr.get("verdict", "")
        st_ = "bad" if lab.startswith("🔴") else ("good" if lab.startswith("🟢") else ("info" if lab.startswith("🟡") else "info"))
        pb = pr.get("peer") or {}
        items.append((st_, "너쟁이 · 글로벌 피어", f"{lab} — 해외 피어({pr.get('peer_groups')}) 5일 {pb.get('r5', 0):+.1f}% · "
                      f"20일선 위 {pb.get('a20', 0):.0f}%"))
    # ④ 밸류 쿠션: 싸면 피라미딩 여유, 비싸면 추가 매수 신중·일부 익절
    per, peg = _v.fwd_per(m.get("_fins"), price)
    if per is not None:
        st_, txt = _v.value_cushion(per, peg)
        items.append((st_, "너쟁이 · 밸류 쿠션", txt))
    return items


def kq_view(h: dict, m: dict, ctx: dict, stop_pct: float, max_r) -> list:
    """깡토 급락장 Q&A(2026.10) 점검. 손절·익절 기준은 효석 원칙 그대로, 재량 참고만 보태요."""
    items = []
    grp = m.get("group") or ""
    mates = [n for n in (ctx.get("hold_groups") or {}).get(grp, []) if n != h.get("name")]
    if grp and len(mates) >= 2:
        items.append(("warn", "깡토 · 같은 섹터 우르르", f"'{grp}'에 {len(mates) + 1}종목({', '.join(mates[:4])} 포함) — "
                      "같은 섹터 정찰병을 한꺼번에 넣으면 작은 손절이 모여 계좌가 갈려요. 모니터링이 안 되면 "
                      "섹터에서 먼저 신고가·거래량 터진 1~2종목으로 줄이기"))
    price, avg = _f(m.get("price")), _f(h.get("avg"))
    if price and avg and ctx.get("market_ok") is False:
        r_now = (price / avg - 1) * 100 / stop_pct if stop_pct else None
        if r_now is not None and r_now < 1 and (max_r or 0) < 1:
            items.append(("info", "깡토 · 쉬는 장의 정찰병", "지수 60일선 아래 — 지금 포지션은 정찰병 크기가 맞는지 확인. "
                          "정찰병은 죽는 게 당연하고, 변동성이 커도 손절을 넓히지 않아요(넓히면 손실만 커짐). "
                          "재진입은 FTD(바닥 반등 3~4일차 강한 상승) 뒤 점진적으로"))
    return items


def lt_view(h: dict, m: dict, bars: pd.DataFrame | None, ctx: dict) -> list:
    """리 태너 규칙 참고 항목: +10% 뒤 12~14% 트레일링 · 8주 보유 예외 · +50% 뒤 주봉 10주선 · 10일선 저가 이탈 · 추가 매수 범위."""
    items = []
    ms = {k: x for k, x in (ctx.get("mkt_sig") or {}).items() if x}
    if ms:
        bad = any(x["state"] in ("corr", "rally") for x in ms.values())
        items.append(("bad" if any(x["state"] == "corr" for x in ms.values()) else ("warn" if bad or any(x["state"] == "press" for x in ms.values()) else "good"),
                      "오닐 · 시장 국면", " · ".join(f"{k} {x['em']} {x['state_txt']}" + (f" Day {x['day']}" if x["state"] == "rally" else "")
                                                  + f"(분산일 {x['dd']})" for k, x in ms.items())
                      + (" — FTD 전엔 추가 매수 보류, 쿠션 없는 종목부터 정리" if bad else "")))
    price, avg = _f(m.get("price")), _f(h.get("avg"))
    if not price or not avg or bars is None or len(bars) < 60:
        return items
    dts = pd.to_datetime(bars["date"])
    c = pd.to_numeric(bars["close"], errors="coerce").to_numpy(dtype=float)
    hi = pd.to_numeric(bars["high"], errors="coerce").to_numpy(dtype=float)
    lo = pd.to_numeric(bars["low"], errors="coerce").to_numpy(dtype=float)
    v = pd.to_numeric(bars["volume"], errors="coerce").to_numpy(dtype=float)
    since = np.ones(len(c), dtype=bool)
    if h.get("buy_date"):
        try:
            since = (dts >= pd.Timestamp(h["buy_date"])).to_numpy()
        except (ValueError, TypeError):
            pass
    if not since.any():
        return items
    top = float(np.nanmax(hi[since]))
    gain_max = (top / avg - 1) * 100
    pnl = (price / avg - 1) * 100
    # ① +10% 전엔 최초 손절, 뒤엔 최고가 -12~14% 트레일링
    if gain_max >= 10:
        t12, t14 = top * 0.88, top * 0.86
        st_ = "bad" if price < t14 else ("warn" if price < t12 else "good")
        items.append((st_, "리 태너 · 트레일링", f"매수 후 최고 +{gain_max:.0f}% → 최고가 {top:,.0f} 대비 -12~14% = {t14:,.0f}~{t12:,.0f}"
                      + (" 이탈" if price < t14 else (" 근처" if price < t12 else " 위"))))
    else:
        items.append(("info", "리 태너 · 트레일링", f"아직 +10% 전(최고 +{gain_max:.1f}%) — 최초 손절 기준 유지(태너: -7%, 장중 스톱은 -9%)"))
    # ② 8주 보유 예외: 매수(돌파) 뒤 15거래일 안에 +20%
    idx = np.flatnonzero(since)
    first = idx[0]
    w3 = hi[first:first + 15]
    if len(w3) and (np.nanmax(w3) / avg - 1) * 100 >= 20:
        end = dts.iloc[first] + pd.Timedelta(weeks=8)
        left = (end - pd.Timestamp.now()).days
        items.append(("good", "리 태너 · 8주 보유", f"매수 뒤 3주 안에 +20% — 대형 성장주 후보: {end:%m/%d}까지(남은 {max(left, 0)}일) 목표 도달해도 보유 검토"
                      if left > 0 else "매수 뒤 3주 안에 +20%였던 종목 — 8주 보유 기간 지남, 일반 매도 규칙으로"))
    # ③ +50% 뒤 주봉: 10주선 첫 이탈 + 거래량 40%↑
    if gain_max >= 50:
        wk = pd.DataFrame({"c": c, "v": v}, index=dts).resample("W-FRI").agg({"c": "last", "v": "sum"}).dropna()
        if len(wk) >= 12:
            ma10 = wk["c"].rolling(10).mean()
            vavg = wk["v"].rolling(10).mean().shift(1)
            br = wk["c"].iloc[-1] < ma10.iloc[-1]
            vx = wk["v"].iloc[-1] / vavg.iloc[-1] if vavg.iloc[-1] else np.nan
            items.append(("bad" if br and vx >= 1.4 else ("warn" if br else "good"), "리 태너 · 주봉 10주선",
                          f"+50% 넘은 종목 → 주봉으로 관리: 10주선 {ma10.iloc[-1]:,.0f} " + ("아래" if br else "위")
                          + (f", 주간 거래량 평균의 {vx:.1f}배" if vx == vx else "") + (" — 중요 매도 신호" if br and vx >= 1.4 else "")))
    # ④ 크리스 캐처: 10일선 아래 마감한 날의 저가 이탈
    ma10d = pd.Series(c).rolling(10).mean().to_numpy()
    below = [i for i in range(len(c) - 6, len(c) - 1) if c[i] < ma10d[i]]
    if below and gain_max >= 10:
        j = below[-1]
        trig = lo[j]
        items.append(("bad" if price < trig else "warn", "캐처 · 10일선",
                      f"{dts.iloc[j]:%m/%d} 10일선 아래 마감 — 그날 저가 {trig:,.0f} " + ("이탈: 매도 신호" if price < trig else "깨면 매도(스톱은 조금 아래)")))
    # ⑤ 추가 매수 범위: 마지막 매수가(모르면 평단) +6% 안
    last_buy = _f(h.get("last_buy")) or avg
    if pnl > 0:
        gap = (price / last_buy - 1) * 100
        items.append(("good" if gap <= 6 else "info", "리 태너 · 추가 매수 범위",
                      f"마지막 매수가 대비 {gap:+.1f}% — " + ("6% 안: 새 매수 신호면 정상 크기(직전 보유의 20%)" if gap <= 6
                                                        else "6% 넘음: 추가하면 절반 크기 또는 생략(평단이 현재가에 붙는 것 방지)")))
    return items


def pyramid_schedule(avg: float | None, stop_pct: float) -> list:
    """깡토 책의 피라미드: 손절 폭만큼 유리해질 때마다 더 사되 차수가 갈수록 줄이기(1차 30%, 2차 15%, 3차 10%)."""
    if not avg:
        return []
    return [(f"{k}차", avg * (1 + stop_pct / 100 * k), pct) for k, pct in ((1, 30), (2, 15), (3, 10))]


def score_card(sections: dict) -> tuple[int, str]:
    """펀더멘털 35 · 추세(가격) 30 · 거래량 15 · 수급 10 · 섹터·시장 10 — 가장 중요한 건 펀더멘털.
    깡토: 수급은 메인 팩터가 아니라 '보너스 알파' → 좋으면 더하고, 나빠도 깎지 않아요(보통 점수)."""
    w = {"펀더멘털": 35, "추세": 30, "거래량": 15, "수급": 10, "섹터·시장": 10}
    pts = {"good": 1.0, "info": 0.6, "warn": 0.35, "bad": 0.0}

    def p(k):
        v = pts.get(sections.get(k, "info"), 0.6)
        return max(v, 0.6) if k == "수급" else v
    total = sum(w[k] * p(k) for k in w)
    sc = int(round(total))
    grade = "A" if sc >= 80 else "B" if sc >= 65 else "C" if sc >= 50 else "D"
    return sc, grade


def _state_of(checks, names) -> str:
    sel = [c[0] for c in checks if c[1] in names]
    if "bad" in sel:
        return "bad"
    if sel and all(x == "good" for x in sel):
        return "good"
    if "warn" in sel:
        return "warn"
    return "info" if sel else "info"


def judge_value(h: dict, m: dict, bars: pd.DataFrame | None, ctx: dict) -> dict:
    """가치투자 계좌 점검(스승님 원칙): 가치우파에서만 천천히 분할매수, 가치훼손이면 매도, 추세 손절 규칙은 쓰지 않아요."""
    price, avg = _f(m.get("price")), _f(h.get("avg"))
    out = {"verdict": "판단 불가", "level": "gray", "pnl": None, "r": None, "stop_pct": None, "stop_price": None,
           "stop_dist": None, "target3r": None, "max_r": None, "checks": [], "actions": [], "account": "가치"}
    if not price or not avg:
        return out
    pnl = (price / avg - 1) * 100
    out.update(pnl=pnl, r=None)
    checks = []

    def add(state, item, text):
        checks.append((state, item, text))

    # 가치우파/좌파: 60일선 방향과 위치
    side = None
    if bars is not None and len(bars) >= 80:
        c = pd.to_numeric(bars["close"], errors="coerce").to_numpy(dtype=float)
        ma60 = pd.Series(c).rolling(60).mean().to_numpy()
        slope = (ma60[-1] / ma60[-21] - 1) * 100 if ma60[-21] == ma60[-21] and ma60[-21] else None
        low20, low_prev = np.nanmin(c[-20:]), np.nanmin(c[-60:-20])
        if slope is not None:
            if c[-1] >= ma60[-1] * 0.97 and slope >= -0.5 and low20 >= low_prev * 0.98:
                side = "우파"
                add("good", "가치우파", f"하락을 멈추고 횡보·우상향(60일선 {slope:+.1f}%/20일, 최근 저점이 이전 저점 위)")
            elif c[-1] < ma60[-1] and slope < 0:
                side = "좌파"
                add("bad", "가치좌파", f"아직 내려가는 중(60일선 {slope:+.1f}%/20일, 현재가 60일선 아래) — 떨어지는 칼날")
            else:
                side = "중간"
                add("warn", "방향 확인 중", f"60일선 {slope:+.1f}%/20일 — 하락이 멈췄는지 더 확인")
    # 절대가치
    val = value_estimate(_f(h.get("net_cash")), _f(h.get("op")), _f(h.get("opm")))
    cap = _f(m.get("cap_krw"))
    if val is not None and cap:
        cap_eok = cap / 1e8
        gap = (val / cap_eok - 1) * 100
        add("good" if gap >= 30 else ("info" if gap >= 0 else "warn"), "절대가치",
            f"순현금 {h.get('net_cash') or 0:,.0f}억 + 영업이익 {h.get('op'):,.0f}억 × {float(h.get('opm')) / 2:.1f}배 "
            f"= {val:,.0f}억 · 시가총액 {cap_eok:,.0f}억 대비 {gap:+.0f}%")
    elif val is None:
        add("info", "절대가치", "순현금·영업이익·영업이익률을 넣으면 스승님 방식 절대가치를 계산해요(고치기 칸)")
    fund = analyze_fundamental(m.get("_fins"))
    flow = analyze_flow(m.get("_flow"), bars)
    if fund.get("ok") is False:
        add("bad", "이익 감소", "영업이익 감소·적자 — 가치훼손 여부 점검 필요")
    if h.get("impaired"):
        add("bad", "가치훼손", "가치훼손 이슈 체크됨 — 원칙상 매도(가치우파가 좌파로 바뀌는 신호)")
    if ctx.get("market_ok") is not None:
        add("info", "시장", ctx.get("market_text", ""))
    out["checks"] = checks
    secs = {"펀더멘털": fund["state"], "수급": flow["state"], "거래량": "info",
            "추세": {"우파": "good", "좌파": "bad", "중간": "warn"}.get(side, "info"), "섹터·시장": "info"}
    sc, grade = score_card(secs)
    out.update(fund=fund["items"], flow=flow["items"], vol=[], sections=secs, score=sc, grade=grade, pyramid=None,
               plan=[("🌱 분할매수", "가치우파 확인 → 매수 기간·총 비중을 정해 두고 나눠 사기"),
                     ("🛑 매도", "가치훼손(이익 훼손·사업 문제)이 확인되면 매도 — 가격이 싸 보여도 예외 없음")])
    if h.get("impaired"):
        out.update(verdict="🛑 가치훼손 — 매도 원칙", level="red")
        out["actions"] = ["가치투자라고 무조건 들고 가는 게 아님 — 가치훼손이면 매도", "계좌 BEP는 그대로 지키기"]
    elif side == "좌파":
        out.update(verdict="⏸️ 가치좌파 — 매수 보류", level="orange")
        out["actions"] = ["하락이 멈추고 횡보·우상향(가치우파)이 확인될 때까지 추가매수 보류",
                          "가치훼손 이슈가 없는지 점검(실적·공시·산업)"]
    elif side == "우파":
        out.update(verdict="✅ 가치우파 — 분할매수 가능", level="green")
        out["actions"] = ["매수 기간과 총 비중을 정해 두고 천천히 분할매수", "가치훼손 이슈가 생기면 매도"]
    else:
        out.update(verdict="👀 방향 확인 중", level="yellow")
        out["actions"] = ["가치우파 확인 전까지는 서두르지 않기"]
    return out


# ─────────────────────────── 뉴스 · 리포트 모으기 ───────────────────────────
def _walk(node, fn):
    if isinstance(node, dict):
        fn(node)
        for v in node.values():
            _walk(v, fn)
    elif isinstance(node, list):
        for v in node:
            _walk(v, fn)


def naver_news(code: str, n: int = 12) -> list[dict]:
    """네이버 증권 종목 뉴스."""
    out: list[dict] = []
    try:
        j = requests.get(f"https://m.stock.naver.com/api/news/stock/{code}", params={"pageSize": n, "page": 1},
                         headers=UA, timeout=8).json()
    except (requests.RequestException, ValueError):
        return out

    def pick(d):
        t = d.get("title") or d.get("tit")
        oid, aid = d.get("officeId"), d.get("articleId")
        if t and (oid and aid) and not any(x["title"] == t for x in out):
            out.append({"title": _html.unescape(re.sub(r"<[^>]+>", "", str(t))), "source": d.get("officeName") or "네이버",
                        "time": str(d.get("datetime") or d.get("dt") or ""),
                        "url": f"https://n.news.naver.com/mnews/article/{oid}/{aid}", "portal": "네이버",
                        "body": _html.unescape(re.sub(r"<[^>]+>", "", str(d.get("body") or "")))[:200]})
    _walk(j, pick)
    for x in out:   # 202609281030 → 09/28 10:30
        m = re.match(r"(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})", x["time"])
        if m:
            x["time"] = f"{m.group(2)}/{m.group(3)} {m.group(4)}:{m.group(5)}"
    return out[:n]


def google_news(query: str, n: int = 12) -> list[dict]:
    """구글 뉴스(여러 언론사·포털 기사 모음) RSS."""
    out = []
    try:
        r = requests.get(f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=ko&gl=KR&ceid=KR:ko",
                         headers={"User-Agent": UA["User-Agent"]}, timeout=8)
        r.raise_for_status()
        for it in re.findall(r"<item>([\s\S]*?)</item>", r.text)[:n]:
            def tag(t):
                m = re.search(rf"<{t}[^>]*>([\s\S]*?)</{t}>", it)
                return _html.unescape(re.sub(r"<!\[CDATA\[|\]\]>", "", m.group(1))).strip() if m else ""
            title, src = tag("title"), tag("source")
            if src and title.endswith(f" - {src}"):
                title = title[: -len(src) - 3]
            try:
                t = parsedate_to_datetime(tag("pubDate")).astimezone(KST).strftime("%m/%d %H:%M")
            except (TypeError, ValueError):
                t = ""
            out.append({"title": title, "source": src or "구글 뉴스", "time": t, "url": tag("link"),
                        "portal": "구글 뉴스", "body": ""})
    except requests.RequestException:
        pass
    return out


def naver_research(code: str, n: int = 8) -> list[dict]:
    """네이버 증권 종목 리포트 목록(증권사·제목·날짜).
    2026.9 네이버가 예전 리서치 페이지(finance.naver.com/research)를 없애서 새 JSON 주소를 써요."""
    out = []
    try:
        r = requests.get(f"https://m.stock.naver.com/api/research/stock/{code}", params={"pageSize": n, "page": 1},
                         headers=UA, timeout=8)
        js = r.json()
        found = []
        _walk(js, lambda d: found.append(d) if isinstance(d, dict) and d.get("researchId") and d.get("title") else None)
        for d in found:
            w = re.sub(r"\D", "", str(d.get("writeDate") or ""))
            out.append({"title": _html.unescape(str(d["title"])), "source": str(d.get("brokerName") or ""),
                        "time": f"{w[2:4]}.{w[4:6]}.{w[6:8]}" if len(w) >= 8 else str(d.get("writeDate") or ""),
                        "url": f"https://stock.naver.com/research/company/{d['researchId']}", "portal": "증권사 리포트"})
            if len(out) >= n:
                break
    except (requests.RequestException, ValueError):
        pass
    return out


def gather(code: str, name: str, is_kr: bool) -> dict:
    """여러 곳에서 동시에 모아요. {news: [...], research: [...]}"""
    jobs = {"google": lambda: google_news(f"{name} 주가" if is_kr else f"{name} stock", 12)}
    if is_kr:
        jobs["naver"] = lambda: naver_news(code, 12)
        jobs["research"] = lambda: naver_research(code, 8)
    with ThreadPoolExecutor(max_workers=3) as pool:
        got = dict(zip(jobs, pool.map(lambda f: f(), jobs.values())))
    news, seen = [], set()
    for x in got.get("naver", []) + got.get("google", []):
        key = re.sub(r"\W", "", x["title"])[:30]
        if key and key not in seen:
            seen.add(key)
            news.append(x)
    return {"news": news[:20], "research": got.get("research", [])}


def mock_gather(code: str, name: str) -> dict:
    return {"news": [{"title": f"{name}, 신규 수주 기대감에 강세", "source": "테스트경제", "time": "09/28 10:12",
                      "url": "https://example.com", "portal": "네이버", "body": ""},
                     {"title": f"{name} 3분기 실적 컨센서스 상회 전망", "source": "테스트일보", "time": "09/27 17:40",
                      "url": "https://example.com", "portal": "구글 뉴스", "body": ""}],
            "research": [{"title": "수주 모멘텀 지속", "source": "테스트증권", "time": "26.09.25",
                          "url": "https://example.com", "portal": "증권사 리포트"}]}


AI_NEWS_PROMPT = """너는 효석의 추세추종 매매 원칙을 아는 보조야. 아래는 보유 종목의 최근 뉴스·리포트 제목과 원칙 점검 결과야.
한국어로, 아래 형식만 써(마크다운 소제목 없이, 문장은 짧게):
호재: (뉴스 근거 1~3개, 없으면 '뚜렷한 호재 없음')
악재: (뉴스 근거 1~3개, 없으면 '뚜렷한 악재 없음')
원칙과 연결: (뉴스가 손절·보유·익절 판단에 주는 의미 1~2문장. 추세 이탈 판단은 느리게, 급한 악재는 빠르게라는 원칙 기준)
주의: 뉴스 제목만으로 판단한 것이라 원문 확인 필요
매수·매도를 권하지 말고, 판단 재료만 정리해."""


def ai_news_summary(api_key: str, model: str, name: str, judged: dict, items: list[dict]) -> tuple[str | None, str]:
    lines = "\n".join(f"- [{x.get('portal')}·{x.get('source')}·{x.get('time')}] {x.get('title')}" for x in items[:30])
    checks = "\n".join(f"- {c[1]}: {c[2]}" for c in judged.get("checks", []))
    try:
        r = requests.post(ANTHROPIC_URL, timeout=90, headers={
            "x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": model, "max_tokens": 900, "messages": [{"role": "user", "content":
                  f"종목: {name}\n원칙 점검 결과: {judged.get('verdict')}\n{checks}\n\n최근 뉴스·리포트:\n{lines}\n\n{AI_NEWS_PROMPT}"}]})
    except requests.RequestException as exc:
        return None, f"AI 연결 실패: {exc.__class__.__name__}"
    if r.status_code != 200:
        try:
            return None, f"AI 요약 실패({r.status_code}): {r.json().get('error', {}).get('message', '')}"
        except ValueError:
            return None, f"AI 요약 실패({r.status_code})"
    return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text").strip(), ""


# ─────────────────────────── 👀 보유 살피기(실시간 점검 + AI 판단) ───────────────────────────
def volume_pace(volume, vol50, now: datetime | None = None, status: str | None = None) -> float | None:
    """오늘 누적 거래량이 '지금 시각까지 평소에 쌓였을 양'의 몇 배인지.
    장중에는 9:00~15:30(390분) 중 지난 시간 비율로 50일 평균을 나눠 비교해요(장 초반 10분은 과대 추정을 막으려 10분으로 고정)."""
    v, base = _f(volume), _f(vol50)
    if not v or not base:
        return None
    now = now or now_kst()
    mins = (now.hour * 60 + now.minute) - 9 * 60
    open_ = (status == "OPEN") or (status is None and now.weekday() < 5 and 0 <= mins <= 390)
    frac = min(1.0, max(10, mins) / 390) if open_ and mins < 390 else 1.0
    return v / (base * frac)


def news_is_today(t: str, now: datetime | None = None) -> bool:
    """뉴스 시각('10/17 09:30')이 오늘인지."""
    now = now or now_kst()
    return bool(t) and str(t).startswith(now.strftime("%m/%d"))


def flow_snapshot(flow: pd.DataFrame | None) -> dict:
    """수급 칸: 최근 거래일(날짜 표시)·5일·20일 외국인/기관, 연속 순매수 일수."""
    out = {"date": None, "rows": [], "streak": {}}
    if flow is None or flow.empty or "외국인금액" not in flow:
        return out
    f = flow.tail(20)
    last = f.iloc[-1]
    out["date"] = pd.Timestamp(last["date"]).strftime("%m/%d")
    for k in ("외국인", "기관", "개인"):
        col = f"{k}금액"
        if col not in f:
            continue
        out["rows"].append((k, _f(last[col]), float(f[col].tail(5).sum()), float(f[col].sum())))
    for k in ("외국인", "기관"):
        n, sign = 0, None
        for v in reversed(f[k].tolist()):
            if v is None or pd.isna(v) or v == 0:
                break
            if sign is None:
                sign = v > 0
            elif (v > 0) != sign:
                break
            n += 1
        out["streak"][k] = (n if sign else -n) if n else 0
    return out


AI_JUDGE_RULES = """[효석 매매 원칙 — 판단의 기준. 원칙이 AI의 의견보다 항상 우선]
- 우선순위: 리스크관리 > 멘탈관리 > 종목관리 > 타점관리. "시장은 예측이 아니라 대응", "손실은 짧게 수익은 길게".
- 1R 손절 = 평단 -8%(ATR이 8% 이상이면 ATR까지 넓히되 계좌 손실은 1.5% 이내). 손절선 이탈은 예외 없이 정리 검토.
- 물타기 절대 금지. 추가 매수는 수익 중인 종목의 새 돌파(불타기)에서만, 추가 후 손절선만 새 평단 기준으로 올리고 3R 익절가는 처음 가격 그대로.
- 3R(+24%)에서 절반 익절, 나머지는 일봉 5일선이 50일선에 닿으면 정리. R 사다리(원칙): 2R이면 손절선 본전, 4R이면 +1R, 5R이면 +2R …(설정에서 1R·2R 간격으로 바꿀 수 있음).
- 추세 이탈은 상승 속도에 맞는 이평선으로: 막 돌파 = 5일선, 추세 종목 = 20일선, 느린 종목 = 50일선. 추세가 꺾였다는 판단은 느리게, 급한 악재에는 빠르게.
- 클라이맥스(신고가에서 사상 최대 거래량·윗꼬리, 섹터 동반) 또는 고점권 -10% 대량 음봉 = 비중 축소 신호.
- 시장: 코스피·코스닥이 60일선 이하면 쉬는 장(신규·추가 매수 보류). 지수 물량일이 25일 중 4~5일이면 경계.
- 주도섹터(신고가 2종목 이상) 안의 대장주(RS·시총·거래대금 1위)를 오래 끌고 가는 게 핵심. RS는 70 이상이고 지수 RS보다 높아야.
- 제일 중요한 건 펀더멘털: 영업이익이 늘지 않는 강세는 배제, 넘버스 없는 내러티브는 비중 축소.
- 수급(외국인·기관 특히 연기금·투신)은 보너스. 가격은 오르는데 외국인+기관이 팔고 개인만 사면 경계.
- 추세 계좌와 가치 계좌는 섞지 않음. 추세 계좌에서 '싸졌다'며 버티기·추가 매수 금지.
- 가치 계좌 종목은 손절선이 아니라 가치우파(하락 멈춤·횡보·우상향) 확인 후 분할매수, 가치훼손이면 매도로 판단.

[참고 관점 — 깡토 × 너쟁이(추노) 인터뷰 · 리 태너 강연 · 깡토 분산일/FTD·하락장 백테스트 · 깡토 급락장 Q&A(2026.10) · 스승님 10/28 장중 멘트(3일차 강세 정찰병·의심스러운 FTD·견조주 신고가). 위 원칙과 충돌하면 위 원칙이 우선, 재량 판단에 보태는 용도]
""" + __import__("views").AI_VIEWS + "\n"

AI_JUDGE_PROMPT = """너는 효석의 추세추종 원칙을 그대로 적용하는 보유 종목 점검 보조야. 예측하지 말고, 위 원칙과 아래 숫자·뉴스로
'지금 원칙상 어떤 상태이고 어떻게 대응하는 게 원칙에 맞는지'만 판단해.
규칙 점검표(앱이 계산한 판정)는 숫자 계산이 정확하니 손절선·3R·R 값은 그대로 써. 네가 더할 건 뉴스·수급·거래량·펀더멘털을 엮은 해석이야.
원칙과 충돌하는 대응(물타기, 손절선 이탈 후 버티기, 쉬는 장 신규 매수)은 절대 권하지 마.
반드시 아래 JSON 하나만 출력해(설명·코드블록 없이). 모든 값은 한국어, 문장은 짧게, 가격은 숫자로:
{"state": "보유 유지 | 불타기 검토 | 절반 익절 | 나머지 정리 | 비중 축소 | 손절·정리 검토 | 관망·주의 중 하나",
 "level": "green | purple | blue | yellow | orange | red 중 하나 (보유 유지=green, 불타기=purple, 익절=blue, 주의=yellow, 축소=orange, 손절·정리=red)",
 "headline": "지금 상태 한 문장",
 "confidence": "높음 | 보통 | 낮음",
 "reasons": ["숫자를 넣은 근거 3~5개 — 추세·거래량·수급·펀더멘털·뉴스·시장"],
 "now": ["지금 할 일 1~3개 — 손절선·익절가 같은 구체 가격 포함"],
 "scenarios": [{"if": "예: 종가 85,000 이탈", "then": "원칙상 대응"}],
 "news": "뉴스·리포트가 판단에 주는 영향 1~2문장(호재/악재/중립 표시)",
 "diff": "앱의 규칙 판정과 다르게 본 점과 이유. 같으면 '규칙 판정과 같음'"}"""

AI_JUDGE_MODELS = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-4-5-20251001")

_M_LABELS = [("price", "현재가"), ("change", "오늘 등락률%"), ("high52", "52주 최고가"), ("to_high", "신고가까지%"),
             ("days_since_high", "신고가 후 경과일"), ("bo_status", "돌파 유지/이탈"), ("bo_nth", "몇 번째 돌파"),
             ("bo_days", "돌파 후 일수"), ("aligned", "정배열"), ("ma5", "5일선"), ("ma20", "20일선"), ("ma50", "50일선"),
             ("ma5_vs_50", "5일선-50일선 괴리%"), ("dist_ma20", "20일선 괴리%"), ("atr_pct", "ATR%(20일)"), ("adx", "ADX"),
             ("rs", "종합 RS"), ("rs_1m", "RS 1M"), ("ret_1m", "1개월 수익률%"), ("ret_3m", "3개월 수익률%"),
             ("dcr_today", "오늘 종가 위치(DCR)"), ("htf", "HTF"), ("sector", "산업"), ("group", "세부 분류")]


def _fmt_v(v) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "-"
    if isinstance(v, (bool, np.bool_)):
        return "예" if v else "아니오"
    if isinstance(v, (int, float, np.integer, np.floating)):
        return f"{float(v):,.2f}".rstrip("0").rstrip(".")
    return str(v)[:60]


def build_ai_payload(h: dict, m: dict, j: dict, ctx: dict, bars: pd.DataFrame | None, quote: dict | None,
                     ev: dict | None, pace: float | None) -> str:
    """AI에게 넘길 판단 재료(텍스트)."""
    L = [f"[종목] {h.get('name')}({h.get('code')}) · 계좌: {h.get('account') or '추세'} · 매수일 {h.get('buy_date') or '-'}"
         f" · 평단 {_fmt_v(h.get('avg'))} · 수량 {_fmt_v(h.get('qty'))} · 절반 익절함: {'예' if h.get('half_taken') else '아니오'}"
         + (f" · 메모: {h['memo']}" if h.get("memo") else "")]
    L.append(f"[규칙 판정] {j.get('verdict')} · 수익률 {_fmt_v(j.get('pnl'))}% · {_fmt_v(j.get('r'))}R · 매수 후 최고 {_fmt_v(j.get('max_r'))}R"
             f" · 손절선 {_fmt_v(j.get('stop_price'))}(손절 기준 {_fmt_v(j.get('stop_pct'))}%, 여유 {_fmt_v(j.get('stop_dist'))}%)"
             f" · 3R 목표 {_fmt_v(j.get('target3r'))}" + (f" · 점수 {j.get('grade')} {j.get('score')}점" if j.get("score") is not None else ""))
    for st_, item, txt in j.get("checks") or []:
        L.append(f"  - [{st_}] {item}: {txt}")
    for key, title in (("fund", "펀더멘털"), ("flow", "수급 분석"), ("vol", "거래량 분석"), ("masters", "거장 관점")):
        for st_, item, txt in j.get(key) or []:
            L.append(f"  - [{title}·{st_}] {item}: {txt}")
    if j.get("pyramid"):
        p = j["pyramid"]
        L.append(f"  - [불타기 점검] {p['n_ok']}/{p['n']} 충족: " + ", ".join(f"{n}{'✔' if ok else '✖'}" for n, ok, _ in p["conds"]))
    if j.get("actions"):
        L.append("  - [규칙상 행동] " + " / ".join(j["actions"]))
    L.append("[지표] " + " · ".join(f"{lab} {_fmt_v(m.get(k))}" for k, lab in _M_LABELS if m.get(k) is not None))
    if quote:
        L.append(f"[실시간 시세] 현재가 {_fmt_v(quote.get('price'))} · 시가 {_fmt_v(quote.get('open'))} · 고가 {_fmt_v(quote.get('high'))}"
                 f" · 저가 {_fmt_v(quote.get('low'))} · 전일 {_fmt_v(quote.get('prev'))} · 누적 거래량 {_fmt_v(quote.get('volume'))}"
                 f" · 거래량 페이스(평소 이 시각 대비) {_fmt_v(pace)}배 · 장 상태 {quote.get('status') or '-'}")
    if bars is not None and len(bars):
        b = bars.tail(15)
        L.append("[최근 15거래일 일봉: 날짜 시가 고가 저가 종가 거래량]")
        for _, r in b.iterrows():
            L.append(f"  {pd.Timestamp(r['date']):%m/%d} {_fmt_v(r['open'])} {_fmt_v(r['high'])} {_fmt_v(r['low'])} "
                     f"{_fmt_v(r['close'])} {_fmt_v(r['volume'])}")
    fl = flow_snapshot(m.get("_flow"))
    if fl["rows"]:
        L.append(f"[수급(억원 추정) 최근 거래일 {fl['date']} / 5일 / 20일] " + " · ".join(
            f"{k} {_fmt_v(d)} / {_fmt_v(d5)} / {_fmt_v(d20)}" for k, d, d5, d20 in fl["rows"])
                 + f" · 연속 순매수(음수=순매도) 외국인 {fl['streak'].get('외국인')}일 · 기관 {fl['streak'].get('기관')}일")
    L.append(f"[시장] {ctx.get('market_text')} · 60일선 위: {ctx.get('market_ok')} · 지수 RS {ctx.get('idx_rs')}"
             f" · 지수 물량일(25일) {ctx.get('dist_days')} · 변동성 장세: {ctx.get('vol_mode')}"
             f" · 주도섹터: {', '.join(sorted(ctx.get('leaders') or [])) or '없음'}")
    if ev:
        if ev.get("news"):
            L.append("[최근 뉴스 제목]")
            L += [f"  - {x.get('time')} {x.get('source')}: {x.get('title')}" + (f" — {x['body'][:120]}" if x.get("body") else "")
                  for x in ev["news"][:15]]
        if ev.get("research"):
            L.append("[증권사 리포트]")
            L += [f"  - {x.get('time')} {x.get('source')}: {x.get('title')}" for x in ev["research"][:6]]
    return "\n".join(L)


def ai_judge(api_key: str, model: str | None, payload: str, timeout: int = 90) -> tuple[dict | None, str]:
    """원칙 + 재료 → AI 판단(JSON). (결과, 오류)"""
    tried = []
    r = None
    for mdl in dict.fromkeys([m for m in (model, *AI_JUDGE_MODELS) if m]):
        tried.append(mdl)
        try:
            r = requests.post(ANTHROPIC_URL, timeout=timeout, headers={
                "x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": mdl, "max_tokens": 1800, "system": AI_JUDGE_RULES,
                      "messages": [{"role": "user", "content": payload + "\n\n" + AI_JUDGE_PROMPT}]})
        except requests.RequestException as exc:
            return None, f"AI 연결 실패: {exc.__class__.__name__}"
        if r.status_code == 404 or (r.status_code == 400 and "model" in r.text.lower() and "not" in r.text.lower()):
            continue
        break
    if r is None or r.status_code != 200:
        try:
            msg = r.json().get("error", {}).get("message", "") if r is not None else ""
        except ValueError:
            msg = r.text[:200]
        return None, f"AI 판단 실패({getattr(r, 'status_code', '-')}): {msg}"
    txt = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
    return parse_ai_judge(txt)


AI_STATES = ("보유 유지", "불타기 검토", "절반 익절", "나머지 정리", "비중 축소", "손절·정리 검토", "관망·주의")


def parse_ai_judge(txt: str) -> tuple[dict | None, str]:
    txt = re.sub(r"```(?:json)?", "", txt or "").strip()
    mm = re.search(r"\{[\s\S]*\}", txt)
    if not mm:
        return None, "AI 응답에서 결과를 못 찾았어요. 다시 시도해 보세요."
    try:
        d = json.loads(mm.group(0))
    except ValueError:
        return None, "AI 응답 형식이 깨졌어요. 다시 시도해 보세요."
    state = str(d.get("state") or "").strip()
    d["state"] = next((s for s in AI_STATES if s in state), state or "관망·주의")
    if d.get("level") not in ("green", "purple", "blue", "yellow", "orange", "red"):
        d["level"] = {"보유 유지": "green", "불타기 검토": "purple", "절반 익절": "blue", "나머지 정리": "orange",
                      "비중 축소": "orange", "손절·정리 검토": "red"}.get(d["state"], "yellow")
    for k in ("reasons", "now"):
        d[k] = [str(x) for x in (d.get(k) or []) if x][:6]
    d["scenarios"] = [x for x in (d.get("scenarios") or []) if isinstance(x, dict) and x.get("if")][:5]
    d["at"] = now_kst().strftime("%m/%d %H:%M")
    return d, ""


def mock_ai_judge(j: dict) -> dict:
    lv = j.get("level") or "yellow"
    st_ = {"red": "손절·정리 검토", "orange": "비중 축소", "blue": "절반 익절", "purple": "불타기 검토", "green": "보유 유지"}.get(lv, "관망·주의")
    return {"state": st_, "level": lv if lv in ("green", "purple", "blue", "yellow", "orange", "red") else "yellow",
            "headline": "(테스트) 규칙 판정과 같은 방향 — 손절선 유지하며 추세 확인",
            "confidence": "보통", "reasons": ["(테스트) 20일선 위 유지", "(테스트) 외국인+기관 5일 순매수"],
            "now": [f"손절선 {_fmt_v(j.get('stop_price'))} 유지"],
            "scenarios": [{"if": f"종가 {_fmt_v(j.get('stop_price'))} 이탈", "then": "정리 검토"},
                          {"if": f"{_fmt_v(j.get('target3r'))} 도달", "then": "절반 익절"}],
            "news": "중립 — (테스트 데이터)", "diff": "규칙 판정과 같음", "at": now_kst().strftime("%m/%d %H:%M")}
