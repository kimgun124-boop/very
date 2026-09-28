"""💼 내 보유 종목: 평단가 저장 · 내 매매 원칙으로 판단 · 뉴스/리포트 근거 모으기.

판단은 효석 매매 원칙을 그대로 옮긴 규칙이에요(매수·매도 추천이 아니라 원칙 점검표).
- 1R 손절 = 평단 -8% (ATR이 8% 이상이면 ATR까지), 2R 이상 갔던 종목은 손절선을 본전으로
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

HOLDINGS_VERSION = "2026-09-28-holdings"
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
                half_taken: bool, memo: str) -> dict:
    return {"id": uuid.uuid4().hex[:10], "code": code, "name": name, "avg": float(avg), "qty": float(qty or 0),
            "buy_date": buy_date, "stop_pct": stop_pct, "half_taken": bool(half_taken), "memo": memo,
            "added_at": now_kst().strftime("%Y-%m-%d %H:%M")}


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
    price, avg = _f(m.get("price")), _f(h.get("avg"))
    out = {"verdict": "판단 불가", "level": "gray", "pnl": None, "r": None, "stop_pct": None, "stop_price": None,
           "stop_dist": None, "target3r": None, "max_r": None, "checks": [], "actions": []}
    if not price or not avg:
        return out
    atr = _f(m.get("atr_pct"))
    stop_pct = _f(h.get("stop_pct")) or max(8.0, atr or 0.0)
    pnl = (price / avg - 1) * 100
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
    breakeven = (max_r or 0) >= 2 or bool(h.get("half_taken"))
    stop_price = avg if breakeven else avg * (1 - stop_pct / 100)
    target3r = avg * (1 + 3 * stop_pct / 100)
    out.update(pnl=pnl, r=r_now, stop_pct=stop_pct, stop_price=stop_price, stop_dist=(price / stop_price - 1) * 100,
               target3r=target3r, max_r=max_r)

    checks = []   # (상태 good/warn/bad/info, 항목, 설명)

    def add(state, item, text):
        checks.append((state, item, text))

    # 1) 손절
    if price <= stop_price:
        add("bad", "손절선", f"현재가가 손절선 {stop_price:,.0f} 아래 — {'본전 손절(2R 이상 갔던 종목)' if breakeven else f'1R(-{stop_pct:.0f}%)'} 도달")
    else:
        add("good" if out["stop_dist"] > 3 else "warn", "손절선",
            f"{'본전' if breakeven else f'-{stop_pct:.0f}%'} {stop_price:,.0f} · 여유 {out['stop_dist']:.1f}%"
            + (" (ATR이 8%보다 커서 ATR로 넓힘)" if not h.get("stop_pct") and atr and atr > 8 else ""))
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
    out["checks"] = checks

    bad = [c for c in checks if c[0] == "bad"]
    warn = [c for c in checks if c[0] == "warn"]
    if any(c[1] == "손절선" for c in bad):
        out.update(verdict="🛑 손절 조건 도달", level="red")
        out["actions"] = [f"원칙: 손절선 {stop_price:,.0f} 이탈 — 정리 검토", "예외를 두지 않는 게 원칙(리스크관리 > 멘탈관리)"]
    elif r_now >= 3 and not h.get("half_taken"):
        out.update(verdict="💰 3R 익절 구간", level="blue")
        out["actions"] = ["원칙: 절반 익절 후 손절선을 본전으로", "나머지는 5일선이 50일선에 닿을 때까지"]
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
    """네이버 증권 종목 리포트 목록(증권사·제목·날짜)."""
    out = []
    try:
        r = requests.get("https://finance.naver.com/research/company_list.naver",
                         params={"searchType": "itemCode", "itemCode": code}, headers=UA, timeout=8)
        text = r.content.decode("cp949", errors="ignore")
        for tr in re.findall(r"<tr>([\s\S]*?)</tr>", text):
            m = re.search(r'href="(company_read\.naver\?[^"]+)"[^>]*>([^<]+)</a>', tr)
            tds = [re.sub(r"<[^>]+>", "", t).strip() for t in re.findall(r"<td[^>]*>([\s\S]*?)</td>", tr)]
            if not m or len(tds) < 5:
                continue
            out.append({"title": _html.unescape(m.group(2).strip()), "source": tds[2], "time": tds[4],
                        "url": "https://finance.naver.com/research/" + _html.unescape(m.group(1)), "portal": "증권사 리포트"})
            if len(out) >= n:
                break
    except requests.RequestException:
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
