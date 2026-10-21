"""📣 실적·지분 추적 — 텔레그램 채널의 어닝 서프라이즈 + DART 대량보유(VC·펀드 지분 확대) 공시.

- 텔레그램: t.me/s/<채널>?q=<검색어> 공개 미리보기 HTML을 읽어요(로그인 불필요).
    · @maddingStock(스탁이지) '어닝 서프라이즈 요약': "1. 파두(440110)" / "영업익 363억 | 예상비 +14.2%"
    · @awake_schedule(AWAKE) 실적 게시물: 같은 모양이 아니어도 '종목(코드)' + 영업이익·컨센 대비 숫자를 찾아요.
- DART(OpenDART, 무료 인증키 필요): 지분공시(D) 중 '주식등의대량보유상황보고서' → 보고자가 VC·펀드·운용사이고
  지분율이 늘어난 것만(majorstock.json의 보유비율 증감 > 0).
모든 숫자는 채널·공시 원문 그대로이고, 채널 자료는 자동 생성이라 틀릴 수 있어요 → 원문 링크를 같이 보여줘요.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0 Safari/537.36", "Accept-Language": "ko-KR,ko;q=0.9"}

# (채널, 검색어) — 검색어마다 따로 받아서 합쳐요
TG_SOURCES = [
    ("maddingStock", "어닝 서프라이즈"),
    ("awake_schedule", "서프라이즈"),
    ("awake_schedule", "어닝"),
    ("awake_schedule", "잠정실적"),
]
TG_NAMES = {"maddingStock": "스탁이지", "awake_schedule": "AWAKE"}


def now_kst() -> datetime:
    return datetime.now(KST)


# ─────────────────────────── 텔레그램 ───────────────────────────
def _html_text(frag) -> str:
    """메시지 본문 HTML → 줄바꿈 살린 글자."""
    for br in frag.xpath(".//br"):
        br.tail = "\n" + (br.tail or "")
    return frag.text_content().replace("\xa0", " ")


def fetch_tg(channel: str, q: str | None = None, before: int | None = None, timeout: int = 10) -> list[dict]:
    """공개 채널 미리보기 한 쪽(보통 20개 안팎) → [{id, date(KST), text, url, channel}] (오래된 → 최근)."""
    from lxml import html as lh
    params = {}
    if q:
        params["q"] = q
    if before:
        params["before"] = before
    r = requests.get(f"https://t.me/s/{channel}", params=params, headers=UA, timeout=timeout)
    r.raise_for_status()
    doc = lh.fromstring(r.content)
    out = []
    for m in doc.xpath('//div[contains(@class,"tgme_widget_message ") and @data-post]'):
        post = m.get("data-post") or ""
        try:
            pid = int(post.rsplit("/", 1)[-1])
        except ValueError:
            continue
        body = m.xpath('.//div[contains(@class,"tgme_widget_message_text")]')
        if not body:
            continue
        t = m.xpath(".//time[@datetime]")
        try:
            dt = datetime.fromisoformat(t[0].get("datetime")).astimezone(KST) if t else None
        except ValueError:
            dt = None
        out.append({"id": pid, "date": dt, "text": _html_text(body[0]), "url": f"https://t.me/{post}",
                    "channel": channel})
    return sorted(out, key=lambda x: x["id"])


def fetch_tg_since(channel: str, q: str, since: date, max_pages: int = 8) -> tuple[list[dict], str | None]:
    """since 이후 게시물을 쪽을 넘겨 가며 모아요(검색 결과는 최근 → 오래된 순으로 before=로 넘겨요)."""
    posts, before, err = {}, None, None
    for _ in range(max_pages):
        try:
            page = fetch_tg(channel, q, before)
        except Exception as exc:  # noqa: BLE001
            err = f"{TG_NAMES.get(channel, channel)} '{q}' 받기 실패: {exc.__class__.__name__}"
            break
        if not page:
            break
        for p in page:
            posts[p["id"]] = p
        oldest = page[0]
        if oldest["date"] and oldest["date"].date() < since:
            break
        if before is not None and oldest["id"] >= before:
            break
        before = oldest["id"]
    return [p for p in posts.values() if not p["date"] or p["date"].date() >= since], err


_CODE = r"([0-9][0-9A-Z]{5})"
_ITEM = re.compile(r"([가-힣A-Za-z][가-힣A-Za-z0-9&·.\-]*(?: [가-힣A-Za-z0-9&·.\-]+){0,3}?)\s*\(\s*" + _CODE + r"\s*\)")
_NUM = r"([+\-−▲▼]?\s*[\d,]+(?:\.\d+)?)\s*(조|억|백만)?"
_OP = re.compile(r"영업(?:이익|익)[^\d\n+\-−▲▼]{0,12}" + _NUM)
_SALES = re.compile(r"매출(?:액)?[^\d\n+\-−▲▼]{0,12}" + _NUM)
_BEAT = re.compile(r"(?:예상비|예상 대비|컨센(?:서스)?\s*대비|컨센비|기대치\s*대비|시장\s*예상\s*대비)[^\d\n+\-−▲▼]{0,6}"
                   r"([+\-−▲▼]?\s*\d+(?:\.\d+)?)\s*%")
_CONS = re.compile(r"컨센(?:서스)?\s*[:：]?\s*" + _NUM)
_YOY = re.compile(r"(?:YoY|yoy|전년\s*대비|전년비)[^\d\n+\-−▲▼]{0,6}([+\-−▲▼]?\s*\d+(?:\.\d+)?)\s*%")
_TITLE_DATE = re.compile(r"(20\d{2})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})")
_QTR = re.compile(r"([1-4])\s*Q\s*(\d{2})|(\d{2})\s*\.?\s*([1-4])\s*Q|([1-4])\s*분기")


def _f(s: str | None) -> float | None:
    if not s:
        return None
    s = s.replace(",", "").replace(" ", "").replace("−", "-").replace("▲", "+").replace("▼", "-")
    try:
        return float(s)
    except ValueError:
        return None


def _won(num: str | None, unit: str | None) -> float | None:
    """'363', '억' → 363 (억원 단위)."""
    v = _f(num)
    if v is None:
        return None
    return v * 10000 if unit == "조" else (v / 100 if unit == "백만" else v)


def parse_surprises(post: dict) -> list[dict]:
    """게시물 하나 → 종목별 [{name, code, op, sales, beat, yoy, quarter, date, ...}]. 숫자를 못 찾은 종목은 빼요."""
    text = post["text"]
    m = _TITLE_DATE.search(text[:80])
    pdate = None
    if m:
        try:
            pdate = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pdate = None
    pdate = pdate or (post["date"].date() if post.get("date") else None)
    items = list(_ITEM.finditer(text))
    rows = []
    for k, it in enumerate(items):
        seg = text[it.end(): items[k + 1].start() if k + 1 < len(items) else len(text)][:400]
        op, sales, beat, cons, yoy = _OP.search(seg), _SALES.search(seg), _BEAT.search(seg), _CONS.search(seg), _YOY.search(seg)
        opv = _won(*op.groups()) if op else None
        beatv = _f(beat.group(1)) if beat else None
        consv = _won(*cons.groups()) if cons else None
        if beatv is None and opv is not None and consv and consv > 0:
            beatv = (opv / consv - 1) * 100
        if beatv is None:
            continue
        qm = _QTR.search(seg) or _QTR.search(text[:120])
        qtr = None
        if qm:
            if qm.group(1):
                qtr = f"{qm.group(1)}Q{qm.group(2)}"
            elif qm.group(3):
                qtr = f"{qm.group(4)}Q{qm.group(3)}"
            elif qm.group(5):
                qtr = f"{qm.group(5)}Q{(pdate or date.today()).year % 100:02d}"
        if not qtr and pdate:                       # 분기 표기가 없으면 발표 시기로: 10~11월 = 3분기
            qtr = {1: "4Q", 2: "4Q", 3: "4Q", 4: "1Q", 5: "1Q", 6: "1Q", 7: "2Q", 8: "2Q", 9: "2Q",
                   10: "3Q", 11: "3Q", 12: "3Q"}[pdate.month] + f"{(pdate.year - (1 if pdate.month <= 3 else 0)) % 100:02d}"
        nm = it.group(1).strip(" .-·")
        if it.start() > 0 and text[it.start() - 1] == "#" and " " in nm:   # '#어닝서프 LG이노텍' → 해시태그 낱말 빼기
            nm = nm.split(" ", 1)[1]
        rows.append({"date": pdate.isoformat() if pdate else None, "name": nm,
                     "code": it.group(2), "op": opv, "sales": _won(*sales.groups()) if sales else None,
                     "cons": consv, "beat": beatv, "yoy": _f(yoy.group(1)) if yoy else None, "quarter": qtr,
                     "src": TG_NAMES.get(post["channel"], post["channel"]), "url": post["url"], "post_id": post["id"]})
    return rows


def collect_surprises(since: date, sources=TG_SOURCES) -> tuple[list[dict], list[str]]:
    """여러 채널·검색어에서 어닝 서프라이즈를 모아 (종목, 발표일) 기준으로 합쳐요. 출처가 여러 곳이면 같이 적어요."""
    seen_posts, rows, errs = set(), [], []
    for ch, q in sources:
        posts, err = fetch_tg_since(ch, q, since)
        if err:
            errs.append(err)
        for p in posts:
            if (ch, p["id"]) in seen_posts:
                continue
            seen_posts.add((ch, p["id"]))
            rows += parse_surprises(p)
    merged: dict = {}
    for r in sorted(rows, key=lambda x: (x["date"] or "", x["src"] != "스탁이지")):
        key = (r["code"], r["quarter"])
        if key in merged:
            m = merged[key]
            if r["src"] not in m["src"]:
                m["src"] += " · " + r["src"]
                m["urls"].append(r["url"])
            for k in ("op", "sales", "cons", "yoy"):
                if m.get(k) is None and r.get(k) is not None:
                    m[k] = r[k]
        else:
            merged[key] = {**r, "urls": [r["url"]]}
    return sorted(merged.values(), key=lambda x: (x["date"] or "", x["beat"] or 0), reverse=True), errs


# ─────────────────────────── DART: VC·펀드 지분 확대 ───────────────────────────
DART = "https://opendart.fss.or.kr/api"
VC_WORDS = ("인베스트먼트", "벤처", "창업투자", "창투", "파트너스", "캐피탈", "자산운용", "투자조합", "조합", "펀드",
            "사모", "에쿼티", "PE", "프라이빗", "투자자문", "투자회사", "기술투자", "신기술", "Capital", "CAPITAL",
            "Partners", "PARTNERS", "Fund", "FUND", "Investment", "INVESTMENT", "Asset", "ASSET", "Management",
            "MANAGEMENT", "Ventures", "VENTURES", "Equity", "EQUITY", "LLC", "L.P", "LP")
NOT_VC = ("국민연금", "증권", "은행", "보험", "공제회")      # 연기금·금융회사는 따로 표시(기본은 VC·펀드만)


def is_vc(name: str) -> bool:
    n = name or ""
    return any(w in n for w in VC_WORDS) and not any(w in n for w in NOT_VC)


def _dart(path: str, key: str, timeout: int = 12, **params) -> dict:
    r = requests.get(f"{DART}/{path}", params={"crtfc_key": key, **params}, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r.json()


def dart_list(key: str, bgn: date, end: date, ty: str = "D", max_pages: int = 10) -> tuple[list[dict], str | None]:
    """공시 목록(코스피·코스닥만). 회사 지정 없이는 3개월까지만 검색돼요."""
    out, err = [], None
    for page in range(1, max_pages + 1):
        try:
            js = _dart("list.json", key, bgn_de=f"{bgn:%Y%m%d}", end_de=f"{end:%Y%m%d}", pblntf_ty=ty,
                       page_no=page, page_count=100, sort="date", sort_mth="desc")
        except Exception as exc:  # noqa: BLE001
            err = f"DART 목록 실패: {exc.__class__.__name__}"
            break
        st_ = js.get("status")
        if st_ == "013":                                  # 조회된 데이터 없음
            break
        if st_ != "000":
            err = f"DART: {js.get('message') or st_}"
            break
        out += [x for x in js.get("list", []) if x.get("corp_cls") in ("Y", "K") and x.get("stock_code")]
        if page >= int(js.get("total_page") or 1):
            break
    return out, err


def stake_increases(key: str, days: int = 30, only_vc: bool = True, max_corps: int = 150) -> tuple[list[dict], str | None]:
    """최근 days일 대량보유 보고 중 보유비율이 늘어난 것 [{date, name, code, reporter, ratio, ratio_chg, shares_chg, reason, url}]."""
    end = now_kst().date()
    lst, err = dart_list(key, end - timedelta(days=min(days, 90)), end, "D")
    lst = [x for x in lst if "대량보유" in (x.get("report_nm") or "")]
    if only_vc:
        lst = [x for x in lst if is_vc(x.get("flr_nm", ""))]
    want = {x["rcept_no"]: x for x in lst}
    corps = list(dict.fromkeys(x["corp_code"] for x in lst))[:max_corps]
    rows = []
    for cc in corps:
        try:
            js = _dart("majorstock.json", key, corp_code=cc)
        except Exception:  # noqa: BLE001
            continue
        for d in js.get("list", []) if js.get("status") == "000" else []:
            base = want.get(d.get("rcept_no"))
            if not base:
                continue
            chg = _f(d.get("stkrt_irds"))
            if chg is None or chg <= 0:
                continue
            rows.append({"date": f"{base['rcept_dt'][:4]}-{base['rcept_dt'][4:6]}-{base['rcept_dt'][6:]}",
                         "name": base["corp_name"], "code": base["stock_code"], "reporter": d.get("repror") or base["flr_nm"],
                         "ratio": _f(d.get("stkrt")), "ratio_chg": chg, "shares": _f(d.get("stkqy")),
                         "shares_chg": _f(d.get("stkqy_irds")), "reason": (d.get("report_resn") or "").strip()[:80],
                         "vc": is_vc(d.get("repror") or base["flr_nm"]),
                         "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={base['rcept_no']}"})
    return sorted(rows, key=lambda x: (x["date"], x["ratio_chg"]), reverse=True), err


def prelim_results(key: str, since: date) -> tuple[list[dict], str | None]:
    """DART 잠정실적(공정공시) 목록 — 텔레그램에 없는 종목도 '발표는 했는지' 확인용."""
    lst, err = dart_list(key, since, now_kst().date(), "I")
    rows = [{"date": f"{x['rcept_dt'][:4]}-{x['rcept_dt'][4:6]}-{x['rcept_dt'][6:]}", "name": x["corp_name"],
             "code": x["stock_code"], "title": x["report_nm"].strip(),
             "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={x['rcept_no']}"}
            for x in lst if "잠정" in (x.get("report_nm") or "") and "실적" in (x.get("report_nm") or "")]
    return rows, err
