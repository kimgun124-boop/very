"""💵 현금창출 기업 — '적게 투자하면서도 성장하고, 번 이익이 꾸준히 현금으로 쌓이는 기업' 스크리닝.

데이터: 밸류라인(valueline.co.kr) 연간 재무제표(연결 우선, 연결이 없으면 개별)
  - 손익계산서: 매출액·영업이익·이자비용
  - 현금흐름표: 영업활동현금흐름·유형자산의 증가·무형자산의 증가(= CAPEX)
  - 재무상태표: 현금·단기금융자산, 차입금(단기·장기·사채·유동성장기부채), 자본총계

이 파일을 직접 실행하면 보드의 국내 종목 전부를 받아 cashflow_screen.json 으로 저장해요.
    python cashflow.py            # 전부 새로 받기
    python cashflow.py 058470 ... # 몇 종목만 받아서 기존 파일에 덮어쓰기
GitHub Actions(.github/workflows/cashflow.yml)가 분기 실적 발표 뒤 자동으로 돌려서 파일을 갱신해요.
앱은 이 파일만 읽어서 계산하므로(네트워크 X) 가볍고, 기준값은 화면에서 바꿀 수 있어요.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

OUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cashflow_screen.json")
SRC = "밸류라인(valueline.co.kr) 연간 재무제표"
BASE = "https://valueline.co.kr/finance"

KEEP = {
    "income": {"매출액(수익)": "sales", "영업이익": "op", "이자비용": "int"},
    "cashflow": {"영업활동으로인한현금흐름": "ocf", "유형자산의증가": "capex_t", "무형자산의증가": "capex_i"},
    "balancesheet": {"현금및현금성자산": "cash", "단기금융자산": "stfin", "단기금융상품": "stfin2",
                     "단기차입금": "std", "장기차입금": "ltd", "사채": "bond", "유동성장기부채": "cltd",
                     "자본총계": "equity"},
}

# 금융·지주는 영업현금흐름·차입금 의미가 달라서 기본은 빼요(화면에서 켤 수 있음)
FIN_GROUPS = {"금융·증권", "지주"}
FIN_SECTORS = {"금융·지주"}
FIN_NAME_HINT = ("지주", "홀딩스", "금융", "증권", "생명", "화재", "투자", "인베스트", "캐피탈")


def kst_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=9)))


# ─────────────────────────── 받아오기 ───────────────────────────
def _num(s: str):
    s = (s or "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def parse_table(html_text: str) -> tuple[list[str], dict[str, list]]:
    """밸류라인 재무표 → (기간 머리글, {계정: [값...]}). 셀 안의 정밀값(.deci)을 써요."""
    from lxml import html as lh
    doc = lh.fromstring(html_text)
    for t in doc.xpath("//table"):
        rows = t.xpath(".//tr")
        if not rows:
            continue
        head = [c.text_content().strip() for c in rows[0].xpath("./th|./td")]
        if not any(re.match(r"^\d{4}/\d{2}", h) for h in head):
            continue
        out: dict[str, list] = {}
        for tr in rows[1:]:
            cells = tr.xpath("./th|./td")
            if not cells:
                continue
            k = cells[0].text_content().strip()
            if not k or k in out:
                continue
            vals = []
            for c in cells[1:len(head)]:
                d = c.xpath('.//*[contains(concat(" ", normalize-space(@class), " "), " deci ")]')
                vals.append(_num((d[0] if d else c).text_content()))
            out[k] = vals
        return head[1:], out
    return [], {}


def _session():
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
                      "Referer": "https://valueline.co.kr/"})
    return s


def fetch_page(sess, page: str, code: str, mc: str) -> tuple[list[str], dict[str, list]]:
    form = {"dimension": "mry", "bic_type": "", "view_all": "", "view_point": "", "view_big": "", "master_chk": mc}
    for attempt in range(3):
        try:
            r = sess.post(f"{BASE}/{page}/{code}", data=form, timeout=20)
            if r.status_code == 200:
                return parse_table(r.text)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1.5 * (attempt + 1))
    return [], {}


def fetch_one(code: str, sess=None) -> dict | None:
    """한 종목의 손익·현금흐름·재무상태 원자료. 연결(C) → 개별(P) 순서로 시도."""
    sess = sess or _session()
    for mc in ("C", "P"):
        head, inc = fetch_page(sess, "income", code, mc)
        sales = inc.get("매출액(수익)") or []
        if not head or not any(v for v in sales if v):
            continue
        rec = {"mc": mc, "periods": {}}

        def put(page_head, rows, keys):
            for i, p in enumerate(page_head):
                slot = rec["periods"].setdefault(p, {})
                for src, dst in keys.items():
                    arr = rows.get(src) or []
                    v = arr[i] if i < len(arr) else None
                    if v is not None:
                        slot[dst] = v

        put(head, inc, KEEP["income"])
        for page in ("cashflow", "balancesheet"):
            h, rows = fetch_page(sess, page, code, mc)
            put(h, rows, KEEP[page])
        return rec
    return None


def board_codes() -> list[dict]:
    import stocks
    seen, out = set(), []
    for s in stocks.STOCKS:
        c = str(s.get("code") or "")
        if re.fullmatch(r"\d{6}", c) and c not in seen:
            seen.add(c)
            out.append({"code": c, "name": s["name"], "sector": s["sector"], "group": s["group"]})
    return out


def is_fin(meta: dict) -> bool:
    return (meta.get("group") in FIN_GROUPS or meta.get("sector") in FIN_SECTORS
            or any(h in (meta.get("name") or "") for h in FIN_NAME_HINT))


def crawl(metas: list[dict], workers: int = 3, log=print) -> dict:
    out, done = {}, [0]

    def job(m):
        sess = _session()
        try:
            rec = fetch_one(m["code"], sess)
        except Exception as e:  # noqa: BLE001
            rec = None
            log(f"  ! {m['code']} {m['name']}: {e}")
        if rec:
            rec.update(name=m["name"], sector=m["sector"], group=m["group"], fin=is_fin(m))
            out[m["code"]] = rec
        done[0] += 1
        if done[0] % 25 == 0:
            log(f"  {done[0]}/{len(metas)}")
        time.sleep(0.2)

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(job, metas))
    return out


def save(items: dict, path: str | None = None):
    with open(path or OUT_FILE, "w", encoding="utf-8") as f:
        json.dump({"updated": kst_now().isoformat(timespec="minutes"), "source": SRC, "items": items},
                  f, ensure_ascii=False, separators=(",", ":"))


def load(path: str | None = None) -> dict:
    try:
        with open(path or OUT_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"updated": None, "source": SRC, "items": {}}


# ─────────────────────────── 계산 ───────────────────────────
DEFAULTS = {
    "cagr": 8.0,        # 매출 연평균 성장률(%) 최소
    "capex_s": 8.0,     # CAPEX/매출(%) 최대 — 4년 합산
    "capex_ocf": 60.0,  # CAPEX/영업현금흐름(%) 최대 — 4년 합산
    "de": 30.0,         # 차입금/자본(%) 최대 (순현금이면 통과)
    "icr": 10.0,        # 이자보상배율(배) 최소
}


def _annual_keys(periods: dict) -> list[str]:
    return sorted([p for p in periods if p.endswith("/12")])[-4:]


def _partial_key(periods: dict, annual: list[str]) -> str | None:
    last = annual[-1] if annual else ""
    cand = sorted([p for p in periods if not p.endswith("/12") and p > last])
    return cand[-1] if cand else None


def metrics(code: str, rec: dict) -> dict | None:
    P = rec.get("periods") or {}
    ys = _annual_keys(P)
    if len(ys) < 3:
        return None

    def col(k):
        return [P[y].get(k) for y in ys]

    sales, op, intr, ocf = col("sales"), col("op"), col("int"), col("ocf")
    capex = [None if (P[y].get("capex_t") is None and P[y].get("capex_i") is None)
             else (P[y].get("capex_t") or 0) + (P[y].get("capex_i") or 0) for y in ys]
    fcf = [None if o is None or c is None else o - c for o, c in zip(ocf, capex)]
    bkey = max(P)                       # 가장 최근 재무상태(반기·분기 포함)
    b = P[bkey]
    cash = (b.get("cash") or 0) + (b.get("stfin") or 0) + (b.get("stfin2") or 0)
    debt = sum(b.get(k) or 0 for k in ("std", "ltd", "bond", "cltd"))
    eq = b.get("equity")
    pk = _partial_key(P, ys)
    part = P.get(pk) or {}
    p_capex = (part.get("capex_t") or 0) + (part.get("capex_i") or 0)
    ok = lambda v: v is not None  # noqa: E731
    n = len(ys)
    cagr = None
    if ok(sales[0]) and ok(sales[-1]) and sales[0] > 0 and sales[-1] > 0:
        cagr = ((sales[-1] / sales[0]) ** (1 / (n - 1)) - 1) * 100
    s_sum = sum(v for v in sales if ok(v)) or None
    c_sum = sum(v for v in capex if ok(v))
    o_sum = sum(v for v in ocf if ok(v))
    fv = [v for v in fcf if ok(v)]
    half = len(fv) // 2
    fcf_trend = (sum(fv[half:]) / max(1, len(fv) - half) - sum(fv[:half]) / max(1, half)) if len(fv) >= 2 else None
    months = int(pk[-2:]) if pk else 0
    return {
        "code": code, "name": rec.get("name"), "sector": rec.get("sector"), "group": rec.get("group"),
        "fin": bool(rec.get("fin")), "mc": rec.get("mc"), "years": [y[:4] for y in ys],
        "sales": sales, "op": op, "ocf": ocf, "capex": capex, "fcf": fcf, "int": intr,
        "cagr": cagr, "g_last": (sales[-1] / sales[-2] - 1) * 100 if ok(sales[-1]) and ok(sales[-2]) and sales[-2] else None,
        "fcf_pos": sum(1 for v in fcf if ok(v) and v > 0), "fcf_n": len(fv), "fcf_trend": fcf_trend,
        "capex_s": c_sum / s_sum * 100 if s_sum else None,
        "capex_ocf": c_sum / o_sum * 100 if o_sum > 0 else None,
        "op_margin": op[-1] / sales[-1] * 100 if ok(op[-1]) and ok(sales[-1]) and sales[-1] else None,
        "fcf_margin": fcf[-1] / sales[-1] * 100 if ok(fcf[-1]) and ok(sales[-1]) and sales[-1] else None,
        "cash": cash, "debt": debt, "net_cash": cash - debt, "equity": eq,
        "de": debt / eq * 100 if eq and eq > 0 else None,
        "icr": (op[-1] / intr[-1]) if ok(op[-1]) and ok(intr[-1]) and intr[-1] > 0 else (999.0 if ok(op[-1]) and op[-1] > 0 else None),
        "bs_at": bkey,
        "part": pk, "part_months": months,
        "p_sales": part.get("sales"), "p_op": part.get("op"),
        "p_fcf": (part["ocf"] - p_capex) if part.get("ocf") is not None else None,
        "p_growth": ((part["sales"] * 12 / months) / sales[-1] - 1) * 100
        if part.get("sales") and months and ok(sales[-1]) and sales[-1] > 0 else None,
    }


def checks(m: dict, th: dict | None = None) -> dict:
    """기준별 통과 여부. True/False/None(자료 부족)."""
    th = {**DEFAULTS, **(th or {})}
    fcf, n = m["fcf"], m["fcf_n"]
    good = lambda v: v is not None  # noqa: E731
    c = {
        "성장": None if m["cagr"] is None else (m["cagr"] >= th["cagr"] and (m["g_last"] is None or m["g_last"] >= 0)),
        "저CAPEX": None if m["capex_s"] is None else (m["capex_s"] <= th["capex_s"]
                                                    and (m["capex_ocf"] is not None and m["capex_ocf"] <= th["capex_ocf"])),
        "FCF 연속+": None if n < 3 else (m["fcf_pos"] == n),
        "FCF 증가": None if m["fcf_trend"] is None else (m["fcf_trend"] > 0 and good(fcf[-1]) and good(fcf[0]) and fcf[-1] >= fcf[0]),
        "재무": (m["net_cash"] > 0 or (m["de"] is not None and m["de"] <= th["de"]))
                and (m["icr"] is None or m["icr"] >= th["icr"]),
    }
    return c


def grade(m: dict, th: dict | None = None) -> tuple[str, int, dict]:
    c = checks(m, th)
    n_ok = sum(1 for v in c.values() if v is True)
    if n_ok == 5:
        g = "1군"
    elif n_ok == 4 and m["fcf"][-1] is not None and m["fcf"][-1] > 0:
        g = "2군"
    elif c["성장"] and (m["fcf"][-1] is not None and m["fcf"][-1] <= 0 or c["저CAPEX"] is False):
        g = "함정"          # 성장은 좋은데 현금이 안 남거나 투자 부담이 큰 곳
    else:
        g = "-"
    return g, n_ok, c


if __name__ == "__main__":
    metas = board_codes()
    only = [a for a in sys.argv[1:] if re.fullmatch(r"\d{6}", a)]
    if only:
        metas = [m for m in metas if m["code"] in only] or [{"code": c, "name": c, "sector": "", "group": ""} for c in only]
    print(f"[{kst_now():%Y-%m-%d %H:%M}] {len(metas)}종목 받는 중 …")
    got = crawl(metas)
    prev = load().get("items", {})
    if only:
        got = {**prev, **got}
    if len(got) < (len(metas) * 0.5 if not only else 1):
        print(f"받은 종목이 너무 적어요({len(got)}) — 파일을 바꾸지 않아요.")
        sys.exit(1)
    if got == prev:
        print("바뀐 숫자가 없어요 — 파일을 그대로 둬요.")
        sys.exit(0)
    save(got)
    print(f"저장: {OUT_FILE} · {len(got)}종목")
