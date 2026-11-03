"""오늘의 투자 명언 — 검증된 투자 대가들의 말 중 하루에 하나씩.

guru_quotes.json: [{"a": 저자, "s": 출처, "c": 분류, "q": 명언(한국어), "m": 내 원칙으로 본 의미}]
순서는 명언 내용의 해시로 고정해 섞고, 기준일부터 지난 날 수로 하루에 하나씩 넘겨요.
(파일에 명언을 추가해도 이미 지나간 날의 순서는 크게 흔들리지 않게 해시 정렬)
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import date

QUOTES_VERSION = "2026-11-03-guru"
_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "guru_quotes.json")
_START = date(2026, 11, 3)
_CACHE: dict = {}

CAT_COLOR = {"리스크": "#E5484D", "멘탈": "#7C5CFC", "종목": "#2B7FF0", "타점": "#F59E0B",
             "시장": "#0EA5E9", "인내": "#16A34A", "학습": "#6B7280"}


def load() -> list[dict]:
    mt = os.path.getmtime(_PATH) if os.path.exists(_PATH) else 0
    if _CACHE.get("mt") != mt:
        try:
            with open(_PATH, encoding="utf-8") as f:
                rows = [r for r in json.load(f) if r.get("q") and r.get("a")]
        except Exception:  # noqa: BLE001
            rows = []
        rows.sort(key=lambda r: hashlib.md5(r["q"].encode("utf-8")).hexdigest())
        _CACHE.update(mt=mt, rows=rows)
    return _CACHE["rows"]


def today_quote(day: date | None = None, shift: int = 0) -> dict | None:
    rows = load()
    if not rows:
        return None
    day = day or date.today()
    i = ((day - _START).days + shift) % len(rows)
    return {**rows[i], "no": i + 1, "total": len(rows)}
