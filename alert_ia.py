"""🔔 인더스트리 액션 '매수하세요' 휴대폰 알림 (GitHub Actions에서 장중 10분마다 실행).

- 앱(hyoseok.streamlit.app)과 똑같은 판정(data.ia_buy_signals)으로 stocks.py 국내 종목을 살펴요.
- 아침 1차(09:30~15:00): 오늘 인더스트리 액션 섹터에서 매수 조건을 모두 통과한 종목이 새로 나오면 알림
- 종가 2차(15:05~15:25): 아직 조건이 유지되는 종목은 '종가 매수하세요', 아침 신호였는데 깨진 종목은 '취소' 알림
- 같은 종목·같은 단계 알림은 하루 한 번만 보내요(.alert_state/sent.json).

필요한 설정: GitHub 저장소 Settings → Secrets → Actions 에 DISCORD_WEBHOOK_URL
테스트: python alert_ia.py --test  (웹훅으로 시험 메시지 한 통)
"""
from __future__ import annotations

import json
import os
import pickle
import sys
from datetime import time as dtime
from pathlib import Path

import requests

import data
import market as msig
import scout
import stocks as stock_list

STATE = Path(".alert_state/sent.json")
CACHE = Path(".alert_cache")
WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()


def send(text: str):
    print(text)
    if not WEBHOOK:
        print("(DISCORD_WEBHOOK_URL 이 없어서 보내지 않았어요)")
        return
    for i in range(0, len(text), 1900):          # 디스코드 한 메시지 2,000자 제한
        r = requests.post(WEBHOOK, json={"content": text[i:i + 1900]}, timeout=10)
        print("discord", r.status_code)


def load_state(today: str) -> dict:
    try:
        st = json.loads(STATE.read_text())
    except (OSError, ValueError):
        st = {}
    return st if st.get("date") == today else {"date": today, "sent": {}}


def save_state(st: dict):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False))


def cached(name: str, key: str, loader):
    """오늘 이미 받은 일봉·주식수는 다시 받지 않아요(실시간 시세만 매번)."""
    CACHE.mkdir(exist_ok=True)
    f = CACHE / f"{name}.pkl"
    try:
        k, v = pickle.loads(f.read_bytes())
        if k == key:
            return v
    except (OSError, pickle.PickleError, ValueError, EOFError):
        pass
    v = loader()
    f.write_bytes(pickle.dumps((key, v)))
    return v


def main():
    if "--test" in sys.argv:
        send("🔔 신고가 보드 알림 연결 테스트예요. 이 메시지가 보이면 '매수하세요' 알림이 이 채널로 와요.")
        return
    now = data.now_kst()
    force = "--force" in sys.argv
    t = now.time()
    if not force and (now.weekday() >= 5 or not (dtime(9, 30) <= t <= dtime(15, 25))):
        print("장 시간이 아니에요:", now)
        return
    phase = "close" if dtime(15, 5) <= t <= dtime(15, 25) else "morning"
    today = now.date().isoformat()

    stocks = [dict(s, tags=s.get("tags") or [], notes=s.get("notes") or [])
              for s in stock_list.STOCKS if s.get("code") and data.is_kr(s["code"])]
    codes = tuple(sorted({s["code"] for s in stocks}))
    histories = cached("hist", today, lambda: data.fetch_histories(codes))
    shares = cached("shares", f"{now.isocalendar()[0]}-{now.isocalendar()[1]}", lambda: data.fetch_kr_shares(codes)[0])
    quotes, err = data.fetch_quotes(codes)
    if err:
        print(err)
    if len(quotes) < len(codes) * 0.5:
        print("실시간 시세를 절반도 못 받았어요 — 이번엔 건너뛰어요")
        return
    idx = data.fetch_index_histories()
    df = data.build_table(stocks, histories, quotes, shares, {"KRW": 1.0})
    sig = data.ia_buy_signals(df, histories, quotes, idx)
    print("market", sig["market_text"], "d20", sig["d20"], "sectors", list(sig["sectors"]),
          "signals", [s["name"] for s in sig["signals"]], "watch", len(sig["watch"]))

    st = load_state(today)
    sent = st["sent"]
    if phase == "morning":
        new = [s for s in sig["signals"] if "morning" not in sent.get(s["code"], [])]
        if new:
            send(data.ia_signal_text({**sig, "signals": new}, "매수하세요 — 1차(장중) · 인더스트리 액션"))
            for s in new:
                sent.setdefault(s["code"], []).append("morning")
    else:
        keep = [s for s in sig["signals"] if "close" not in sent.get(s["code"], [])]
        if keep:
            send(data.ia_signal_text({**sig, "signals": keep}, "종가 매수하세요 — 2차 · 종가까지 조건 유지"))
            for s in keep:
                sent.setdefault(s["code"], []).append("close")
        valid = {s["code"] for s in sig["signals"]}
        names = dict(zip(df["code"], df["name"]))
        broke = [c for c, v in sent.items() if "morning" in v and c not in valid and "cancel" not in v]
        if broke:
            send("⚠️ **아침 신호 취소 — 종가 매수 보류**\n" + "\n".join(
                f"• {names.get(c, c)} ({c}): 지금은 조건이 깨졌어요(거래량·양봉·저항·종가강도 중). 아침에 샀다면 손절선 지키기"
                for c in broke))
            for c in broke:
                sent[c].append("cancel")
    # 🛰️ 정찰병 신호(스승님 플레이북): 지수가 60일선 아래라 '매수하세요'가 안 뜨는 반등장에서도
    # 3일차 강세·의심스러운 FTD·FTD면 견조주 중 오늘 거래량 실어 신고가 가는 종목을 알려요(하루 한 번씩만)
    try:
        ms = {name: msig.analyze((idx.get(sym) or (None, None))[0]) for name, sym in data.MARKETS.items()}
        phases = {n: scout.index_phase(m) for n, m in ms.items()}
        ph, ph_txt = scout.combine(phases)
        if scout.scout_allowed(ph) and ph != "up":
            best = max(((n, v) for n, v in phases.items() if v), key=lambda kv: kv[1]["rank"])[0]
            res = scout.resilient(df, histories, ms[best].get("low_date"), ms[best].get("peak_date"))
            picks = scout.pick(scout.candidates(df, sig.get("scan"), res, set(sig.get("ia_all") or [])))
            done = st.setdefault("scout", [])
            new = [r for r in picks.itertuples() if r.code not in done]
            if new:
                pb = scout.PLAYBOOK[ph]
                send(f"🛰️ **정찰병 신호 — {pb['title']}**  ({now:%m/%d %H:%M})\n{ph_txt}\n"
                     + "\n".join(f"• **{r.name}** {r.price:,.0f}원 {r.chg:+.1f}% · {r.group} — {r.why}\n"
                                 f"   손절 {r.stop:,.0f} · 3R {r.target:,.0f} · 계좌 약 {scout.SCOUT_WEIGHT:.0f}%"
                                 for r in new)
                     + f"\n스승님: “{pb['mentor']}” · 정해진 규칙대로, 같은 섹터 우르르 금지")
                done.extend(r.code for r in new)
                save_state(st)
    except Exception as exc:  # noqa: BLE001 — 정찰병 알림이 실패해도 '매수하세요' 알림은 그대로
        print("정찰병 신호 실패:", exc)


if __name__ == "__main__":
    main()
