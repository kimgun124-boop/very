"""친구 계정 · 관심종목 · 포트폴리오 저장소.

- 친구는 자기 비밀번호 하나만 치면 들어와요(아이디 없음). 그래서 비밀번호는 사람마다 달라야 해요.
  비밀번호로 계정을 바로 찾으려고 '후추(pepper)'를 친 HMAC 값(pid)을 같이 저장하고,
  확인은 소금(salt)을 친 느린 해시(PBKDF2)로 한 번 더 해요.
- 관심종목은 그룹(헬스케어·금융 …)으로 나눠 담고, 포트폴리오는 매매 기록·입출금 기록으로 계산해요.
- 비밀번호는 그대로 저장하지 않고 소금(salt)을 친 해시로만 저장해요(holdings와 같은 방식).
- 저장소(GitHub)가 공개(Public)라서, 파일 전체를 USERS_KEY로 암호화해서 올려요.
  USERS_KEY를 모르면 저장소를 열어 봐도 누가 무슨 종목을 담았는지, 비밀번호 해시도 알 수 없어요.
- 서버(Streamlit) 하나가 모든 접속을 같이 받으니, 메모리에 한 벌을 두고 잠금(lock)을 걸어 고쳐요.
  그래서 친구 둘이 동시에 저장해도 서로 덮어쓰지 않아요.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import threading
import uuid

from cryptography.fernet import Fernet, InvalidToken

import holdings

FRIENDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_friends.json")
GH_PATH = "user_friends.json"
MAX_WATCH = 60                       # 한 사람당 관심종목 최대 개수(앱이 느려지지 않게)
MAX_GROUPS = 12
MAX_TRADES = 2000
MAX_USERS = 100
MIN_PW = 6

_LOCK = threading.Lock()


# ─────────────────────────── 암호화 ───────────────────────────
def _fernet(key: str) -> Fernet:
    """아무 문자열이든 USERS_KEY로 쓸 수 있게, SHA-256으로 32바이트 열쇠를 만들어요."""
    raw = hashlib.sha256(("very-friends:" + key).encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def encrypt(store: dict, key: str) -> dict:
    tok = _fernet(key).encrypt(json.dumps(store, ensure_ascii=False).encode("utf-8")).decode()
    return {"v": 1, "enc": tok}


def decrypt(blob: dict | None, key: str) -> dict | None:
    """암호화된 파일을 풀어요. 열쇠가 다르면 None(덮어쓰지 않게 조심)."""
    if not isinstance(blob, dict):
        return None
    if "enc" not in blob:                # 비어 있는 새 파일
        return blob if isinstance(blob.get("users"), dict) else None
    try:
        d = json.loads(_fernet(key).decrypt(blob["enc"].encode()).decode("utf-8"))
        return d if isinstance(d, dict) else None
    except (InvalidToken, ValueError):
        return None


def empty() -> dict:
    return {"users": {}}


def load_local(key: str) -> dict | None:
    try:
        with open(FRIENDS_FILE, encoding="utf-8") as f:
            return decrypt(json.load(f), key)
    except OSError:
        return empty()
    except ValueError:
        return None


def save_local(store: dict, key: str) -> dict:
    blob = encrypt(store, key)
    with open(FRIENDS_FILE, "w", encoding="utf-8") as f:
        json.dump(blob, f)
    return blob


# ─────────────────────────── 계정 ───────────────────────────
def _now() -> str:
    return holdings.now_kst().strftime("%Y-%m-%d %H:%M")


def _pepper(store: dict) -> bytes:
    """비밀번호로 계정을 찾을 때 쓰는 비밀 값. 파일 안(암호화됨)에만 있어요."""
    if not store.get("pepper"):
        store["pepper"] = holdings._secrets.token_hex(32)
    return bytes.fromhex(store["pepper"])


def pid_of(store: dict, pw: str) -> str:
    return hmac.new(_pepper(store), (pw or "").encode("utf-8"), hashlib.sha256).hexdigest()


def valid_pw(pw: str) -> str | None:
    if len(pw or "") < MIN_PW:
        return f"비밀번호는 {MIN_PW}자 이상으로 정해 주세요."
    if len(set(pw)) < 3:
        return "같은 글자만 반복한 비밀번호는 쓸 수 없어요."
    return None


def _find(store: dict, pw: str) -> str | None:
    p = pid_of(store, pw)
    for uid, u in (store.get("users") or {}).items():
        if hmac.compare_digest(u.get("pid") or "", p):
            return uid
    return None


def _fresh_user(name: str, pid: str, pw: str) -> dict:
    return {"name": (name or "").strip()[:20], "pid": pid, "pw": holdings._hash(pw), "created": _now(),
            "groups": [{"name": "관심종목", "items": []}], "trades": [], "cash": []}


def migrate(u: dict) -> dict:
    """예전 모양(watch 목록) → 그룹 모양으로."""
    if "groups" not in u:
        u["groups"] = [{"name": "관심종목", "items": list(u.pop("watch", None) or [])}]
    u.setdefault("trades", [])
    u.setdefault("cash", [])
    if not u.get("name"):
        u["name"] = u.get("nick") or u.get("id") or ""
    return u


def sign_up(store: dict, pw: str, name: str = "") -> tuple[str | None, str | None]:
    """새 계정 → (uid, None) / 실패하면 (None, 이유). 비밀번호가 이미 쓰이면 'taken'."""
    err = valid_pw(pw)
    if err:
        return None, err
    with _LOCK:
        users = store.setdefault("users", {})
        if len(users) >= MAX_USERS:
            return None, f"계정은 {MAX_USERS}개까지만 만들 수 있어요."
        if _find(store, pw):
            return None, "taken"
        uid = uuid.uuid4().hex[:10]
        users[uid] = _fresh_user(name, pid_of(store, pw), pw)
    return uid, None


def login(store: dict, pw: str) -> str | None:
    """비밀번호만으로 들어가기 → uid 또는 None."""
    if not pw:
        return None
    uid = _find(store, pw)
    if uid and holdings._check(pw, store["users"][uid].get("pw")):
        return uid
    return None


def change_pw(store: dict, uid: str, new_pw: str) -> str | None:
    err = valid_pw(new_pw)
    if err:
        return err
    with _LOCK:
        u = (store.get("users") or {}).get(uid)
        if not u:
            return "계정이 없어요."
        other = _find(store, new_pw)
        if other and other != uid:
            return "다른 비밀번호로 정해 주세요."
        u["pid"] = pid_of(store, new_pw)
        u["pw"] = holdings._hash(new_pw)
    return None


def temp_password() -> str:
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "".join(holdings._secrets.choice(alphabet) for _ in range(8))


def delete_user(store: dict, uid: str) -> None:
    with _LOCK:
        (store.get("users") or {}).pop(uid, None)


def user(store: dict, uid: str | None) -> dict | None:
    u = (store.get("users") or {}).get(uid) if uid else None
    return migrate(u) if u else None


# ─────────────────────────── 관심종목 그룹 ───────────────────────────
def groups(store: dict, uid: str) -> list[dict]:
    u = user(store, uid)
    return u["groups"] if u else []


def n_items(u: dict) -> int:
    return sum(len(g.get("items") or []) for g in u.get("groups") or [])


def add_group(store: dict, uid: str, name: str) -> str | None:
    name = (name or "").strip()[:14]
    if not name:
        return "그룹 이름을 넣어 주세요."
    with _LOCK:
        u = user(store, uid)
        if not u:
            return "로그인이 풀렸어요."
        if any(g["name"] == name for g in u["groups"]):
            return "같은 이름의 그룹이 있어요."
        if len(u["groups"]) >= MAX_GROUPS:
            return f"그룹은 {MAX_GROUPS}개까지예요."
        u["groups"].append({"name": name, "items": []})
    return None


def rename_group(store: dict, uid: str, gi: int, name: str) -> str | None:
    name = (name or "").strip()[:14]
    with _LOCK:
        u = user(store, uid)
        if not u or not name or not (0 <= gi < len(u["groups"])):
            return "바꿀 수 없어요."
        if any(g["name"] == name for i, g in enumerate(u["groups"]) if i != gi):
            return "같은 이름의 그룹이 있어요."
        u["groups"][gi]["name"] = name
    return None


def delete_group(store: dict, uid: str, gi: int) -> None:
    """그룹을 지우면 안의 종목은 첫 그룹으로 옮겨요(그룹이 하나뿐이면 못 지워요)."""
    with _LOCK:
        u = user(store, uid)
        if not u or len(u["groups"]) <= 1 or not (0 <= gi < len(u["groups"])):
            return
        g = u["groups"].pop(gi)
        first = u["groups"][0]["items"]
        have = {x["code"] for x in first}
        first.extend(x for x in g.get("items") or [] if x["code"] not in have)


def add_item(store: dict, uid: str, gi: int, code: str, name: str, memo: str = "") -> str | None:
    with _LOCK:
        u = user(store, uid)
        if not u or not (0 <= gi < len(u["groups"])):
            return "로그인이 풀렸어요. 다시 들어와 주세요."
        items = u["groups"][gi]["items"]
        if any(x["code"] == code for x in items):
            return f"{name}은(는) 이 그룹에 이미 있어요."
        if n_items(u) >= MAX_WATCH:
            return f"관심종목은 모두 합쳐 {MAX_WATCH}개까지 담을 수 있어요."
        items.append({"code": code, "name": name, "memo": (memo or "").strip()[:60],
                      "added": holdings.now_kst().strftime("%Y-%m-%d")})
    return None


def remove_item(store: dict, uid: str, gi: int, code: str) -> None:
    with _LOCK:
        u = user(store, uid)
        if u and 0 <= gi < len(u["groups"]):
            u["groups"][gi]["items"] = [x for x in u["groups"][gi]["items"] if x["code"] != code]


def move_item(store: dict, uid: str, gi: int, code: str, to_gi: int) -> str | None:
    with _LOCK:
        u = user(store, uid)
        if not u or not (0 <= gi < len(u["groups"])) or not (0 <= to_gi < len(u["groups"])) or gi == to_gi:
            return None
        src = u["groups"][gi]["items"]
        it = next((x for x in src if x["code"] == code), None)
        if not it:
            return None
        u["groups"][gi]["items"] = [x for x in src if x["code"] != code]
        dst = u["groups"][to_gi]["items"]
        if not any(x["code"] == code for x in dst):
            dst.append(it)
    return None


def reorder_item(store: dict, uid: str, gi: int, code: str, step: int) -> None:
    with _LOCK:
        u = user(store, uid)
        if not u or not (0 <= gi < len(u["groups"])):
            return
        w = u["groups"][gi]["items"]
        i = next((i for i, x in enumerate(w) if x["code"] == code), None)
        if i is None:
            return
        j = max(0, min(len(w) - 1, i + step))
        w[i], w[j] = w[j], w[i]


def set_memo(store: dict, uid: str, gi: int, code: str, memo: str) -> None:
    with _LOCK:
        u = user(store, uid)
        if u and 0 <= gi < len(u["groups"]):
            for x in u["groups"][gi]["items"]:
                if x["code"] == code:
                    x["memo"] = (memo or "").strip()[:60]


# ─────────────────────────── 포트폴리오(매매·입출금 기록) ───────────────────────────
def add_trade(store: dict, uid: str, date: str, code: str, name: str, side: str, qty: float, price: float,
              fee: float = 0.0, cur: str = "KRW", memo: str = "") -> str | None:
    if side not in ("buy", "sell"):
        return "매수·매도를 골라 주세요."
    if not qty or qty <= 0 or not price or price <= 0:
        return "수량과 가격을 넣어 주세요."
    with _LOCK:
        u = user(store, uid)
        if not u:
            return "로그인이 풀렸어요."
        if side == "sell":
            held = positions(u["trades"]).get(code, {}).get("qty", 0)
            if qty > held + 1e-9:
                return f"가진 수량({held:,.0f}주)보다 많이 팔 수 없어요."
        if len(u["trades"]) >= MAX_TRADES:
            return "매매 기록이 너무 많아요."
        u["trades"].append({"id": uuid.uuid4().hex[:8], "date": date, "code": code, "name": name, "side": side,
                            "qty": float(qty), "price": float(price), "fee": float(fee or 0), "cur": cur,
                            "memo": (memo or "").strip()[:60], "at": _now()})
        u["trades"].sort(key=lambda t: (t["date"], t.get("at", "")))
    return None


def add_cash(store: dict, uid: str, date: str, amount: float, memo: str = "") -> str | None:
    if not amount:
        return "금액을 넣어 주세요."
    with _LOCK:
        u = user(store, uid)
        if not u:
            return "로그인이 풀렸어요."
        u["cash"].append({"id": uuid.uuid4().hex[:8], "date": date, "amount": float(amount),
                          "memo": (memo or "").strip()[:60], "at": _now()})
        u["cash"].sort(key=lambda c: (c["date"], c.get("at", "")))
    return None


def delete_record(store: dict, uid: str, kind: str, rid: str) -> None:
    with _LOCK:
        u = user(store, uid)
        if u and kind in ("trades", "cash"):
            u[kind] = [x for x in u[kind] if x.get("id") != rid]


def positions(trades: list[dict]) -> dict[str, dict]:
    """매매 기록 → 종목별 {qty, avg, cost, name, cur, realized}. 평단은 이동평균(매도해도 평단 그대로)."""
    out: dict[str, dict] = {}
    for t in sorted(trades, key=lambda t: (t["date"], t.get("at", ""))):
        p = out.setdefault(t["code"], {"qty": 0.0, "avg": 0.0, "name": t["name"], "cur": t.get("cur", "KRW"),
                                       "realized": 0.0, "fees": 0.0})
        p["fees"] += t.get("fee") or 0
        if t["side"] == "buy":
            tot = p["qty"] + t["qty"]
            p["avg"] = (p["avg"] * p["qty"] + t["price"] * t["qty"]) / tot if tot else 0.0
            p["qty"] = tot
        else:
            q = min(t["qty"], p["qty"])
            p["realized"] += (t["price"] - p["avg"]) * q - (t.get("fee") or 0)
            p["qty"] -= q
            if p["qty"] <= 1e-9:
                p["qty"], p["avg"] = 0.0, 0.0
    for p in out.values():
        p["cost"] = p["qty"] * p["avg"]
    return out
