"""계좌 종목 관리 앱 — 계산·저장·데이터 모듈 (화면과 분리)"""
from __future__ import annotations

import base64
import json
import math
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from io import StringIO

import numpy as np
import pandas as pd
import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

DEFAULT_SETTINGS = {
    "equity": 53_000_000,   # 초기 자본금 = 기준 시점 계좌 총액(원)
    "risk_pct": 1.5,        # 1회 매매 위험 (계좌 대비 %)
    "stop_pct": 8.0,        # 1R 손절폭 (%)
    "max_positions": 8,     # 최대 보유 종목 수
    "target_r": 3.0,        # 절반 익절 R
    "breakeven_r": 2.0,     # 본전 손절가 이동 R
    "fx": 1400.0,           # 환율 불러오기 실패 시 사용
    "start_date": "",       # 계좌 기준일 (비우면 첫 매매일)
    "unit_pct": 7.5,        # 1R(1유닛) 비중 — 계좌의 7~8%. 이보다 작으면 정찰병
}


def empty_book() -> dict:
    return {"settings": dict(DEFAULT_SETTINGS), "holdings": [], "snapshots": [], "updated": ""}


def new_holding(code: str, name: str, market: str, sector: str = "") -> dict:
    return {
        "id": uuid.uuid4().hex[:8],
        "code": code.strip().upper(),
        "name": name.strip(),
        "market": market,          # "KR" or "US"
        "index": "KOSPI" if market == "KR" else "NASDAQ",   # 비교 지수
        "sector": sector.strip(),
        "trades": [],              # {date, side(buy/sell), price, qty, memo}
        "stop": None,              # 직접 정한 손절가 (없으면 평단 × (1-손절%))
        "trend_ma": "자동",
        "atr_pct": None,           # 종목별 ATR% (손절률 = MAX(기본 손절폭, ATR%))
        "atr_src": None,           # "auto"(진입일 ATR20 자동) / "manual"(직접) / "stop"(직접 손절가에서 계산)
        "target_fixed": None,      # 익절가(3R) — 첫 매수 때 정하고 추가 매수해도 그대로
        "stop_floor": None,        # 손절가 하한 — 물타기로 평단이 내려가도 손절가는 안 내려감
        "stop_log": [],            # 손절가 변경 기록        # 추세 기준선: 자동/5/20/50
        "fund_override": None,     # 영업이익 증가 수동 판단: None/True/False
        "memo": "",
        "created": date.today().isoformat(),
    }


