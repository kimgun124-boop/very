"""📉 하락장 점검 — '하락장 과대낙폭 vs 버티는 종목' 섹터 점검 자료(제주바람 작성 HTML)를 보드에 싣는 모듈.

자료는 bear_check/날짜.json 으로 저장해요. 새 점검 HTML을 앱에 넣으면 안의 데이터(const D = {...})만 뽑아
같은 형식으로 저장하고, GITHUB_TOKEN이 있으면 저장소에도 올려 영구 보관해요.
"""
from __future__ import annotations

import glob
import json
import os
import re

DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bear_check")

TAG_TXT = {
    "tail": ("꼬리", "낙폭 −60% 이하"),
    "popular_crash": ("인기", "과대낙폭 중 거래대금 상위 30%"),
    "late_breaker": ("늦락", "고점 60거래일 이내인데 −30% 이하"),
    "pre_decliner": ("선락", "코스피 고점 전에 먼저 꺾임"),
    "near_high": ("신고", "고점 −5% 이내 또는 최근 20일 신고가"),
}
SRC = "제주바람 「하락장 과대낙폭 vs 버티는 종목 — 섹터 점검」"
NOTE = ("상황점검용 자료이며 종목 추천·매수 판단이 아니에요. 백테스트 수치(−67%/−8% 등)는 깡토님 백테스트 기준이고 "
        "현재 데이터로 재계산한 값이 아니에요. 가격은 KRX 정규장 종가(다음 금융)를 분할·무상증자 보정한 수정주가, "
        "52주 고점은 종가 기준. 분산일·FTD는 자료 작성자의 자체 계산이라 스탁이지·보드 🚦 시장 신호와 다를 수 있어요.")


def parse_html(raw: bytes | str) -> dict | None:
    """점검 HTML에서 데이터 객체(const D = {...})만 꺼내요. 형식이 다르면 None."""
    s = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else raw
    m = re.search(r"const\s+D\s*=\s*(\{.*?\})\s*;\s*\n", s, flags=re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(1))
    except ValueError:
        return None
    if not isinstance(d, dict) or not d.get("base") or not isinstance(d.get("stocks"), list):
        return None
    return d


def list_dates() -> list[str]:
    """저장된 점검 날짜(최신 먼저)."""
    out = []
    for p in glob.glob(os.path.join(DIR, "*.json")):
        b = os.path.splitext(os.path.basename(p))[0]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", b):
            out.append(b)
    return sorted(out, reverse=True)


def load(date: str) -> dict | None:
    try:
        with open(os.path.join(DIR, f"{date}.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_local(d: dict) -> str:
    os.makedirs(DIR, exist_ok=True)
    path = os.path.join(DIR, f"{d['base']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    return path


def repo_path(d: dict) -> str:
    return f"bear_check/{d['base']}.json"


def tag_label(tags) -> str:
    return " ".join(TAG_TXT.get(t, (t, ""))[0] for t in (tags or []))
