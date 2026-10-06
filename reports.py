"""앱에서 직접 넣은 리포트를 읽어 종목을 뽑고, 저장하고, 보드 종목에 붙여요.

- 읽기: PDF(글자), 텍스트, 붙여넣은 글. 이미지·스캔 PDF는 AI 분석에서만 읽을 수 있어요.
- 종목 뽑기: ① AI(Claude API 키가 있을 때) ② 이름 맞추기(키가 없을 때, 상장사 이름이 본문에 나오면 찾아요)
- 저장: user_reports.json. GitHub 토큰이 있으면 저장소에 바로 저장해서 앱을 다시 켜도 남아요.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

import requests

REPORTS_VERSION = "2026-10-06-autolearn"
REPORTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_reports.json")
KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0"}
KIND_URL = "https://kind.krx.co.kr/corpgeneral/corpList.do"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5-5"
# 모델 이름이 바뀌어 404가 나면 차례로 시도해요.
FALLBACK_MODELS = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-4-5-20251001")
RECENT_DAYS = 30          # 이 기간 안에 넣은 리포트는 리포트별 태그도 보여줘요. 지난 건 테마 태그로만 묶여요.


def now_kst() -> datetime:
    return datetime.now(KST)


# ─────────────────────────── 저장 · 불러오기 ───────────────────────────
def empty_store() -> dict:
    return {"reports": [], "learned": {}, "themes": {}}


def load_local() -> dict:
    try:
        with open(REPORTS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        if not (isinstance(d, dict) and isinstance(d.get("reports"), list)):
            return empty_store()
        d.setdefault("learned", {})
        d.setdefault("themes", {})
        return d
    except (OSError, ValueError):
        return empty_store()


def save_local(store: dict) -> None:
    with open(REPORTS_FILE, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=1)


def _gh_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def save_github(store: dict, token: str, repo: str, branch: str | None = None,
                path: str = "user_reports.json") -> tuple[bool, str]:
    """GitHub 저장소에 user_reports.json을 올려요(있으면 덮어쓰기). (성공 여부, 메시지)"""
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    try:
        params = {"ref": branch} if branch else None
        r = requests.get(url, headers=_gh_headers(token), params=params, timeout=15)
        sha = r.json().get("sha") if r.status_code == 200 else None
        body = {"message": f"리포트 저장 ({now_kst():%m/%d %H:%M})",
                "content": base64.b64encode(json.dumps(store, ensure_ascii=False, indent=1).encode("utf-8")).decode()}
        if sha:
            body["sha"] = sha
        if branch:
            body["branch"] = branch
        r = requests.put(url, headers=_gh_headers(token), json=body, timeout=20)
        if r.status_code in (200, 201):
            return True, "GitHub 저장소에 저장했어요."
        msg = (r.json() or {}).get("message", "") if r.headers.get("content-type", "").startswith("application/json") else ""
        return False, f"GitHub 저장 실패({r.status_code}) {msg}".strip()
    except (requests.RequestException, ValueError) as exc:
        return False, f"GitHub 연결 실패: {exc.__class__.__name__}"


def fetch_github_json(path: str, token: str, repo: str, branch: str | None = None):
    """GitHub 저장소에 저장된 JSON 파일(user_holdings.json 등)을 받아와요. 없거나 실패하면 None.
    앱이 켜질 때 가장 최근에 저장된 보유 종목·비밀번호·리포트를 쓰기 위해서예요."""
    try:
        r = requests.get(f"https://api.github.com/repos/{repo}/contents/{path}",
                         headers={**_gh_headers(token), "Accept": "application/vnd.github.raw+json"},
                         params={"ref": branch} if branch else None, timeout=15)
        if r.status_code != 200:
            return None
        return json.loads(r.content.decode("utf-8"))
    except (requests.RequestException, ValueError):
        return None


def new_report(title: str, broker: str, date: str, summary: str, stocks: list[dict], source: str,
               theme: str = "") -> dict:
    return {"id": uuid.uuid4().hex[:10], "title": title.strip(), "broker": broker.strip(), "date": date.strip(),
            "summary": summary.strip(), "source": source, "added_at": now_kst().strftime("%Y-%m-%d %H:%M"),
            "theme": (theme or "").strip(), "stocks": stocks}


def theme_tag(theme: str) -> str:
    return f"📥 테마 · {theme.strip()}"


def _is_recent(rep: dict, days: int = RECENT_DAYS) -> bool:
    try:
        t = datetime.strptime(rep.get("added_at", "")[:10], "%Y-%m-%d").replace(tzinfo=KST)
        return now_kst() - t <= timedelta(days=days)
    except ValueError:
        return True


def tag_name(rep: dict) -> str:
    """보드의 '리포트 태그'에 쓰일 이름."""
    head = f"{rep.get('broker', '').strip()} " if rep.get("broker") else ""
    tail = f"({rep['date']})" if rep.get("date") else ""
    return f"📥 {head}{rep.get('title', '리포트')}{tail}".strip()


# ─────────────────────────── 보드 종목에 붙이기 ───────────────────────────
def merge(stocks: list[dict], tags: dict, store: dict, fallback_sector: str = "기타(리포트 스크린)"):
    """앱에서 넣은 리포트 종목을 보드에 합쳐요. 원본 목록은 건드리지 않고 새 목록을 돌려줘요.
    이미 있는 종목은 태그·메모만 붙이고, 없는 종목은 새로 추가해요."""
    out = [dict(s) for s in stocks]
    by_code = {s["code"]: s for s in out if s.get("code")}
    tags = {k: list(v) for k, v in tags.items()}
    for rep in store.get("reports", []):
        # 자동 정리: 최근 리포트는 리포트별 태그 + 테마 태그, 지난 리포트는 테마 태그로만 묶어요(태그 목록이 끝없이 늘지 않게).
        tname = tag_name(rep)
        rep_tags = ([tname] if (_is_recent(rep) or not rep.get("theme")) else []) + \
                   ([theme_tag(rep["theme"])] if rep.get("theme") else [])
        codes = []
        for x in rep.get("stocks", []):
            code = str(x.get("code") or "").strip().upper()
            if not code:
                continue
            if re.fullmatch(r"\d{1,6}", code):
                code = code.zfill(6)
            codes.append(code)
            note = f"[{tname.replace('📥 ', '')}] {x['point']}" if x.get("point") else None
            if code in by_code:
                s = by_code[code]
                have = list(s.get("tags") or [])
                s["tags"] = have + [t for t in rep_tags if t not in have]
                if note and note not in (s.get("notes") or []):
                    s["notes"] = list(s.get("notes") or []) + [note]
            else:
                s = {"sector": x.get("sector") or fallback_sector, "group": x.get("group") or "리포트 추가",
                     "name": x.get("name") or code, "code": code, "desc": x.get("desc") or "",
                     "tags": list(rep_tags), "notes": [note] if note else []}
                out.append(s)
                by_code[code] = s
        if codes:
            for t in rep_tags:
                tags[t] = list(dict.fromkeys(tags.get(t, []) + codes))
    return out, tags


# ─────────────────────────── 자동 분류 · 학습 ───────────────────────────
# AI가 준 산업 이름을 보드 산업으로 맞춰요(앞에 있는 낱말이 먼저 맞으면 그 산업).
SECTOR_SYNONYMS = [
    ("반도체", ["반도체", "HBM", "파운드리", "메모리", "후공정", "전공정", "OSAT", "기판", "팹리스"]),
    ("2차전지", ["2차전지", "이차전지", "배터리", "양극재", "음극재", "전해질", "분리막", "리튬이온", "슈퍼캡", "연료전지 부품"]),
    ("데이터센터 전력", ["데이터센터", "AIDC", "IDC", "온사이트", "Co-location", "코로케이션"]),
    ("전력·에너지", ["전력", "변압기", "전선", "원전", "SMR", "원자력", "에너지", "가스", "정유", "수소", "연료전지", "태양광", "ESS"]),
    ("풍력", ["풍력", "타워", "하부구조물"]),
    ("조선", ["조선", "해운", "LNG선", "기자재"]),
    ("우주 데이터센터", ["우주", "위성", "발사체", "항공우주"]),
    ("산업재", ["방산", "기계", "로봇", "산업재", "상사", "자동차", "철강"]),
    ("바이오·헬스케어", ["바이오", "제약", "헬스", "의료", "의약"]),
    ("화장품", ["화장품", "뷰티", "코스메틱", "ODM"]),
    ("건설", ["건설", "건자재", "인프라", "시멘트"]),
    ("AI·IT", ["AI", "소프트웨어", "인터넷", "플랫폼", "보안", "양자", "통신", "게임", "클라우드", "IT"]),
    ("OLED", ["OLED", "디스플레이"]),
    ("금융·지주", ["금융", "지주", "은행", "증권", "보험"]),
    ("전략광물", ["희토류", "광물", "리튬", "니켈"]),
]


def map_sector(hint: str, sector_order: list[str], fallback: str = "기타(리포트 스크린)") -> str:
    hint = (hint or "").strip()
    if not hint:
        return fallback
    if hint in sector_order:
        return hint
    low = hint.lower()
    for sec, words in SECTOR_SYNONYMS:
        if sec in sector_order and any(w.lower() in low for w in words):
            return sec
    return fallback


def map_group(group: str, sector: str, board: list[dict], group_order: list[str]) -> str:
    """AI가 준 세부 분류를 보드에 이미 있는 분류에 맞춰요. 같은 산업 안의 분류 이름과 겹치면 그 분류로."""
    g = (group or "").strip()
    if not g:
        return "리포트 추가"
    if g in group_order:
        return g
    same = sorted({s["group"] for s in board if s.get("sector") == sector and s.get("group")})
    toks = [t for t in re.split(r"[\s·/,()]+", g) if len(t) >= 2]
    best, score = None, 0
    for cand in same:
        sc = sum(1 for t in toks if t in cand)
        if sc > score:
            best, score = cand, sc
    return best if best else g


def classify(row: dict, code: str, board_by_code: dict, learned: dict, themes: dict, theme: str,
             board: list[dict], sector_order: list[str], group_order: list[str]) -> tuple[str, str, str]:
    """(산업, 세부 분류, 한 줄 설명). 보드에 있으면 그대로 → 전에 배운 분류 → AI 힌트 → 같은 테마에서 배운 산업."""
    on = board_by_code.get(code)
    if on:
        return on["sector"], on["group"], on.get("desc", "")
    lv = learned.get(code)
    if lv and lv.get("sector") in sector_order:
        return lv["sector"], lv.get("group") or "리포트 추가", lv.get("desc") or row.get("desc") or ""
    sector = map_sector(row.get("sector_hint") or row.get("sector") or "", sector_order, "")
    if not sector and theme and themes.get(theme, {}).get("sector") in sector_order:
        sector = themes[theme]["sector"]
    sector = sector or "기타(리포트 스크린)"
    return sector, map_group(row.get("group") or "", sector, board, group_order), row.get("desc") or ""


def learn(store: dict, rep: dict) -> None:
    """넣은 리포트에서 종목 분류·설명·테마를 배워 둬요. 다음 리포트에서 같은 종목이 나오면 같은 자리로 들어가요."""
    learned = store.setdefault("learned", {})
    for x in rep.get("stocks", []):
        code = x.get("code")
        if not code:
            continue
        d = learned.setdefault(code, {"seen": 0})
        d.update({k: x[k] for k in ("name", "sector", "group", "desc") if x.get(k)})
        d["seen"] = int(d.get("seen", 0)) + 1
        d["last"] = rep.get("date") or rep.get("added_at", "")[:10]
    th = rep.get("theme")
    if th:
        t = store.setdefault("themes", {}).setdefault(th, {"reports": 0, "sectors": {}})
        t["reports"] = int(t.get("reports", 0)) + 1
        for x in rep.get("stocks", []):
            sec = x.get("sector")
            if sec and not sec.startswith("기타"):
                t["sectors"][sec] = int(t["sectors"].get(sec, 0)) + 1
        if t["sectors"]:
            t["sector"] = max(t["sectors"].items(), key=lambda kv: kv[1])[0]


def _norm(t: str) -> str:
    return re.sub(r"[\s\W_]+", "", (t or "").lower())


def upsert(store: dict, rep: dict) -> str:
    """같은 리포트(제목·출처·날짜가 같거나 같은 파일)를 또 넣으면 새로 쌓지 않고 바꿔 끼워요. 'added' / 'updated'"""
    key = (_norm(rep.get("title")), _norm(rep.get("broker")), _norm(rep.get("date")))
    for i, r in enumerate(store.get("reports", [])):
        same_src = rep.get("source") and r.get("source") == rep.get("source") and rep["source"] != "붙여넣은 글"
        if same_src or (key[0] and (_norm(r.get("title")), _norm(r.get("broker")), _norm(r.get("date"))) == key):
            rep["id"] = r.get("id", rep["id"])
            store["reports"][i] = rep
            return "updated"
    store.setdefault("reports", []).append(rep)
    return "added"


def known_themes(store: dict, limit: int = 40) -> list[str]:
    th = store.get("themes", {})
    return [k for k, _ in sorted(th.items(), key=lambda kv: -int(kv[1].get("reports", 0)))][:limit]


# ─────────────────────────── 글자 뽑기 ───────────────────────────
def pdf_text(raw: bytes, max_pages: int = 80) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw))
        return "\n".join((p.extract_text() or "") for p in reader.pages[:max_pages])
    except Exception:
        return ""


def file_text(name: str, raw: bytes) -> str:
    low = name.lower()
    if low.endswith(".pdf"):
        return pdf_text(raw)
    if low.endswith((".txt", ".md", ".csv")):
        for enc in ("utf-8", "cp949"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
    if low.endswith((".html", ".htm")):
        t = raw.decode("utf-8", errors="ignore")
        return re.sub(r"<[^>]+>", " ", t)
    return ""


# ─────────────────────────── 이름 맞추기(키 없을 때) ───────────────────────────
_listing_cache: dict = {"t": 0.0, "rows": []}


def krx_listing() -> list[dict]:
    """상장사 전체 [{name, code, industry, product}] (한국거래소 KIND, 하루 기억)."""
    if _listing_cache["rows"] and time.time() - _listing_cache["t"] < 86400:
        return _listing_cache["rows"]
    rows = []
    try:
        r = requests.get(KIND_URL, params={"method": "download", "searchType": "13"}, headers=UA, timeout=20)
        r.raise_for_status()
        text = r.content.decode("cp949", errors="ignore")
        for tr in re.findall(r"<tr>([\s\S]*?)</tr>", text):
            tds = [re.sub(r"<[^>]+>", "", t).strip() for t in re.findall(r"<td[^>]*>([\s\S]*?)</td>", tr)]
            if len(tds) >= 4 and re.fullmatch(r"[0-9A-Z]{1,6}", tds[2] or ""):
                rows.append({"name": tds[0], "code": tds[2].zfill(6), "industry": tds[3],
                             "product": tds[4] if len(tds) > 4 else ""})
    except requests.RequestException:
        pass
    if rows:
        _listing_cache.update(t=time.time(), rows=rows)
    return rows


_AMBIGUOUS = {"대상", "한국", "신세계", "이마트", "동방", "대교", "성안", "조선", "하나", "삼성", "현대", "우리", "보성",
              "SK", "LG", "GS", "CJ", "KT", "LS", "HD", "DB", "OCI", "진도", "태양", "남성", "동원", "서울", "부산", "경남"}


def match_stocks(text: str, board: list[dict], listing: list[dict] | None = None, limit: int = 60) -> list[dict]:
    """본문에 이름이 나온 상장사를 찾아요. 많이 나온 순. 설명은 이름이 처음 나온 문장."""
    if not text.strip():
        return []
    pool: dict[str, dict] = {}
    for s in board:
        if s.get("name") and s.get("code"):
            pool.setdefault(s["name"], {"name": s["name"], "code": s["code"], "desc": s.get("desc", "")})
    for x in listing or []:
        pool.setdefault(x["name"], {"name": x["name"], "code": x["code"],
                                    "desc": x.get("product") or x.get("industry") or ""})
    flat = re.sub(r"\s+", " ", text)
    found = []
    for name, info in pool.items():
        if len(name) < 2 or name in _AMBIGUOUS:
            continue
        if len(name) == 2 and not re.search(r"[가-힣]{2}", name):
            continue
        pat = re.compile(r"(?<![가-힣A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])")
        hits = pat.findall(flat)
        if not hits:
            continue
        m = pat.search(flat)
        a = max(0, flat.rfind(".", 0, m.start()) + 1)
        b = flat.find(".", m.end())
        sent = flat[a:b if b != -1 else m.end() + 120].strip()
        found.append({**info, "count": len(hits), "point": sent[:140]})
    found.sort(key=lambda x: (-x["count"], x["name"]))
    return found[:limit]


# ─────────────────────────── AI 분석(Claude API) ───────────────────────────
AI_PROMPT = """너는 증권 리포트를 정리하는 애널리스트 보조야. 첨부한 리포트(또는 글)에서 언급된 상장 종목을 빠짐없이 뽑아.
반드시 아래 JSON 하나만 출력해. 설명 문장·마크다운·코드블록 없이.
{
 "title": "리포트 제목(짧게, 30자 이내)",
 "theme": "리포트의 큰 투자 테마 한 단어~짧은 구(예: AIDC 전력, HBM, 원전, 우주항공, 보안). 아래 '이미 있는 테마'와 같은 주제면 그 이름을 그대로 써",
 "broker": "증권사·출처(모르면 빈칸)",
 "date": "YY.MM.DD 형식 발간일(모르면 빈칸)",
 "summary": "리포트 핵심 2~3문장",
 "stocks": [
  {"name": "종목명(한국 종목은 한국거래소 정식 이름)",
   "code": "한국 종목은 6자리 종목코드, 해외 종목은 야후 파이낸스 티커(예: NVDA, 6857.T). 모르면 빈칸",
   "market": "KR 또는 US 또는 JP 등",
   "sector_hint": "산업(예: 반도체, 2차전지, 조선, 전력·에너지, 바이오·헬스케어, AI·IT, 화장품, 건설, 금융·지주, 산업재)",
   "group": "리포트 기준 세부 분류(예: HBM 장비, 변압기, 원전)",
   "desc": "이 회사가 무엇을 하는지 한 줄(40자 이내)",
   "point": "이 리포트에서 이 종목에 대해 말한 핵심(목표가·투자의견·수혜 이유 등, 80자 이내)",
   "importance": "상(리포트의 주인공) / 중 / 하(잠깐 언급)"}
 ]
}
표·차트 속 종목도 포함해. 같은 종목은 한 번만. 비상장·지수·ETF는 빼.
sector_hint는 가능하면 아래 '보드 산업' 중 하나를 그대로 고르고, group은 '보드 세부 분류' 중 맞는 게 있으면 그대로 써."""


def ai_extract(api_key: str, files: list[tuple[str, bytes]], text: str = "", model: str | None = None,
               timeout: int = 240, sectors: list[str] | None = None, groups: list[str] | None = None,
               themes: list[str] | None = None) -> tuple[dict | None, str]:
    """Claude API로 리포트를 읽고 종목을 뽑아요. (결과, 오류메시지)"""
    content = []
    for name, raw in files:
        low = name.lower()
        b64 = base64.b64encode(raw).decode()
        if low.endswith(".pdf"):
            content.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64}})
        elif low.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
            mt = "image/jpeg" if low.endswith((".jpg", ".jpeg")) else f"image/{low.rsplit('.', 1)[-1]}"
            content.append({"type": "image", "source": {"type": "base64", "media_type": mt, "data": b64}})
        else:
            t = file_text(name, raw)
            if t.strip():
                content.append({"type": "text", "text": f"[파일 {name}]\n{t[:150000]}"})
    if text.strip():
        content.append({"type": "text", "text": f"[붙여넣은 글]\n{text[:150000]}"})
    if not content:
        return None, "읽을 내용이 없어요."
    prompt = AI_PROMPT
    if sectors:
        prompt += "\n\n보드 산업: " + ", ".join(sectors)
    if groups:
        prompt += "\n보드 세부 분류: " + ", ".join(groups)
    if themes:
        prompt += "\n이미 있는 테마: " + ", ".join(themes)
    content.append({"type": "text", "text": prompt})
    tried = []
    for mdl in dict.fromkeys([model or DEFAULT_MODEL, *FALLBACK_MODELS]):
        tried.append(mdl)
        try:
            r = requests.post(ANTHROPIC_URL, timeout=timeout, headers={
                "x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": mdl, "max_tokens": 12000, "messages": [{"role": "user", "content": content}]})
        except requests.RequestException as exc:
            return None, f"AI 연결 실패: {exc.__class__.__name__}"
        if r.status_code == 404 or (r.status_code == 400 and "model" in r.text.lower() and "not" in r.text.lower()):
            continue                      # 모델 이름이 없으면 다음 모델로
        break
    if r.status_code != 200:
        try:
            msg = r.json().get("error", {}).get("message", "")
        except ValueError:
            msg = r.text[:200]
        return None, f"AI 분석 실패({r.status_code}): {msg}"
    out = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
    out = re.sub(r"```(?:json)?", "", out).strip()
    m = re.search(r"\{[\s\S]*\}", out)
    if not m:
        return None, "AI 응답에서 결과를 못 찾았어요. 다시 시도해 보세요."
    try:
        return json.loads(m.group(0)), ""
    except ValueError:
        return None, "AI 응답 형식이 깨졌어요. 다시 시도해 보세요."