# ─────────────────────────── 저장소 ───────────────────────────
STORE_VERSION = 11   # 저장 코드를 고칠 때마다 올림 → 앱이 옛 저장 객체를 버리고 새로 만듦
class Store:
    """GitHub 비공개 저장소에 JSON으로 저장.

    - 앱 코드는 main 브랜치, 종목 데이터는 별도 'data' 브랜치에 저장한다.
      → 앱 파일을 고쳐 올려도 데이터가 덮이지 않고, 저장할 때마다 앱이 재배포되지도 않는다.
    - 저장할 때마다 커밋이 남아서 GitHub 기록에서 언제든 되돌릴 수 있다.
    - GitHub 설정이 없으면 로컬 파일(앱이 재시작되면 사라짐)에 저장한다.
    """

    def __init__(self, gh: dict | None, local_path: str = "data/portfolio.json"):
        self.gh = gh if gh and gh.get("token") and gh.get("repo") else None
        self.local_path = local_path
        self.sha = None
        self.info = {"loaded_from": "-", "loaded_count": None, "saved_to": "-", "saved_count": None,
                     "verified": None}
        if self.gh:
            self.code_branch = self.gh.get("branch", "main")
            self.data_branch = self.gh.get("data_branch", "data")
            self.path = self.gh.get("path", "data/portfolio.json")

    @property
    def mode(self) -> str:
        return "GitHub" if self.gh else "임시 (로컬 파일)"

    @property
    def persistent(self) -> bool:
        return bool(self.gh)

    def _api(self, tail: str) -> str:
        base = f"https://api.github.com/repos/{self.gh['repo']}"
        return f"{base}/{tail}" if tail else base

    def _headers(self):
        return {"Authorization": f"Bearer {self.gh['token']}", "Accept": "application/vnd.github+json",
                "Cache-Control": "no-cache"}   # 방금 저장한 내용을 옛 캐시로 읽지 않도록

    # 연결 점검
    def check(self) -> tuple[bool, str]:
        if not self.gh:
            return False, "Secrets에 [github] 설정이 없어 임시 저장 중이에요. 앱을 고치거나 재시작하면 기록이 사라져요."
        try:
            r = requests.get(self._api(""), headers=self._headers(), timeout=10)
        except Exception as e:
            return False, f"GitHub에 연결하지 못했어요 ({type(e).__name__})."
        if r.status_code == 401:
            return False, "GitHub 토큰이 틀렸거나 만료됐어요. 새 토큰을 만들어 Secrets의 token을 바꿔 주세요."
        if r.status_code == 404:
            return False, f"저장소 {self.gh['repo']}를 찾지 못했어요. 토큰에 이 저장소 접근 권한이 있는지 확인해 주세요."
        if not r.ok:
            return False, f"GitHub 응답 오류 {r.status_code}."
        perm = r.json().get("permissions") or {}
        if perm and not perm.get("push", False):
            return False, "토큰에 쓰기 권한이 없어요. Contents 권한을 Read and write로 바꿔 주세요."
        return True, f"GitHub 저장 · {self.gh['repo']} ({self.data_branch} 브랜치)"

    def _head(self, branch: str) -> str | None:
        """브랜치의 최신 커밋 sha (캐시를 피하려고 매번 다른 주소로 요청)."""
        r = requests.get(self._api(f"git/ref/heads/{branch}"), headers=self._headers(),
                         params={"_": time.time_ns()}, timeout=10)
        return r.json()["object"]["sha"] if r.ok else None

    def _get_file(self, branch: str):
        # 브랜치 이름 대신 최신 커밋 sha로 읽어야 GitHub 캐시 때문에 옛 파일(옛 sha)을 받지 않음 → 409 방지
        ref = self._head(branch) or branch
        r = requests.get(self._api(f"contents/{self.path}"), headers=self._headers(),
                         params={"ref": ref, "_": time.time_ns()}, timeout=15)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def _ensure_data_branch(self):
        r = requests.get(self._api(f"git/ref/heads/{self.data_branch}"), headers=self._headers(), timeout=10)
        if r.status_code == 200:
            return
        base = requests.get(self._api(f"git/ref/heads/{self.code_branch}"), headers=self._headers(), timeout=10)
        base.raise_for_status()
        c = requests.post(self._api("git/refs"), headers=self._headers(), timeout=10,
                          json={"ref": f"refs/heads/{self.data_branch}", "sha": base.json()["object"]["sha"]})
        if c.status_code == 403:             # 브랜치 만들 권한이 없으면 main에 그대로 저장
            self.data_branch = self.code_branch
            return
        if c.status_code not in (201, 422):  # 422 = 이미 있음
            c.raise_for_status()

    def load(self) -> dict:
        if self.gh:
            src = self.data_branch
            js = self._get_file(self.data_branch)
            if js is None:                       # 예전 버전처럼 main에 저장돼 있던 데이터도 이어받기
                src = self.code_branch
                js = self._get_file(self.code_branch)
            if js is None:
                self.sha = None
                self.info.update(loaded_from="(저장된 파일 없음)", loaded_count=0)
                return empty_book()
            book = json.loads(base64.b64decode(js["content"]).decode("utf-8"))
            self.info.update(loaded_from=f"{src} 브랜치", loaded_count=len(book.get("holdings", [])))
        else:
            if not os.path.exists(self.local_path):
                return empty_book()
            with open(self.local_path, encoding="utf-8") as f:
                book = json.load(f)
        return normalize_book(book)

    def remote_count(self) -> int:
        js = self._get_file(self.data_branch) if self.gh else None
        if not js:
            return 0
        return len(json.loads(base64.b64decode(js["content"]).decode("utf-8")).get("holdings", []))

    def save(self, book: dict, message: str = "update portfolio", allow_empty: bool = False) -> None:
        book["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        raw = json.dumps(book, ensure_ascii=False, indent=2)
        if self.gh:
            self._ensure_data_branch()
            cur = self._get_file(self.data_branch)
            if cur and not allow_empty and not book["holdings"]:
                old = json.loads(base64.b64decode(cur["content"]).decode("utf-8"))
                if old.get("holdings"):
                    raise RuntimeError("저장된 종목이 있는데 빈 목록으로 덮어쓰려 해서 막았어요. 새로고침 후 다시 해 주세요.")
            body = {"message": message, "branch": self.data_branch,
                    "content": base64.b64encode(raw.encode("utf-8")).decode()}
            if cur:
                body["sha"] = cur["sha"]
            r = requests.put(self._api(f"contents/{self.path}"), headers=self._headers(), json=body, timeout=20)
            if r.status_code == 403:
                raise RuntimeError("GitHub 토큰에 쓰기 권한이 없어요. GitHub → Settings → Developer settings → "
                                   "Fine-grained tokens → 이 토큰 Edit → Repository permissions의 Contents를 "
                                   "'Read and write'로 바꾸고 Update 해 주세요.")
            for attempt in range(3):             # 충돌(409)이면 잠깐 쉬고 최신 sha로 다시
                if r.status_code not in (409, 422):
                    break
                time.sleep(1.0 + attempt)
                cur = self._get_file(self.data_branch)
                if cur:
                    body["sha"] = cur["sha"]
                else:
                    body.pop("sha", None)
                r = requests.put(self._api(f"contents/{self.path}"), headers=self._headers(), json=body, timeout=20)
            r.raise_for_status()
            self.sha = r.json()["content"]["sha"]
            # 저장 확인: 방금 쓴 파일을 다시 읽어 종목 수가 같은지 본다
            back = self._get_file(self.data_branch)
            n_back = len(json.loads(base64.b64decode(back["content"]).decode("utf-8")).get("holdings", [])) if back else None
            self.info.update(saved_to=f"{self.data_branch} 브랜치", saved_count=len(book["holdings"]),
                             verified=(n_back == len(book["holdings"])))
        else:
            os.makedirs(os.path.dirname(self.local_path) or ".", exist_ok=True)
            with open(self.local_path, "w", encoding="utf-8") as f:
                f.write(raw)


def normalize_book(book: dict) -> dict:
    out = empty_book()
    out["settings"].update(book.get("settings", {}))
    out["updated"] = book.get("updated", "")
    out["snapshots"] = list(book.get("snapshots", []))   # 증권사 화면 기준 계좌 금액 기록
    for h in book.get("holdings", []):
        base = new_holding(h.get("code", ""), h.get("name", ""), h.get("market", "KR"))
        base.update(h)
        base["trades"] = sorted(base.get("trades", []), key=lambda t: (t.get("date", ""), t.get("side") != "buy"))
        if base.get("atr_src") == "auto":          # 자동으로 넣었던 ATR은 지우고 직접 입력 받기
            base["atr_pct"], base["atr_src"], base["target_fixed"] = None, None, None
        if not base.get("target_fixed") and base["trades"]:
            base["target_fixed"] = fixed_target(base, out["settings"])   # 예전 기록: 첫 매수가 기준으로 고정
        out["holdings"].append(base)
    return out


# ─────────────────────────── 포지션 계산 ───────────────────────────
@dataclass
class Position:
    qty: float = 0
    avg: float = 0.0
    cost: float = 0.0
    realized: float = 0.0
    first_buy: float = 0.0
    first_date: str = ""
    buys: int = 0
    sells: int = 0
    sold_after_last_buy: bool = False   # 최근 매수 이후 부분매도(익절) 했는지
    history: list = field(default_factory=list)


def position(h: dict) -> Position:
    p = Position()
    for t in sorted(h["trades"], key=lambda t: (t["date"], t["side"] != "buy")):
        price, qty = float(t["price"]), float(t["qty"])
        if t["side"] == "buy":
            if p.qty <= 0:          # 새로 시작하는 포지션
                p.first_buy, p.first_date, p.sold_after_last_buy = price, t["date"], False
            p.cost += price * qty
            p.qty += qty
            p.avg = p.cost / p.qty if p.qty else 0
            p.buys += 1
            p.sold_after_last_buy = False
        else:
            qty = min(qty, p.qty)
            p.realized += (price - p.avg) * qty
            p.qty -= qty
            p.cost = p.avg * p.qty
            p.sells += 1
            p.sold_after_last_buy = p.qty > 0
            if p.qty <= 0:
                p.avg, p.cost = 0.0, 0.0
    return p


def kr_tick(p: float) -> float:
    """한국 주식 호가 단위 (엑셀 일지와 같은 기준)."""
    for lim, t in ((2000, 1), (5000, 5), (20000, 10), (50000, 50), (200000, 100), (500000, 500)):
        if p < lim:
            return t
    return 1000


ATR_N = 20   # ATR 기간 (일)


def implied_atr(avg: float, stop: float | None) -> float | None:
    """직접 넣은 손절가 → 그 손절폭(%) = 내 ATR로 본다."""
    if not stop or not avg or stop >= avg:
        return None
    return round((avg - stop) / avg * 100, 2)


def levels(h: dict, avg: float, settings: dict, atr_pct: float | None = "use_h",
           manual: float | None = "use_h") -> dict:
    """손절률·손절가·3R 목표가.
    - 직접 손절가(평단 아래)가 있으면: 손절폭 = (평단 − 손절가)/평단 = ATR, 3R 목표 = 평단 + 3 × 그 폭
    - 없으면: 손절률 = MAX(기본 손절폭, 내 ATR%)
    - 직접 손절가가 평단 이상(본전·수익 보호)이면: 손절가는 그 값, R·3R은 ATR 기준 유지"""
    atr = h.get("atr_pct") if atr_pct == "use_h" else atr_pct
    man = (float(h["stop"]) if h.get("stop") else None) if manual == "use_h" else manual
    kr = h.get("market", "KR") == "KR" and avg > 0
    imp = implied_atr(avg, man)
    if imp is not None:
        spct, stop, src = imp, man, f"직접 손절가 → ATR {imp:g}%"
    else:
        spct = max(float(settings["stop_pct"]), float(atr) if atr else 0.0)
        stop = avg * (1 - spct / 100)
        if kr:
            stop = math.ceil(stop / kr_tick(stop)) * kr_tick(stop)
        src = f"ATR {float(atr):g}%" if atr and float(atr) >= settings["stop_pct"] else f"기본 {settings['stop_pct']:g}%"
        if man:                                   # 본전 이상으로 올린 손절가
            stop, src = man, "직접 설정 (본전 이상)"
    floor = h.get("stop_floor")
    floor_used = False
    if floor and float(floor) > stop:          # 손절가는 내려가지 않음 (물타기로 평단이 내려가도)
        stop, src = float(floor), h.get("stop_floor_note") or "기존 손절가 유지 (내려가지 않음)"
        floor_used = True
    target = fixed_target(h, settings, spct)
    return {"stop_pct": spct, "stop": stop, "target": target, "r_unit": avg * spct / 100,
            "manual_stop": man, "implied_atr": imp,
            "note": src if floor_used or (man and imp is None) else f"평단 −{spct:g}% ({src})"}


def fixed_target(h: dict, settings: dict, spct: float | None = None) -> float:
    """익절가(3R) = 기준가(첫 매수가) × (1 + 3 × 손절률). 저장해 두지 않고 매번 같은 식으로 계산한다.
    → 추가 매수는 첫 매수가를 바꾸지 않으니 그대로, 매수 기록을 고치면 바로 맞게 다시 계산된다."""
    p = position(h)
    base = float(h.get("target_base") or 0) or p.first_buy or p.avg     # 기준가 = 첫 매수가 (직접 고칠 수 있음)
    sp = stop_pct_of(h, settings) if h.get("atr_pct") else (spct if spct is not None else stop_pct_of(h, settings))
    t = base * (1 + settings["target_r"] * sp / 100)
    if h.get("market", "KR") == "KR" and t > 0:
        t = math.floor(t / kr_tick(t)) * kr_tick(t)
    return t


def calc_explain(h: dict, avg: float, settings: dict) -> dict:
    """화면에 보여줄 계산식 (손절가·익절가가 어떻게 나왔는지)."""
    p = position(h)
    lv = levels(h, avg, settings)
    sp = lv["stop_pct"]
    base = float(h.get("target_base") or 0) or p.first_buy or p.avg
    sp_t = stop_pct_of(h, settings) if h.get("atr_pct") else sp
    raw_t = base * (1 + settings["target_r"] * sp_t / 100)
    raw_s = avg * (1 - sp / 100)
    auto_s = math.ceil(raw_s / kr_tick(raw_s)) * kr_tick(raw_s) if h.get("market", "KR") == "KR" and raw_s > 0 else raw_s
    return {
        "stop": f"평단 {avg:,.0f} × (1 − {sp:g}%) = {raw_s:,.0f}"
                + (f" → 호가 올림 {auto_s:,.0f}" if abs(raw_s - auto_s) >= 1 else "")
                + (f" · 지금은 {lv['note']} {lv['stop']:,.0f}" if abs(lv["stop"] - auto_s) >= 1 else ""),
        "target": f"기준가(첫 매수) {base:,.0f} × (1 + {settings['target_r']:g} × {sp_t:g}%) = {raw_t:,.0f}"
                  + (f" → 호가 내림 {lv['target']:,.0f}" if abs(raw_t - lv["target"]) >= 1 else ""),
        "base": base, "stop_pct": sp}


def reset_target(h: dict, settings: dict) -> None:
    """ATR(손절폭)을 처음 설정·수정할 때만 첫 매수가 기준으로 익절가를 다시 정한다."""
    h["target_fixed"] = None
    p = position(h)
    sp = levels({**h, "stop_floor": None}, p.first_buy or p.avg, settings)["stop_pct"]
    h["target_fixed"] = fixed_target(h, settings, sp)


def trail_lock_r(peak_r: float, be_r: float = 2.0) -> int | None:
    """최고 몇 R까지 갔는지 → 손절가를 몇 R에 잠글지.
    +2R → 0R(본절), +3R → 0R 유지, +4R → +1R, +5R → +2R … (최고 R − 3)."""
    lvl = math.floor(peak_r + 1e-9)
    if lvl >= 4:
        return lvl - 3
    if lvl >= be_r:
        return 0
    return None


def check_breakeven(h: dict, df: pd.DataFrame, settings: dict) -> dict | None:
    """수익이 R 단위로 올라갈 때마다 손절가를 따라 올린다 (트레일링). 내려가지 않음.
    +2R 닿으면 본절, +4R이면 +1R, +5R이면 +2R … 마지막 매수일 이후 일봉 고가 기준."""
    p = position(h)
    if p.qty <= 0 or df is None or df.empty:
        return None
    lv = levels(h, p.avg, settings)
    r_unit = lv["r_unit"]
    if r_unit <= 0:
        return None
    last_buy = max((t["date"] for t in h["trades"] if t["side"] == "buy"), default=None)
    seg = df[df.index >= pd.Timestamp(last_buy)] if last_buy else df
    if seg.empty:
        return None
    peak = float(seg["High"].max())
    peak_r = (peak - p.avg) / r_unit
    lock = trail_lock_r(peak_r, settings.get("breakeven_r", 2.0))
    if lock is None:
        return None
    new_stop = p.avg + lock * r_unit
    if h.get("market", "KR") == "KR":
        new_stop = math.ceil(new_stop / kr_tick(new_stop)) * kr_tick(new_stop)
    if new_stop <= lv["stop"] + 1e-9:            # 이미 그 이상이면 그대로
        return None
    hit_r = math.floor(peak_r + 1e-9)
    trig = p.avg + hit_r * r_unit
    hit_day = seg.index[seg["High"] >= trig][0].date().isoformat()
    label = "본절" if lock == 0 else f"+{lock}R"
    old = lv["stop"]
    h["stop_floor"] = float(new_stop)
    h["stop_floor_note"] = f"{label} 잠금 (+{hit_r}R 도달)"
    h.setdefault("stop_log", []).append(
        {"date": hit_day, "event": f"+{hit_r}R 도달 → 손절가 {label}",
         "from": round(old, 2), "to": round(new_stop, 2), "target": round(lv["target"], 2), "avg": round(p.avg, 2)})
    return {"from": old, "to": new_stop, "date": hit_day, "label": label, "hit_r": hit_r}


def check_3r(h: dict, df: pd.DataFrame, settings: dict) -> str | None:
    """이번 포지션에서 3R 익절가에 처음 닿은 날을 기록한다 (일봉 고가 기준, 실시간 가격 포함)."""
    p = position(h)
    if p.qty <= 0 or df is None or df.empty:
        return None
    tgt = levels(h, p.avg, settings)["target"]
    key = f"{p.first_date}|{tgt:.0f}"              # 포지션이나 익절가가 바뀌면 새로 판단
    if h.get("r3_hit") and h.get("r3_key") == key:
        return None
    seg = df[df.index >= pd.Timestamp(p.first_date)] if p.first_date else df
    if seg.empty or float(seg["High"].max()) < tgt:
        if h.get("r3_key") != key:
            h["r3_hit"], h["r3_key"] = None, None
        return None
    day = seg.index[seg["High"] >= tgt][0].date().isoformat()
    h["r3_hit"], h["r3_key"] = day, key
    h.setdefault("stop_log", []).append(
        {"date": day, "event": f"🎯 {settings['target_r']:g}R 달성 ({tgt:,.0f})", "from": None, "to": None,
         "target": round(tgt, 2), "avg": round(p.avg, 2)})
    return day


def trade_preview(h: dict, side: str, price: float, qty: float, settings: dict, account: float) -> dict:
    """매매를 기록하기 전에 '지금 → 매매 후' 수량·평단·손절가·익절가·손익비를 계산."""
    p0 = position(h)
    lv0 = levels(h, p0.avg, settings)
    before = {"qty": p0.qty, "avg": p0.avg, "stop": lv0["stop"], "target": lv0["target"]}
    warn, info = [], []
    if side == "buy":
        q1 = p0.qty + qty
        avg1 = (p0.avg * p0.qty + price * qty) / q1 if q1 else price
        man = float(h["stop"]) if h.get("stop") else None
        h1 = {**h, "stop": man if (man and man >= avg1) else None, "stop_floor": None,
              "target_fixed": lv0["target"]}
        auto = levels(h1, avg1, settings)["stop"]
        stop1 = auto
        if auto < lv0["stop"]:
            stop1 = lv0["stop"]
            warn.append(f"물타기 경고 — 평단이 {p0.avg:,.0f} → {avg1:,.0f}로 내려가요. 원칙상 물타기는 하지 않아요. "
                        f"손절가는 내려가지 않고 {lv0['stop']:,.0f} 그대로 둬요.")
        after = {"qty": q1, "avg": avg1, "stop": stop1, "target": lv0["target"]}
    else:
        q1 = max(0.0, p0.qty - qty)
        after = {"qty": q1, "avg": p0.avg if q1 else 0.0, "stop": lv0["stop"], "target": lv0["target"]}
        pnl = (price - p0.avg) * min(qty, p0.qty)
        info.append(f"실현손익 {pnl:+,.0f} ({(price / p0.avg - 1) * 100:+.2f}%)" if p0.avg else "")
        if q1 == 0:
            info.append("전량 매도 — 청산 처리돼요")
    for d in (before, after):
        risk = d["avg"] - d["stop"]
        d["rr"] = (d["target"] - d["avg"]) / risk if risk > 0 else None
        d["loss"] = max(0.0, risk) * d["qty"]
        d["loss_pct"] = d["loss"] / account * 100 if account else None
    if after["loss_pct"] and after["loss_pct"] > settings["risk_pct"]:
        warn.append(f"손절 시 계좌 손실 {after['loss_pct']:.2f}% — 1회 위험 {settings['risk_pct']:g}%를 넘어요.")
    if side == "buy" and after["rr"] is not None and after["rr"] < 2:
        warn.append(f"새 평단 기준 손익비 {after['rr']:.1f} : 1 — 많이 낮아졌어요.")
    return {"before": before, "after": after, "warn": warn, "info": [i for i in info if i]}


def apply_trade(h: dict, side: str, date_s: str, price: float, qty: float, memo: str,
                settings: dict, account: float) -> dict:
    """미리보기와 똑같이 기록: 매매 추가 + 손절가 변경 + 변경 기록(stop_log)."""
    pv = trade_preview(h, side, price, qty, settings, account)
    if not h.get("target_fixed"):
        h["target_fixed"] = pv["before"]["target"]          # 지금 익절가를 고정해 둠
    h["trades"].append({"date": date_s, "side": side, "price": float(price), "qty": float(qty), "memo": memo})
    b, a = pv["before"], pv["after"]
    if side == "buy":
        man = float(h["stop"]) if h.get("stop") else None
        if man is not None and man < a["avg"]:
            h["stop"] = None                                   # 손절폭은 ATR로 반영돼 있으니 새 평단 기준으로
        keep = a["stop"] <= b["stop"] + 1e-9
        h["stop_floor"] = b["stop"] if keep else None
        if not keep:
            h["stop_floor_note"] = None
        h.setdefault("stop_log", []).append(
            {"date": date_s, "event": f"추가 매수 {qty:,.0f}주 @ {price:,.2f}",
             "from": round(b["stop"], 2), "to": round(a["stop"], 2), "target": round(a["target"], 2),
             "avg": round(a["avg"], 2)})
    else:
        h.setdefault("stop_log", []).append(
            {"date": date_s, "event": f"매도 {qty:,.0f}주 @ {price:,.2f}", "from": round(b["stop"], 2),
             "to": round(a["stop"], 2), "target": round(a["target"], 2), "avg": round(a["avg"], 2)})
    return pv


def stop_pct_of(h: dict, settings: dict) -> float:
    """손절률 = MAX(기본 손절폭, 종목별 ATR%) — 엑셀 일지 공식."""
    atr = h.get("atr_pct")
    return max(float(settings["stop_pct"]), float(atr) if atr else 0.0)


def stop_price(h: dict, pos: Position, settings: dict, atr_now: float | None = None) -> tuple[float, str]:
    lv = levels(h, pos.avg, settings)
    return lv["stop"], lv["note"]


def size_for(entry: float, stop: float, settings: dict, fx: float = 1.0) -> dict:
    """계좌 대비 위험 %로 살 수 있는 수량."""
    risk_money = settings["equity"] * settings["risk_pct"] / 100
    per_share = (entry - stop) * fx
    if per_share <= 0:
        return {"qty": 0, "risk_money": risk_money, "value": 0, "weight": 0}
    qty = math.floor(risk_money / per_share)
    value = qty * entry * fx
    return {"qty": qty, "risk_money": risk_money, "value": value,
            "weight": value / settings["equity"] * 100 if settings["equity"] else 0}


# ─────────────────────────── 시세 데이터 ───────────────────────────
def fetch_kr(code: str, count: int = 340) -> tuple[pd.DataFrame, str]:
    """네이버 일봉. 신고가 보드(data.py)와 같은 순서: fchart → 안 되면 siseJson. 지수는 KOSPI / KOSDAQ."""
    name, df = "", pd.DataFrame()
    try:
        r = requests.get("https://fchart.stock.naver.com/sise.nhn",
                         params={"symbol": code, "timeframe": "day", "count": count, "requestType": 0},
                         headers=UA, timeout=10)
        r.encoding = "euc-kr"
        txt = r.text
        m = re.search(r'name="([^"]*)"', txt)
        name = m.group(1) if m else ""
        recs = []
        for row in re.findall(r'data="([^"]+)"', txt):
            d, o, hi, lo, c, v = row.split("|")
            recs.append((pd.to_datetime(d), float(o), float(hi), float(lo), float(c), float(v)))
        df = pd.DataFrame(recs, columns=["Date", "Open", "High", "Low", "Close", "Volume"]).set_index("Date")
    except Exception:
        df = pd.DataFrame()
    if len(df) < 30 and code.isdigit():
        try:
            end = pd.Timestamp.today()
            r = requests.get("https://api.finance.naver.com/siseJson.naver", headers=UA, timeout=10,
                             params={"symbol": code, "requestType": 1, "timeframe": "day",
                                     "startTime": (end - pd.Timedelta(days=int(count * 1.6))).strftime("%Y%m%d"),
                                     "endTime": end.strftime("%Y%m%d")})
            rows = re.findall(r'\["(\d{8})",\s*([\d.]+),\s*([\d.]+),\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)', r.text)
            if rows:
                df = pd.DataFrame([(pd.to_datetime(d), float(o), float(h_), float(l_), float(c), float(v))
                                   for d, o, h_, l_, c, v in rows],
                                  columns=["Date", "Open", "High", "Low", "Close", "Volume"]).set_index("Date")
        except Exception:
            pass
    return df, name


def atr20_pct(df: pd.DataFrame, upto: str | None = None, n: int = 20) -> float | None:
    """ATR(20)% — 신고가 보드 data.py의 _atr_np와 같은 계산.
    TR = max(고가, 전일종가) − min(저가, 전일종가), 최근 n일 평균 ÷ 그날 종가 × 100.
    upto(날짜)를 주면 그날까지의 데이터로 계산 (= 진입일 ATR)."""
    if df is None or df.empty:
        return None
    d = df[df.index <= pd.Timestamp(upto)] if upto else df
    if len(d) < n + 1:
        d = df.iloc[: n + 1] if upto and len(df) >= n + 1 else d   # 진입일이 데이터보다 이르면 가장 이른 구간
    if len(d) < n + 1:
        return None
    hi, lo, cl = d["High"].to_numpy(float), d["Low"].to_numpy(float), d["Close"].to_numpy(float)
    prev = np.empty_like(cl)
    prev[0] = np.nan
    prev[1:] = cl[:-1]
    tr = np.fmax(hi, prev) - np.fmin(lo, prev)
    atr = np.nanmean(tr[-n:])
    return float(atr / cl[-1] * 100) if cl[-1] else None


def atr_status(h: dict) -> str:
    """'applied' = 내가 직접 넣은 ATR/손절가, 'check' = 예전 값이라 확인 필요, 'missing' = 아직 없음(임시 8%)."""
    if h.get("atr_src") in ("manual", "stop") and (h.get("atr_pct") or h.get("stop")):
        return "applied"
    if h.get("atr_pct") and h.get("atr_src") != "auto":
        return "check"
    return "missing"


def auto_atr(h: dict, df: pd.DataFrame) -> float | None:
    """종목의 자동 ATR = 첫 매수일 기준 ATR(20). 손절률은 MAX(8%, 이 값)."""
    p = position(h)
    v = atr20_pct(df, p.first_date or None)
    return round(v, 1) if v else None


def fetch_us(ticker: str) -> tuple[pd.DataFrame, str]:
    import yfinance as yf
    t = yf.Ticker(ticker)
    df = t.history(period="15mo", auto_adjust=False)
    if df.empty:
        return pd.DataFrame(), ""
    df = df[["Open", "High", "Low", "Close", "Volume"]]
    df.index = df.index.tz_localize(None)
    name = ""
    try:
        name = t.info.get("shortName", "") or ""
    except Exception:
        pass
    return df, name


def fetch_fx() -> float | None:
    try:
        import yfinance as yf
        v = yf.Ticker("KRW=X").history(period="5d")["Close"].dropna()
        return float(v.iloc[-1]) if len(v) else None
    except Exception:
        return None


def search_kr(q: str) -> list[dict]:
    """종목명으로 코드 찾기 (네이버 자동완성)."""
    try:
        r = requests.get("https://ac.stock.naver.com/ac", params={"q": q, "target": "stock"},
                         headers=UA, timeout=8)
        items = r.json().get("items", [])
        out = []
        for it in items:
            if isinstance(it, dict):
                code, name = it.get("code"), it.get("name")
                mkt = it.get("typeName") or it.get("typeCode") or ""
                if code and name and str(code).isdigit():
                    out.append({"code": code, "name": name, "market": mkt})
        return out[:10]
    except Exception:
        return []


# ─────────────────────────── 지표 ───────────────────────────
def indicators(df: pd.DataFrame) -> dict:
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
    ind = {"price": float(c.iloc[-1]), "prev": float(c.iloc[-2]) if len(c) > 1 else float(c.iloc[-1])}
    ind["chg"] = (ind["price"] / ind["prev"] - 1) * 100
    for n in (5, 20, 50, 60, 120):
        ind[f"ma{n}"] = float(c.rolling(n).mean().iloc[-1]) if len(c) >= n else np.nan
    ind["ma5_prev"] = float(c.rolling(5).mean().iloc[-2]) if len(c) >= 6 else np.nan
    ind["ma50_prev"] = float(c.rolling(50).mean().iloc[-2]) if len(c) >= 51 else np.nan

    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    a20 = atr20_pct(df)                   # ATR은 20일 기준 (신고가 보드와 같은 계산)
    ind["atr_pct"] = a20 if a20 is not None else np.nan

    # ADX(14)
    up, dn = h.diff(), -l.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    ndm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    atr_w = tr.ewm(alpha=1 / 14, adjust=False).mean()
    pdi = 100 * pdm.ewm(alpha=1 / 14, adjust=False).mean() / atr_w
    ndi = 100 * ndm.ewm(alpha=1 / 14, adjust=False).mean() / atr_w
    dx = (100 * (pdi - ndi).abs() / (pdi + ndi)).replace([np.inf, -np.inf], np.nan)
    ind["adx"] = float(dx.ewm(alpha=1 / 14, adjust=False).mean().iloc[-1]) if len(c) > 30 else np.nan

    look = min(250, len(df) - 1)
    ind["high52_prev"] = float(h.iloc[-look - 1:-1].max())       # 오늘 제외 52주 최고
    ind["high52"] = max(ind["high52_prev"], float(h.iloc[-1]))
    ind["from_high"] = (ind["price"] / ind["high52"] - 1) * 100
    ind["breakout"] = ind["price"] > ind["high52_prev"]
    ind["vol"] = float(v.iloc[-1])
    ind["vol20"] = float(v.iloc[-21:-1].mean()) if len(v) > 21 else float(v.mean())
    ind["vol_ratio"] = ind["vol"] / ind["vol20"] if ind["vol20"] else np.nan
    ind["vol_max250"] = bool(v.iloc[-1] >= v.iloc[-look - 1:].max())
    rng = float(h.iloc[-1] - l.iloc[-1])
    ind["dcr"] = (ind["price"] - float(l.iloc[-1])) / rng * 100 if rng else 50.0
    body_top = max(float(df["Open"].iloc[-1]), ind["price"])
    ind["upper_wick"] = (float(h.iloc[-1]) - body_top) / rng if rng else 0.0
    ind["ret20"] = (c.iloc[-1] / c.iloc[-21] - 1) * 100 if len(c) > 21 else np.nan
    ind["ret63"] = (c.iloc[-1] / c.iloc[-64] - 1) * 100 if len(c) > 64 else np.nan
    ind["ret126"] = (c.iloc[-1] / c.iloc[-127] - 1) * 100 if len(c) > 127 else np.nan
    ind["aligned"] = all(not np.isnan(ind[k]) for k in ("ma5", "ma20", "ma60", "ma120")) and \
        ind["ma5"] > ind["ma20"] > ind["ma60"] > ind["ma120"]
    return ind


def market_regime(df: pd.DataFrame, name: str) -> dict:
    c = df["Close"]
    ma60 = c.rolling(60).mean()
    last, m = float(c.iloc[-1]), float(ma60.iloc[-1])
    return {"name": name, "price": last, "ma60": m, "above": last > m,
            "gap": (last / m - 1) * 100,
            "ret63": (c.iloc[-1] / c.iloc[-64] - 1) * 100 if len(c) > 64 else np.nan,
            "ret126": (c.iloc[-1] / c.iloc[-127] - 1) * 100 if len(c) > 127 else np.nan}


# ─────────────────────────── 펀더멘털·수급 (네이버) ───────────────────────────
def _naver_html(url: str) -> str:
    r = requests.get(url, headers=UA, timeout=10)
    r.encoding = "euc-kr"
    return r.text


def _fundamentals_kr_html(code: str) -> dict:
    """네이버 종목 메인 '기업실적분석' 표에서 영업이익 추이."""
    out = {"ok": None, "detail": "", "annual": {}, "quarter": {}}
    try:
        html = _naver_html(f"https://finance.naver.com/item/main.naver?code={code}")
        tables = pd.read_html(StringIO(html), match="영업이익")
        t = next(tb for tb in tables if tb.shape[1] >= 8)
        t = t.set_index(t.columns[0])
        row = t.loc[[i for i in t.index if "영업이익" in str(i) and "률" not in str(i)][0]]
        cols = list(t.columns)
        ann = [(c, row[c]) for c in cols if "연간" in str(c[0])]
        qtr = [(c, row[c]) for c in cols if "분기" in str(c[0])]

        def clean(pairs):
            res = {}
            for c, val in pairs:
                label = str(c[1])
                try:
                    res[label] = float(str(val).replace(",", ""))
                except ValueError:
                    pass
            return res
        a, q = clean(ann), clean(qtr)
        out["annual"], out["quarter"] = a, q
        q_act = [(k, v) for k, v in q.items() if "(E)" not in k]
        a_act = [(k, v) for k, v in a.items() if "(E)" not in k]
        a_est = [(k, v) for k, v in a.items() if "(E)" in k]
        msgs, votes = [], []
        if len(q_act) >= 5:
            (k1, v1), (k0, v0) = q_act[-1], q_act[-5]
            g = (v1 / v0 - 1) * 100 if v0 > 0 else None
            votes.append(v1 > v0)
            msgs.append(f"최근 분기 {k1} 영업이익 {v1:,.0f}억 (전년동기 {v0:,.0f}억" + (f", {g:+.0f}%)" if g is not None else ")"))
        elif len(q_act) >= 2:
            (k1, v1), (k0, v0) = q_act[-1], q_act[-2]
            votes.append(v1 > v0)
            msgs.append(f"최근 분기 {k1} {v1:,.0f}억 (직전 분기 {v0:,.0f}억)")
        if len(a_act) >= 2:
            (k1, v1), (k0, v0) = a_act[-1], a_act[-2]
            votes.append(v1 > v0)
            msgs.append(f"연간 {k1} {v1:,.0f}억 (전년 {v0:,.0f}억)")
        if a_est and a_act:
            ke, ve = a_est[0]
            msgs.append(f"컨센서스 {ke} {ve:,.0f}억 ({'증가' if ve > a_act[-1][1] else '감소'} 예상)")
        out["ok"] = all(votes) if votes else None
        out["detail"] = " · ".join(msgs)
    except Exception as e:
        out["detail"] = f"실적 표를 못 가져왔어요 ({type(e).__name__})"
    return out


def _flows_kr_html(code: str) -> dict:
    """네이버 '외국인·기관 순매매' 최근 5일/20일 합계(주)."""
    out = {"ok": False}
    try:
        rows = []
        for page in (1, 2):
            html = _naver_html(f"https://finance.naver.com/item/frgn.naver?code={code}&page={page}")
            for tb in pd.read_html(StringIO(html)):
                flat = [" ".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in tb.columns]
                if any("기관" in f for f in flat) and any("외국인" in f for f in flat):
                    tb.columns = flat
                    rows.append(tb)
                    break
        df = pd.concat(rows).dropna(how="all")
        date_col = next(c for c in df.columns if "날짜" in c)
        df = df[df[date_col].astype(str).str.match(r"\d{4}\.\d{2}\.\d{2}")]
        inst = next(c for c in df.columns if "기관" in c and "순매매" in c)
        frgn = next(c for c in df.columns if "외국인" in c and "순매매" in c)

        def num(s):
            return pd.to_numeric(s.astype(str).str.replace(",", "").str.replace("+", ""), errors="coerce")
        df[inst], df[frgn] = num(df[inst]), num(df[frgn])
        out.update(ok=True,
                   inst5=float(df[inst].head(5).sum()), frgn5=float(df[frgn].head(5).sum()),
                   inst20=float(df[inst].head(20).sum()), frgn20=float(df[frgn].head(20).sum()))
        out["daily"] = [{"date": str(r[date_col]).replace(".", ""), "inst": float(r[inst] or 0),
                         "frgn": float(r[frgn] or 0), "indiv": 0.0, "close": None}
                        for _, r in df.head(20).iterrows()]
    except Exception as e:
        out["err"] = type(e).__name__
    return out


# ─────────────── 네이버 모바일 증권 JSON (실적·수급·리포트·뉴스) ───────────────
M_UA = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
                      "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        "Referer": "https://m.stock.naver.com/"}


def _mjson(url: str, params: dict | None = None):
    r = requests.get(url, params=params, headers=M_UA, timeout=10)
    r.raise_for_status()
    return r.json()


def _n(v):
    """'+1,234' / '-5' / 1234 → float"""
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").replace("+", "").strip())
    except ValueError:
        return None


def _walk(obj):
    """JSON 안의 모든 dict를 순회 (필드 이름이 바뀌어도 찾을 수 있게)."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _pick(d: dict, *cands):
    for c in cands:
        if c in d and d[c] not in (None, ""):
            return d[c]
    low = {k.lower(): v for k, v in d.items()}
    for c in cands:
        for k, v in low.items():
            if c.lower() in k and v not in (None, ""):
                return v
    return None


def fin_series_kr(code: str) -> dict:
    """분기·연간 영업이익 (억원). {'quarter': [(라벨, 값, 추정?)], 'annual': [...]}"""
    out = {"quarter": [], "annual": [], "src": None}
    for kind in ("quarter", "annual"):
        try:
            js = _mjson(f"https://m.stock.naver.com/api/stock/{code}/finance/{kind}")
            fi = js.get("financeInfo", js)
            titles = fi.get("trTitleList") or []
            rows = fi.get("rowList") or []
            op = next((r for r in rows if "영업이익" in str(r.get("title", "")) and "률" not in str(r.get("title", ""))), None)
            if not op:
                continue
            cols = op.get("columns", {})
            for t in titles:
                k = t.get("key")
                v = _n((cols.get(k) or {}).get("value")) if k in cols else None
                if v is None:
                    continue
                label = str(t.get("title", k)).rstrip(".")
                out[kind].append((label, v, str(t.get("isConsensus", "N")).upper() == "Y"))
            out["src"] = "네이버 모바일"
        except Exception:
            pass
    return out


def fundamentals_kr(code: str) -> dict:
    """영업이익 증가 여부 + 분기·연간 추이. 모바일 JSON 먼저, 안 되면 PC 페이지."""
    fs = fin_series_kr(code)
    q = [x for x in fs["quarter"] if not x[2]]
    a = [x for x in fs["annual"] if not x[2]]
    a_est = [x for x in fs["annual"] if x[2]]
    if not q and not a:
        res = _fundamentals_kr_html(code)
        res.setdefault("series", fs)
        return res
    msgs, votes = [], []
    if len(q) >= 5:
        (k1, v1, _), (k0, v0, _) = q[-1], q[-5]
        votes.append(v1 > v0)
        g = f", {(v1 / v0 - 1) * 100:+.0f}%" if v0 > 0 else ""
        msgs.append(f"최근 분기 {k1} 영업이익 {v1:,.0f}억 (전년동기 {v0:,.0f}억{g})")
    elif len(q) >= 2:
        (k1, v1, _), (k0, v0, _) = q[-1], q[-2]
        votes.append(v1 > v0)
        msgs.append(f"최근 분기 {k1} {v1:,.0f}억 (직전 분기 {v0:,.0f}억)")
    if len(a) >= 2:
        (k1, v1, _), (k0, v0, _) = a[-1], a[-2]
        votes.append(v1 > v0)
        msgs.append(f"연간 {k1} {v1:,.0f}억 (전년 {v0:,.0f}억)")
    if a_est and a:
        ke, ve, _ = a_est[0]
        msgs.append(f"컨센서스 {ke} {ve:,.0f}억 ({'증가' if ve > a[-1][1] else '감소'} 예상)")
    return {"ok": all(votes) if votes else None, "detail": " · ".join(msgs), "series": fs}


def flows_kr(code: str) -> dict:
    """외국인·기관 순매수(주). 일별 목록 포함."""
    out = {"ok": False, "daily": []}
    try:
        js = _mjson(f"https://m.stock.naver.com/api/stock/{code}/integration")
        rows = []
        for d in _walk(js):
            f = _pick(d, "foreignerPureBuyQuant")
            o = _pick(d, "organPureBuyQuant")
            if f is not None and o is not None:
                rows.append({"date": str(_pick(d, "bizdate", "localTradedAt", "date") or ""),
                             "frgn": _n(f) or 0.0, "inst": _n(o) or 0.0,
                             "indiv": _n(_pick(d, "individualPureBuyQuant")) or 0.0,
                             "close": _n(_pick(d, "closePrice"))})
        rows.sort(key=lambda r: r["date"], reverse=True)
        if rows:
            out["daily"] = rows
    except Exception as e:
        out["err_m"] = type(e).__name__
    html_res = _flows_kr_html(code)          # PC 페이지: 20일치까지
    if html_res.get("ok"):
        out.update({k: v for k, v in html_res.items() if k != "daily"})
        if html_res.get("daily") and len(html_res["daily"]) > len(out["daily"]):
            out["daily"] = html_res["daily"]
        out["ok"] = True
    elif out["daily"]:
        d = out["daily"]
        out.update(ok=True, inst5=sum(r["inst"] for r in d[:5]), frgn5=sum(r["frgn"] for r in d[:5]),
                   inst20=sum(r["inst"] for r in d[:20]), frgn20=sum(r["frgn"] for r in d[:20]))
    else:
        out["err"] = html_res.get("err")
    return out


def reports_kr(code: str, n: int = 8) -> list[dict]:
    """증권사 리포트 목록 [{title, broker, date, url}]."""
    items = []
    try:
        js = _mjson(f"https://m.stock.naver.com/api/stock/{code}/integration")
        for d in _walk(js.get("researches", js) if isinstance(js, dict) else js):
            t = _pick(d, "tit", "title")
            b = _pick(d, "bnm", "brokerName", "officeName")
            rid = _pick(d, "id", "nid", "researchId")
            if t and b:
                items.append({"title": str(t), "broker": str(b), "date": str(_pick(d, "wdt", "writeDate", "date") or ""),
                              "url": f"https://finance.naver.com/research/company_read.naver?nid={rid}" if rid else
                              f"https://finance.naver.com/research/company_list.naver?searchType=itemCode&itemCode={code}"})
    except Exception:
        pass
    if not items:                            # PC 리서치 목록 페이지
        try:
            html = _naver_html("https://finance.naver.com/research/company_list.naver"
                               f"?searchType=itemCode&itemCode={code}")
            for m in re.finditer(r'<a href="company_read\.naver\?nid=(\d+)[^"]*"[^>]*>([^<]+)</a>\s*</td>\s*'
                                 r'<td>([^<]+)</td>.*?<td class="date"[^>]*>([^<]+)</td>', html, re.S):
                items.append({"title": m.group(2).strip(), "broker": m.group(3).strip(), "date": m.group(4).strip(),
                              "url": f"https://finance.naver.com/research/company_read.naver?nid={m.group(1)}"})
        except Exception:
            pass
    seen, out = set(), []
    for it in items:
        if it["title"] not in seen:
            seen.add(it["title"])
            out.append(it)
    return out[:n]


def news_kr(code: str, n: int = 10) -> list[dict]:
    """종목 뉴스 [{title, office, date, url}]."""
    items = []
    try:
        js = _mjson(f"https://m.stock.naver.com/api/news/stock/{code}", {"pageSize": n, "page": 1})
        for d in _walk(js):
            t = _pick(d, "title", "tit")
            aid, oid = d.get("articleId") or d.get("aid"), d.get("officeId") or d.get("oid")
            if t and aid and oid:
                dt = str(_pick(d, "datetime", "dt", "date") or "")
                if len(dt) >= 12 and dt[:12].isdigit():
                    dt = f"{dt[4:6]}.{dt[6:8]} {dt[8:10]}:{dt[10:12]}"
                items.append({"title": re.sub(r"<[^>]+>", "", str(t)).replace("&quot;", '"').replace("&amp;", "&"),
                              "office": str(_pick(d, "officeName", "ohnm", "press") or ""), "date": dt,
                              "url": f"https://n.news.naver.com/mnews/article/{oid}/{aid}"})
    except Exception:
        pass
    if not items:
        try:
            r = requests.get(f"https://finance.naver.com/item/news_news.naver?code={code}&page=1",
                             headers={**UA, "Referer": f"https://finance.naver.com/item/news.naver?code={code}"},
                             timeout=10)
            r.encoding = "euc-kr"
            for m in re.finditer(r'<a href="(/item/news_read\.naver\?[^"]+)"[^>]*class="tit"[^>]*>(.*?)</a>.*?'
                                 r'<td class="info">([^<]*)</td>\s*<td class="date">([^<]*)</td>', r.text, re.S):
                items.append({"title": re.sub(r"<[^>]+>", "", m.group(2)).strip(), "office": m.group(3).strip(),
                              "date": m.group(4).strip()[5:], "url": "https://finance.naver.com" + m.group(1).replace("&amp;", "&")})
        except Exception:
            pass
    seen, out = set(), []
    for it in items:
        if it["title"] not in seen:
            seen.add(it["title"])
            out.append(it)
    return out[:n]


def sector_kr(code: str) -> str | None:
    """네이버 업종명 (예: 반도체와반도체장비). 모바일 → PC 순서로 시도."""
    try:
        js = _mjson(f"https://m.stock.naver.com/api/stock/{code}/integration")
        for d in _walk(js):
            v = _pick(d, "industryName", "industryCodeName", "upjongName")
            if v and isinstance(v, str):
                return v.strip()
    except Exception:
        pass
    try:
        html = _naver_html(f"https://finance.naver.com/item/main.naver?code={code}")
        m = re.search(r'type=upjong&(?:amp;)?no=\d+"[^>]*>([^<]+)</a>', html)
        if m:
            return m.group(1).strip()
    except Exception:
        pass
    return None


def fundamentals_us(ticker: str) -> dict:
    out = {"ok": None, "detail": ""}
    try:
        import yfinance as yf
        q = yf.Ticker(ticker).quarterly_income_stmt
        row = q.loc["Operating Income"].dropna()
        row = row.sort_index()
        if len(row) >= 5:
            v1, v0 = row.iloc[-1], row.iloc[-5]
            out["ok"] = bool(v1 > v0)
            out["detail"] = f"최근 분기 영업이익 {v1/1e6:,.0f}M$ (전년동기 {v0/1e6:,.0f}M$)"
        elif len(row) >= 2:
            v1, v0 = row.iloc[-1], row.iloc[-2]
            out["ok"] = bool(v1 > v0)
            out["detail"] = f"최근 분기 영업이익 {v1/1e6:,.0f}M$ (직전 분기 {v0/1e6:,.0f}M$)"
    except Exception as e:
        out["detail"] = f"실적을 못 가져왔어요 ({type(e).__name__})"
    return out


# ─────────────────────────── 판단 ───────────────────────────
PRINCIPLES = {
    "stop": ("손절 원칙",
             "손절률 = MAX(8%, ATR%)로 잡은 1R 손절가를 이탈하면 전량 매도한다. 한 번에 잃는 돈은 계좌의 1.5%까지. "
             "리스크관리 > 멘탈관리 > 종목관리 > 타점관리."),
    "exit_rest": ("나머지 청산 원칙",
                  "3R에서 절반을 익절한 뒤, 남은 물량은 일봉 5일선이 50일선에 닿을 때 매도한다."),
    "take_half": ("3R 절반 익절 원칙",
                  "평단 대비 3R(손절률 × 3, 8%면 +24%) 도달 시 절반을 팔고 손절가를 본전으로 올린다. 손실은 짧게, 수익은 길게."),
    "climax": ("클라이맥스 원칙",
               "신고가에서 1년 최대 거래량 + 긴 윗꼬리(클라이맥스 탑), 또는 고점 부근 −10% 대량 장대음봉은 비중 축소 신호. "
               "갑작스러운 악재 급락엔 빠르게 대응한다."),
    "trend": ("추세 판단 원칙",
              "상승 속도에 맞는 이평선으로 본다 — 빠른 돌파주 5일선, 추세주 20일선, 느린 종목 50일선. "
              "추세가 꺾였다는 판단은 일부러 천천히 한다."),
    "breakeven": ("손절가 이동 원칙", "+2R에 닿으면 손절가를 본절로, 그 뒤 +4R이면 +1R, +5R이면 +2R … 최고 R보다 3R 아래로 따라 올려 수익을 지킨다. 손절가는 내려가지 않는다."),
    "protect": ("수익 잠금 원칙", "R 단계에 따라 올려 둔 손절가(본절·+1R·+2R…)를 이탈하면 남은 물량을 정리한다. 수익은 지키고 손실은 없다."),
    "add": ("불타기 원칙",
            "물타기는 하지 않는다. 수익 중인 종목이 52주 신고가를 거래량과 함께 돌파할 때만 1유닛을 더하고, "
            "더할 때마다 손절가를 올린다."),
    "market": ("시장 원칙", "코스피·코스닥이 60일선 이하이면 신규·추가 매매를 쉰다."),
    "fund": ("펀더멘털 원칙", "강하게 치고 나가도 영업이익이 증가하지 않으면 배제한다."),
    "rs": ("RS 원칙", "종목 RS는 시장(지수) RS보다 위여야 한다."),
    "hold": ("보유 원칙", "손절가 위에 있고 추세가 살아 있으면 흔들리지 않고 들고 간다. 시장은 예측이 아니라 대응."),
}


def _rule(key: str, level: str, fact: str) -> dict:
    title, principle = PRINCIPLES[key]
    return {"key": key, "title": title, "principle": principle, "fact": fact, "level": level}


def auto_trend_ma(ind: dict) -> int:
    r = ind.get("ret20", np.nan)
    if not np.isnan(r) and r >= 30:
        return 5
    if not np.isnan(r) and r >= 8:
        return 20
    return 50


def judge(h: dict, pos: Position, ind: dict, settings: dict, regime: dict | None,
          fund_ok: bool | None, flows: dict | None, n_open: int, equity: float | None = None) -> dict:
    """매매 원칙대로 신호와 근거(원칙 + 지금 상태)를 만든다."""
    mk = h.get("market", "KR")
    f0 = (lambda v: f"{v:,.0f}") if mk == "KR" else (lambda v: f"{v:,.2f}")
    price = ind["price"]
    lv = levels(h, pos.avg, settings)
    stop, stop_note, spct = lv["stop"], lv["note"], lv["stop_pct"]
    r_unit = lv["r_unit"]
    r_mult = (price - pos.avg) / r_unit if r_unit else 0
    pnl_pct = (price / pos.avg - 1) * 100 if pos.avg else 0
    target = lv["target"]
    trend_n = int(h["trend_ma"]) if str(h.get("trend_ma")) in ("5", "20", "50") else auto_trend_ma(ind)
    trend_ma = ind.get(f"ma{trend_n}")
    below_trend = bool(trend_ma and not np.isnan(trend_ma) and price < trend_ma)
    market_ok = regime["above"] if regime else True
    rs_ok = None
    if regime and not np.isnan(ind.get("ret63", np.nan)) and not np.isnan(regime.get("ret63", np.nan)):
        rs_ok = ind["ret63"] > regime["ret63"]
    cross_5_50 = (not np.isnan(ind["ma5"]) and not np.isnan(ind["ma50"]) and ind["ma5"] <= ind["ma50"])
    climax = ind["vol_max250"] and ind["upper_wick"] >= 0.5 and ind["from_high"] > -5
    big_red = ind["chg"] <= -10 and ind["vol_ratio"] >= 1.5 and ind["from_high"] > -15
    flow_ok = None
    if flows and flows.get("ok"):
        flow_ok = (flows["inst5"] + flows["frgn5"]) > 0

    checks = [
        ("시장", market_ok, f"{regime['name']} {regime['gap']:+.1f}% vs 60일선" if regime else "지수 정보 없음"),
        ("RS", rs_ok, f"3개월 {ind['ret63']:+.1f}% vs 지수 {regime['ret63']:+.1f}%" if rs_ok is not None else "계산 불가"),
        ("영업이익", fund_ok, "증가" if fund_ok else ("감소/정체" if fund_ok is False else "확인 필요")),
        ("수급", flow_ok, (f"5일 기관 {flows['inst5']:+,.0f} · 외인 {flows['frgn5']:+,.0f}주"
                          if flows and flows.get("ok") else "정보 없음")),
        (f"추세({trend_n}일선)", not below_trend, f"{trend_n}일선 {'아래' if below_trend else '위'}"),
        ("ADX", (ind["adx"] >= 20) if not np.isnan(ind["adx"]) else None,
         f"{ind['adx']:.0f} ({'강한 추세' if ind['adx'] > 40 else '추세' if ind['adx'] >= 20 else '약함/박스'})"
         if not np.isnan(ind["adx"]) else "-"),
    ]

    rules, actions = [], []
    signal, level = "보유", "hold"
    if price <= stop and stop >= pos.avg:
        signal, level = "수익 잠금 청산", "sell"
        rules.append(_rule("protect", "sell",
                           f"현재가 {f0(price)} ≤ 잠근 손절가 {f0(stop)} ({stop_note}). 수익률 {pnl_pct:+.1f}%."))
        actions.append(f"보유 {pos.qty:,.0f}주 정리 (수익 확정)")
    elif price <= stop:
        signal, level = "손절", "sell"
        loss = (price - pos.avg) * pos.qty
        rules.append(_rule("stop", "sell",
                           f"현재가 {f0(price)} ≤ 손절가 {f0(stop)} ({stop_note}). "
                           f"수익률 {pnl_pct:+.1f}% = {r_mult:+.2f}R"
                           + (f", 계좌 대비 {loss / equity * 100:+.2f}%" if equity else "") + "."))
        actions.append(f"보유 {pos.qty:,.0f}주 전량 매도")
    elif pos.sold_after_last_buy and cross_5_50:
        signal, level = "나머지 청산", "sell"
        rules.append(_rule("exit_rest", "sell",
                           f"절반 익절 이후 5일선 {f0(ind['ma5'])}이 50일선 {f0(ind['ma50'])}에 닿음. 현재 {r_mult:+.2f}R."))
        actions.append(f"남은 {pos.qty:,.0f}주 매도")
    elif price >= target and not pos.sold_after_last_buy:
        signal, level = "절반 익절", "take"
        rules.append(_rule("take_half", "take",
                           f"{r_mult:.2f}R 도달 (+{pnl_pct:.1f}%, 목표가 {f0(target)}). 손절률 {spct:g}% × {settings['target_r']:g} = "
                           f"+{spct * settings['target_r']:g}%."))
        actions.append(f"{math.floor(pos.qty / 2):,}주 매도")
        actions.append(f"남은 물량 손절가를 평단 {f0(pos.avg)}로 올리기")
    elif climax or big_red:
        signal, level = "비중 축소", "warn"
        facts = []
        if climax:
            facts.append(f"오늘 거래량이 1년 최대 + 윗꼬리 {ind['upper_wick'] * 100:.0f}% (52주 고점 대비 {ind['from_high']:+.1f}%)")
        if big_red:
            facts.append(f"고점 부근에서 {ind['chg']:.1f}% 장대음봉, 거래량 평소 {ind['vol_ratio']:.1f}배")
        rules.append(_rule("climax", "warn", " / ".join(facts) + "."))
        actions.append("일부 줄이고 섹터 전체가 같이 꺾이는지 확인")
    else:
        can_add = (ind["breakout"] and ind["vol_ratio"] >= 1.5 and pnl_pct > 0 and market_ok
                   and rs_ok is not False and fund_ok is not False)
        if can_add:
            signal, level = "불타기 가능", "add"
            rules.append(_rule("add", "add",
                               f"수익 {pnl_pct:+.1f}% 상태에서 52주 신고가({f0(ind['high52_prev'])}) 돌파, "
                               f"거래량 평소 {ind['vol_ratio']:.1f}배, 종가위치 {ind['dcr']:.0f}%."))
            actions.append(f"1유닛 추가 후 손절가를 최소 평단 {f0(pos.avg)} 이상으로")
        elif below_trend:
            signal, level = "추세 이탈 주의", "warn"
            rules.append(_rule("trend", "warn",
                               f"최근 20일 {ind['ret20']:+.1f}% → {trend_n}일선 기준. 현재가 {f0(price)} < {trend_n}일선 {f0(trend_ma)}."))
            actions.append("급락 악재가 아니면 종가 기준 2~3일 더 확인")
        if r_mult >= settings["breakeven_r"] and stop < pos.avg:
            rules.append(_rule("breakeven", "warn",
                               f"{r_mult:.2f}R 수익인데 손절가 {f0(stop)}가 아직 평단 {f0(pos.avg)} 아래."))
            actions.append(f"손절가를 {f0(pos.avg)}(본전)로 올리기")
            if level == "hold":
                signal, level = "손절가 올리기", "warn"
        if level == "hold":
            rules.append(_rule("hold", "hold",
                               f"손절가 {f0(stop)}까지 {(price / stop - 1) * 100:.1f}% 여유, {trend_n}일선 위, 현재 {r_mult:+.2f}R."))
    # 공통 경고 원칙
    if not market_ok:
        rules.append(_rule("market", "warn", f"{regime['name']} {regime['gap']:+.1f}% — 60일선 아래."))
        if level == "add":
            signal, level = "보유 (시장 쉬는 구간)", "hold"
    if fund_ok is False:
        rules.append(_rule("fund", "warn", "최근 영업이익이 전년·직전 대비 늘지 않음."))
    if rs_ok is False:
        rules.append(_rule("rs", "warn", f"3개월 수익률 {ind['ret63']:+.1f}% < 지수 {regime['ret63']:+.1f}%."))
    if n_open > settings["max_positions"]:
        actions.append(f"보유 {n_open}종목 — 최대 {settings['max_positions']}종목 초과")

    return {"signal": signal, "level": level, "rules": rules, "actions": actions,
            "reasons": [rules[0]["fact"]] if rules else [], "checks": checks, "stop": stop,
            "stop_note": stop_note, "stop_pct": spct, "r_mult": r_mult, "pnl_pct": pnl_pct,
            "trend_n": trend_n, "target": target, "r_unit": r_unit}


# ─────────────────────────── 계좌 곡선 ───────────────────────────
def equity_curve(book: dict, get_hist, fx: float, start: str | None = None) -> pd.DataFrame:
    """초기 자본금 + 실현손익 + 평가손익을 날짜별로 다시 계산 (엑셀 일지의 누적잔고와 같은 개념)."""
    init = float(book["settings"]["equity"])
    trades_all = [t for h in book["holdings"] for t in h["trades"]]
    if not trades_all:
        return pd.DataFrame()
    first = min(t["date"] for t in trades_all)
    start = start or book["settings"].get("start_date") or first
    start = min(start, first)
    idx = pd.bdate_range(start=start, end=pd.Timestamp.today().normalize())
    realized = pd.Series(0.0, index=idx)
    unreal = pd.Series(0.0, index=idx)
    for h in book["holdings"]:
        df, _ = get_hist(h["code"], h["market"])
        m = fx if h["market"] == "US" else 1.0
        close = df["Close"].reindex(idx, method="ffill") if not df.empty else pd.Series(np.nan, index=idx)
        trades = sorted(h["trades"], key=lambda t: (t["date"], t["side"] != "buy"))
        qty = avg = cost = real = 0.0
        ti = 0
        q_s, a_s, r_s = [], [], []
        for d in idx:
            while ti < len(trades) and pd.Timestamp(trades[ti]["date"]) <= d:
                t = trades[ti]
                p, q = float(t["price"]), float(t["qty"])
                if t["side"] == "buy":
                    cost += p * q
                    qty += q
                    avg = cost / qty if qty else 0
                else:
                    q = min(q, qty)
                    real += (p - avg) * q
                    qty -= q
                    cost = avg * qty
                ti += 1
            q_s.append(qty)
            a_s.append(avg)
            r_s.append(real)
        q_s, a_s = np.array(q_s), np.array(a_s)
        c = close.to_numpy(dtype=float)
        c = np.where(np.isnan(c), a_s, c)
        unreal += pd.Series(q_s * (c - a_s) * m, index=idx)
        realized += pd.Series(np.array(r_s) * m, index=idx)
    out = pd.DataFrame({"realized": realized, "unrealized": unreal})
    out["equity"] = init + out["realized"] + out["unrealized"]
    out["ret"] = (out["equity"] / init - 1) * 100
    out["peak"] = out["equity"].cummax()
    out["dd"] = (out["equity"] / out["peak"] - 1) * 100
    return out


# ─────────────────────────── 엑셀 일지 가져오기 ───────────────────────────
def parse_journal_xlsx(file) -> dict:
    """추세추종 돌파매매 계좌일지(.xlsx)의 설정·매매일지 시트를 읽는다."""
    import openpyxl
    wb = openpyxl.load_workbook(file, data_only=True)
    settings = {}
    if "설정" in wb.sheetnames:
        for row in wb["설정"].iter_rows(values_only=True):
            k, v = (row[0], row[1]) if len(row) > 1 else (None, None)
            if not isinstance(k, str) or v is None:
                continue
            if "초기 자본금" in k:
                settings["equity"] = float(v)
            elif "리스크" in k:
                settings["risk_pct"] = float(v) * 100 if float(v) < 1 else float(v)
            elif "목표 R" in k:
                settings["target_r"] = float(v)
            elif "손절폭" in k:
                settings["stop_pct"] = float(v) * 100 if float(v) < 1 else float(v)
    ws = wb["매매일지"] if "매매일지" in wb.sheetnames else wb.worksheets[-1]
    rows = list(ws.iter_rows(values_only=True))
    hi = next(i for i, r in enumerate(rows) if r and "진입일" in [str(c).split("\n")[0] if c else "" for c in r])
    head = [str(c).replace("\n", "") if c else "" for c in rows[hi]]

    def col(*keys):
        for i, hname in enumerate(head):
            if all(k in hname for k in keys):
                return i
        return None
    C = {k: col(*v) for k, v in {
        "date": ("진입일",), "name": ("종목명",), "entry": ("진입가",), "atr": ("ATR",),
        "stop": ("손절가",), "stop_pct": ("손절률",), "qty": ("수량",), "p1d": ("1차청산일",),
        "p1": ("1차청산가",), "p1r": ("1차청산", "비중"), "p2d": ("2차청산일",), "p2": ("2차청산가",),
        "memo": ("메모",)}.items()}

    def g(r, k):
        i = C.get(k)
        return r[i] if i is not None and i < len(r) else None

    def d2s(v):
        if v is None or v == "":
            return None
        if isinstance(v, (datetime, date)):
            return v.strftime("%Y-%m-%d")
        return str(pd.to_datetime(str(v)).date())

    trades = []
    for r in rows[hi + 1:]:
        name, entry, qty = g(r, "name"), g(r, "entry"), g(r, "qty")
        if not name or not isinstance(entry, (int, float)) or not isinstance(qty, (int, float)) or qty <= 0:
            continue
        atr = g(r, "atr")
        sp = g(r, "stop_pct")
        atr_pct = None
        if isinstance(atr, (int, float)) and atr:
            atr_pct = atr * 100 if atr < 1 else atr
        elif isinstance(sp, (int, float)) and sp:
            atr_pct = abs(sp) * 100 if abs(sp) < 1 else abs(sp)
        rec = {"name": str(name).strip(), "date": d2s(g(r, "date")) or date.today().isoformat(),
               "entry": float(entry), "qty": float(qty), "atr_pct": round(atr_pct, 2) if atr_pct else None,
               "memo": str(g(r, "memo") or ""), "sells": []}
        p1, p2 = g(r, "p1"), g(r, "p2")
        ratio = g(r, "p1r") if isinstance(g(r, "p1r"), (int, float)) else 0.5
        ratio = ratio / 100 if ratio > 1 else ratio
        sold = 0.0
        if isinstance(p1, (int, float)) and p1:
            q1 = math.floor(qty * ratio)
            rec["sells"].append({"date": d2s(g(r, "p1d")) or rec["date"], "price": float(p1), "qty": q1})
            sold = q1
        if isinstance(p2, (int, float)) and p2:
            rec["sells"].append({"date": d2s(g(r, "p2d")) or rec["date"], "price": float(p2), "qty": qty - sold})
        trades.append(rec)
    return {"settings": settings, "trades": trades}


# ─────────────────────────── 보유 종목 사진 읽기 (AI) ───────────────────────────
IMAGE_PROMPT = """이 이미지는 증권사 앱/HTS의 계좌 잔고 화면이다(여러 장이면 같은 계좌의 이어진 화면).
아래 형식의 JSON 하나로만 답하라. 설명 문장은 쓰지 마라.
{"account": {"total_asset": 총자산/추정자산/순자산(예수금 포함 계좌 전체 금액),
             "cash": 예수금/주문가능현금(D+2 예수금이 있으면 그 값),
             "total_buy": 총매입금액, "total_eval": 총평가금액(주식), "total_pnl": 총평가손익,
             "total_ret": 총수익률(%)},
 "holdings": [{"name": 종목명, "code": 종목코드(보이면, 없으면 null), "qty": 보유수량(잔고수량),
               "avg_price": 매입평균가/평단가, "price": 현재가(보이면), "buy_amount": 매입금액(보이면),
               "eval_amount": 평가금액(보이면), "market": "KR" 또는 "US"}]}
- 숫자는 쉼표·원·$·% 없이 숫자만. 보이지 않는 값은 null.
- 수량과 평단은 열 이름을 보고 정확히 읽어라. 평가금액·평가손익을 수량이나 평단으로 착각하지 마라.
- qty는 '보유수량/잔고수량'이다. '매도가능수량·주문가능수량·금일매수수량'이 따로 보여도 그것을 쓰지 마라.
- 한 종목이 두 줄(위·아래)로 나뉘어 표시되는 앱이 많다. 같은 종목의 위 줄과 아래 줄 값을 섞지 말고 열 제목 위치를 맞춰 읽어라.
- 종목이 안 보이면 holdings는 빈 배열, 계좌 요약이 안 보이면 account 값들은 null.
- 미국 주식이면 name에 티커를 우선 넣고 market을 "US"로."""


def _shrink(img_bytes: bytes, max_side: int = 1600) -> tuple[bytes, str]:
    from io import BytesIO
    from PIL import Image
    im = Image.open(BytesIO(img_bytes))
    im = im.convert("RGB")
    if max(im.size) > max_side:
        im.thumbnail((max_side, max_side))
    buf = BytesIO()
    im.save(buf, format="JPEG", quality=88)
    return buf.getvalue(), "image/jpeg"


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "").replace("원", "").replace("$", "").replace("%", "").strip())
    except ValueError:
        return None


def _parse_ai(text: str) -> dict:
    m = re.search(r"\{.*\}|\[.*\]", text, re.S)
    if not m:
        raise ValueError("AI 응답에서 표를 찾지 못했어요.")
    data = json.loads(m.group(0))
    if isinstance(data, list):
        data = {"holdings": data, "account": {}}
    acc = {k: _num((data.get("account") or {}).get(k))
           for k in ("total_asset", "cash", "total_buy", "total_eval", "total_pnl", "total_ret")}
    rows = []
    for d in data.get("holdings") or []:
        if not isinstance(d, dict) or not d.get("name"):
            continue
        row = {"name": str(d["name"]).strip(), "code": (str(d["code"]).strip() if d.get("code") else None),
               "qty": _num(d.get("qty")), "avg_price": _num(d.get("avg_price")), "price": _num(d.get("price")),
               "market": "US" if str(d.get("market", "KR")).upper() == "US" else "KR", "check": ""}
        # 검산: 매입금액 ÷ 평단 ≈ 수량, 평가금액 ÷ 현재가 ≈ 수량. 다르면 금액 쪽을 믿고 표시
        buy, ev = _num(d.get("buy_amount")), _num(d.get("eval_amount"))
        cands = []
        if buy and row["avg_price"]:
            cands.append(buy / row["avg_price"])
        if ev and row["price"]:
            cands.append(ev / row["price"])
        if cands:
            q2 = round(sum(cands) / len(cands))
            if not row["qty"] or abs(q2 - row["qty"]) / max(q2, 1) > 0.03:
                row["check"] = f"수량 {row['qty']:,.0f} → {q2:,.0f}주로 고침 (매입·평가금액으로 검산)" if row["qty"] \
                    else f"수량을 금액으로 계산 {q2:,.0f}주"
                row["qty"] = float(q2)
        rows.append(row)
    return {"account": acc, "holdings": rows}


def gemini_models(key: str) -> list[str]:
    """이 키로 쓸 수 있는 Flash 계열 모델을 최신 순으로 (lite·이미지 전용 제외)."""
    try:
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                         params={"key": key, "pageSize": 200}, timeout=15)
        names = [m["name"].split("/", 1)[1] for m in r.json().get("models", [])
                 if "generateContent" in m.get("supportedGenerationMethods", [])]
    except Exception:
        return []
    cand = [n for n in names if "flash" in n
            and not any(x in n for x in ("lite", "image", "tts", "audio", "live", "thinking"))]

    def rank(n):
        m = re.search(r"gemini-(\d+(?:\.\d+)?)", n)
        return (float(m.group(1)) if m else 0, "preview" not in n and "exp" not in n, "latest" in n)
    return sorted(cand, key=rank, reverse=True)


def _gemini_one(img: tuple[bytes, str], ai: dict, models: list[str]) -> tuple[str, str]:
    """사진 한 장을 Gemini로 읽는다. (응답 텍스트, 성공한 모델)"""
    b, mt = img
    base = {"contents": [{"parts": [{"inline_data": {"mime_type": mt, "data": base64.b64encode(b).decode()}},
                                    {"text": IMAGE_PROMPT}]}]}
    # 생각(thinking)을 줄여 속도를 높인다. 모델마다 받는 옵션이 달라서 차례로 시도.
    thinking_opts = [{"thinkingLevel": "low"}, {"thinkingBudget": 0}, None]
    queue, tried, last = list(models), set(), None
    while queue and len(tried) < 6:
        model = queue.pop(0)
        if model in tried:
            continue
        tried.add(model)
        for th in thinking_opts:
            gc = {"responseMimeType": "application/json", "temperature": 0}
            if th:
                gc["thinkingConfig"] = th
            try:
                r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                  params={"key": ai["gemini_key"]}, json={**base, "generationConfig": gc}, timeout=150)
            except requests.exceptions.Timeout:
                last = f"{model} 응답 시간 초과"
                break                          # 다음 모델로
            if r.ok:
                text = "".join(p.get("text", "") for c in r.json().get("candidates", [])
                               for p in c.get("content", {}).get("parts", []))
                return text, model
            last = f"{model} 오류 {r.status_code}: {r.text[:160]}"
            if r.status_code in (400, 401, 403) and "API key" in r.text:
                raise RuntimeError(f"Gemini API 키 문제: {r.text[:200]}")
            if r.status_code == 400 and "thinking" in r.text.lower():
                continue                       # 이 옵션을 안 받는 모델 → 다른 옵션으로
            hint = re.findall(r"models/(gemini-[\w.\-]+)", r.text)
            queue = [m for m in hint if m not in tried] + queue
            break
    raise RuntimeError(f"Gemini로 사진을 못 읽었어요 ({last})")


def _merge(results: list[dict]) -> dict:
    acc, rows, seen = {}, [], set()
    for res in results:
        for k, v in res["account"].items():
            if v is not None and acc.get(k) is None:
                acc[k] = v
        for r in res["holdings"]:
            key = (r.get("code") or r["name"], r.get("qty"))
            if key not in seen:            # 이어진 캡처에 같은 종목이 겹쳐 찍힌 경우 한 번만
                seen.add(key)
                rows.append(r)
    for k in ("total_asset", "cash", "total_buy", "total_eval", "total_pnl", "total_ret"):
        acc.setdefault(k, None)
    return {"account": acc, "holdings": rows}


def read_holdings_image(images: list[bytes], ai: dict) -> dict:
    """잔고 스크린샷 → {"account": {...}, "holdings": [...]}. 사진마다 동시에 읽어서 합친다."""
    from concurrent.futures import ThreadPoolExecutor
    parts = [_shrink(b, 1280) for b in images]
    if ai.get("anthropic_key"):
        def one(img):
            b, mt = img
            r = requests.post("https://api.anthropic.com/v1/messages", timeout=150,
                              headers={"x-api-key": ai["anthropic_key"], "anthropic-version": "2023-06-01",
                                       "content-type": "application/json"},
                              json={"model": ai.get("model") or "claude-sonnet-4-5", "max_tokens": 3000,
                                    "messages": [{"role": "user", "content": [
                                        {"type": "image", "source": {"type": "base64", "media_type": mt,
                                                                     "data": base64.b64encode(b).decode()}},
                                        {"type": "text", "text": IMAGE_PROMPT}]}]})
            if not r.ok:
                raise RuntimeError(f"Claude API 오류 {r.status_code}: {r.text[:200]}")
            return _parse_ai("".join(c.get("text", "") for c in r.json().get("content", [])))
    elif ai.get("gemini_key"):
        models = [ai["model"]] if ai.get("model") else []
        models += gemini_models(ai["gemini_key"]) or ["gemini-flash-latest", "gemini-3-flash-preview", "gemini-2.5-flash"]

        def one(img):
            text, _ = _gemini_one(img, ai, models)
            return _parse_ai(text)
    else:
        raise RuntimeError("Secrets에 ANTHROPIC_API_KEY 또는 GEMINI_API_KEY가 없어요.")
    with ThreadPoolExecutor(max_workers=min(4, len(parts))) as ex:
        results = list(ex.map(one, parts))
    return _merge(results)


def reconcile(book: dict, rows: list[dict]) -> list[dict]:
    """사진에서 읽은 잔고와 앱 기록을 비교해 무엇을 할지 정한다."""
    plan = []
    open_h = [h for h in book["holdings"] if position(h).qty > 0]
    for r in rows:
        h = next((x for x in open_h if (r.get("code") and x["code"] == r["code"]) or x["name"] == r["name"]), None)
        qty, avg = r.get("qty") or 0, r.get("avg_price") or 0
        if h is None:
            plan.append({**r, "action": "신규", "hid": None, "trade_qty": qty, "trade_price": avg, "side": "buy"})
            continue
        p = position(h)
        dq = qty - p.qty
        if abs(dq) < 1e-9 and (not avg or abs(avg - p.avg) / max(p.avg, 1) < 0.002):
            plan.append({**r, "code": h["code"], "action": "변동 없음", "hid": h["id"], "trade_qty": 0,
                         "trade_price": 0, "side": None})
        elif dq > 0:
            price = (qty * avg - p.qty * p.avg) / dq if avg else (r.get("price") or p.avg)
            plan.append({**r, "code": h["code"], "action": f"추가 매수 {dq:,.0f}주", "hid": h["id"],
                         "trade_qty": dq, "trade_price": round(price, 2), "side": "buy"})
        elif dq < 0:
            price = r.get("price")          # 없으면 반영할 때 현재가로
            plan.append({**r, "code": h["code"], "action": f"매도 {-dq:,.0f}주", "hid": h["id"],
                         "trade_qty": -dq, "trade_price": price, "side": "sell"})
        else:
            plan.append({**r, "code": h["code"], "action": "평단만 다름 (확인)", "hid": h["id"], "trade_qty": 0,
                         "trade_price": 0, "side": None})
    names = {r["name"] for r in rows}
    codes = {r.get("code") for r in rows if r.get("code")}
    for h in open_h:
        if h["name"] not in names and h["code"] not in codes:
            p = position(h)
            plan.append({"name": h["name"], "code": h["code"], "qty": 0, "avg_price": p.avg, "price": None,
                         "market": h["market"], "action": "사진에 없음", "hid": h["id"], "trade_qty": p.qty,
                         "trade_price": None, "side": "sell"})
    return plan
