"""🆕 신규상장 종목 — 한국거래소 KIND 공식 자료로만 모아요(사실 기반).

- 신규상장 기업 목록(상장유형 = 신규상장): 회사명 · 상장일 · 시장 · 업종 · 주관사
- 공모가 대비 주가: 공모가 · (수정)공모가 · 상장일 시가(시초가)
- 상장법인목록: 종목코드 · 주요제품
- 공모기업 현황: 상장 예정(공모가 확정 · 상장 예정일)

Streamlit 서버(해외)에서는 KIND가 막힐 때가 있어서, GitHub Actions(.github/workflows/ipo.yml)가
평일 저녁마다 `python ipo.py`로 받아 ipo_list.json에 저장하고 커밋해요. 앱과 stocks.py는 이 파일만 읽어요.
스팩·코넥스·리츠는 빼요(매매 대상이 아니라서).
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ipo_list.json")
KST = timezone(timedelta(hours=9))
DAYS = 365                     # 최근 1년 상장 종목만 보드에 올려요
KIND = "https://kind.krx.co.kr"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def _strip(t: str | None) -> str:
    t = re.sub(r"<[^>]+>", " ", t or "").replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", t).strip()


def _num(t) -> float | None:
    s = re.sub(r"[^\d.\-]", "", str(t or ""))
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v > 0 else None


def _rows(html: str) -> list[list[str]]:
    out = []
    for tr in re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", html or ""):
        tds = re.findall(r"<td([^>]*)>([\s\S]*?)</td>", tr)
        if tds:
            out.append([(_strip(body), attrs + body) for attrs, body in tds])
    return out


def _is_excluded(name: str) -> bool:
    return bool(re.search(r"스팩|기업인수목적|리츠|REIT", name or "", re.I))


# ─────────────────────────── 파서(받은 HTML → 행) ───────────────────────────
def parse_newlist(html: str) -> list[dict]:
    """신규상장 기업 목록 → [{name, date, market, industry, broker}]. 시장은 회사명 칸의 아이콘 alt(코스닥·유가·코넥스)."""
    out = []
    for r in _rows(html):
        if len(r) < 7 or not re.fullmatch(r"\d{4}-\d\d-\d\d", r[1][0]):
            continue
        alts = re.findall(r"alt=['\"]([^'\"]+)['\"]", r[0][1])
        mk = next((a for a in alts if a in ("유가증권", "유가", "코스닥", "코넥스")), "")
        out.append({"name": r[0][0].strip(), "date": r[1][0], "kind": r[2][0], "market": mk,
                    "industry": r[4][0], "broker": r[6][0]})
    return out


def parse_pubprice(html: str) -> dict[str, dict]:
    """공모가 대비 주가 → {회사명: {date, offer, offer_adj, open1}}."""
    out = {}
    for r in _rows(html):
        if len(r) < 6 or not re.fullmatch(r"\d{4}-\d\d-\d\d", r[2][0]):
            continue
        out[r[0][0].strip()] = {"date": r[2][0], "offer": _num(r[3][0]), "offer_adj": _num(r[4][0]), "open1": _num(r[5][0])}
    return out


def parse_pubofr(html: str) -> list[dict]:
    """공모기업 현황 → 상장 예정 [{name, demand, sub, pay, offer, amount, list_date, broker}]."""
    out = []
    for r in _rows(html):
        if len(r) < 9 or not re.fullmatch(r"\d{4}-\d\d-\d\d", r[1][0]):
            continue
        out.append({"name": r[0][0].strip(), "filed": r[1][0], "demand": r[2][0], "sub": r[3][0], "pay": r[4][0],
                    "offer": _num(r[5][0]), "amount_mil": _num(r[6][0]), "list_date": r[7][0] if re.match(r"\d{4}-", r[7][0]) else "",
                    "broker": r[8][0]})
    return out


def parse_corplist(html: str, market: str) -> dict[str, dict]:
    """상장법인목록(다운로드) → {회사명: {code, market, industry, product, list_date}}."""
    heads = [_strip(h) for h in re.findall(r"<th[^>]*>([\s\S]*?)</th>", html or "")]
    out = {}
    for r in _rows(html):
        vals = [v for v, _ in r]
        if len(vals) != len(heads):
            continue
        d = dict(zip(heads, vals))
        code = (d.get("종목코드") or "").strip()
        if not re.fullmatch(r"[0-9A-Z]{1,6}", code):
            continue
        out[d.get("회사명", "").strip()] = {"code": code.zfill(6), "market": market, "industry": d.get("업종", ""),
                                          "product": d.get("주요제품", ""), "list_date": d.get("상장일", "")}
    return out


def build(newlist: list[dict], prices: dict[str, dict], corps: dict[str, dict], upcoming: list[dict],
          today: date | None = None) -> dict:
    today = today or datetime.now(KST).date()
    since = today - timedelta(days=DAYS)
    items, skipped = [], []
    for x in newlist:
        name = x["name"]
        if x.get("kind") and x["kind"] != "신규상장":
            continue
        d = date.fromisoformat(x["date"])
        if d < since or d > today:
            continue
        c = corps.get(name) or {}
        mk = c.get("market") or {"유가": "코스피", "유가증권": "코스피"}.get(x["market"], x["market"])
        if _is_excluded(name) or mk == "코넥스" or not c.get("code"):
            skipped.append({"name": name, "why": "스팩·리츠" if _is_excluded(name) else ("코넥스" if mk == "코넥스" else "코드 없음")})
            continue
        p = prices.get(name) or {}
        items.append({"code": c["code"], "name": name, "market": mk, "list_date": x["date"],
                      "industry": c.get("industry") or x.get("industry", ""), "product": c.get("product", ""),
                      "broker": x.get("broker", ""), "offer": p.get("offer_adj") or p.get("offer"),
                      "offer_raw": p.get("offer"), "open1": p.get("open1")})
    items.sort(key=lambda r: r["list_date"], reverse=True)
    up = [u for u in upcoming if u.get("list_date") and u["list_date"] > today.isoformat() and not _is_excluded(u["name"])]
    up.sort(key=lambda r: r["list_date"])
    return {"updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "source": "한국거래소 KIND",
            "items": items, "upcoming": up, "skipped": skipped}


# ─────────────────────────── 받기 ───────────────────────────
def fetch(today: date | None = None) -> dict:
    import requests
    today = today or datetime.now(KST).date()
    s = requests.Session()
    s.headers.update(UA)
    frm, to = (today - timedelta(days=DAYS + 10)).isoformat(), today.isoformat()

    def post(path, data):
        r = s.post(KIND + path, data=data, headers={"Referer": KIND + path}, timeout=30)
        r.raise_for_status()
        return r.text

    newlist = parse_newlist(post("/listinvstg/listingcompany.do", {
        "method": "searchListingTypeSub", "currentPageSize": "500", "pageIndex": "1", "orderMode": "1", "orderStat": "D",
        "forward": "listingtype_sub", "listTypeArrStr": "01|", "choicTypeArrStr": "",
        "secuGrpArrStr": "0|ST|FS|MF|SC|IF|DR|", "marketType": "", "fromDate": frm, "toDate": to}))
    prices = parse_pubprice(post("/listinvstg/pubprcCmpStkprcByIssue.do", {
        "method": "pubprcCmpStkprcByIssueSub", "currentPageSize": "500", "pageIndex": "1", "orderMode": "1",
        "orderStat": "D", "marketType": "", "searchCorpName": "", "fromDate": frm, "toDate": to}))
    upcoming = parse_pubofr(post("/listinvstg/pubofrprogcom.do", {
        "method": "searchPubofrProgComSub", "currentPageSize": "300", "pageIndex": "1", "orderMode": "1", "orderStat": "D",
        "searchCorpName": "", "marketType": "", "fromDate": (today - timedelta(days=60)).isoformat(),
        "toDate": (today + timedelta(days=90)).isoformat()}))
    corps: dict[str, dict] = {}
    for mt, label in (("stockMkt", "코스피"), ("kosdaqMkt", "코스닥")):
        r = s.get(KIND + "/corpgeneral/corpList.do", params={"method": "download", "searchType": "13", "marketType": mt},
                  timeout=30)
        r.raise_for_status()
        corps.update(parse_corplist(r.content.decode("cp949", errors="ignore"), label))
    if not newlist or not corps:
        raise RuntimeError(f"KIND 응답이 비었어요(신규상장 {len(newlist)}, 상장법인 {len(corps)})")
    return build(newlist, prices, corps, upcoming, today)


def load() -> dict:
    """저장된 ipo_list.json(없거나 깨졌으면 빈 값)."""
    try:
        with open(PATH, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


if __name__ == "__main__":
    got = fetch()
    old = load()
    if old.get("items") == got["items"] and old.get("upcoming") == got["upcoming"]:
        print("바뀐 게 없어요")
        sys.exit(0)
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(got, f, ensure_ascii=False, indent=1)
    print(f"신규상장 {len(got['items'])}개 · 상장 예정 {len(got['upcoming'])}개 · 제외 {len(got['skipped'])}개 저장")
