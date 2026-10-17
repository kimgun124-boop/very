"""친구 계정 · 관심종목 저장소.

- 친구는 아이디/비밀번호로 로그인하고, 자기 관심종목만 따로 담아요.
- 비밀번호는 그대로 저장하지 않고 소금(salt)을 친 해시로만 저장해요(holdings와 같은 방식).
- 저장소(GitHub)가 공개(Public)라서, 파일 전체를 USERS_KEY로 암호화해서 올려요.
  USERS_KEY를 모르면 저장소를 열어 봐도 누가 무슨 종목을 담았는지, 비밀번호 해시도 알 수 없어요.
- 서버(Streamlit) 하나가 모든 접속을 같이 받으니, 메모리에 한 벌을 두고 잠금(lock)을 걸어 고쳐요.
  그래서 친구 둘이 동시에 저장해도 서로 덮어쓰지 않아요.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading

from cryptography.fernet import Fernet, InvalidToken

import holdings

FRIENDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_friends.json")
GH_PATH = "user_friends.json"
MAX_WATCH = 40                       # 한 사람당 관심종목 최대 개수(앱이 느려지지 않게)
MAX_USERS = 50
ID_RE = re.compile(r"^[A-Za-z0-9가-힣_.-]{2,20}$")

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
def norm_id(uid: str) -> str:
    return (uid or "").strip().lower()


def valid_id(uid: str) -> str | None:
    """문제가 있으면 이유를 돌려줘요."""
    if not ID_RE.match((uid or "").strip()):
        return "아이디는 2~20자, 한글·영문·숫자·_ . - 만 쓸 수 있어요."
    return None


def valid_pw(pw: str) -> str | None:
    if len(pw or "") < 6:
        return "비밀번호는 6자 이상으로 정해 주세요."
    return None


def sign_up(store: dict, uid: str, pw: str, nick: str = "") -> str | None:
    """새 계정. 실패하면 이유(문자열), 성공하면 None."""
    err = valid_id(uid) or valid_pw(pw)
    if err:
        return err
    k = norm_id(uid)
    with _LOCK:
        users = store.setdefault("users", {})
        if k in users:
            return "이미 있는 아이디예요."
        if len(users) >= MAX_USERS:
            return f"계정은 {MAX_USERS}개까지만 만들 수 있어요."
        users[k] = {"id": uid.strip(), "nick": (nick or uid).strip()[:20], "pw": holdings._hash(pw),
                    "created": holdings.now_kst().strftime("%Y-%m-%d %H:%M"), "watch": []}
    return None


def login(store: dict, uid: str, pw: str) -> bool:
    u = (store.get("users") or {}).get(norm_id(uid))
    return bool(u) and holdings._check(pw, u.get("pw"))


def change_pw(store: dict, uid: str, new_pw: str) -> str | None:
    err = valid_pw(new_pw)
    if err:
        return err
    with _LOCK:
        u = (store.get("users") or {}).get(norm_id(uid))
        if not u:
            return "계정이 없어요."
        u["pw"] = holdings._hash(new_pw)
    return None


def delete_user(store: dict, uid: str) -> None:
    with _LOCK:
        (store.get("users") or {}).pop(norm_id(uid), None)


def temp_password() -> str:
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "".join(holdings._secrets.choice(alphabet) for _ in range(8))


def user(store: dict, uid: str) -> dict | None:
    return (store.get("users") or {}).get(norm_id(uid))


# ─────────────────────────── 관심종목 ───────────────────────────
def watchlist(store: dict, uid: str) -> list[dict]:
    u = user(store, uid)
    return list(u.get("watch") or []) if u else []


def add_watch(store: dict, uid: str, code: str, name: str, memo: str = "") -> str | None:
    with _LOCK:
        u = user(store, uid)
        if not u:
            return "로그인이 풀렸어요. 다시 로그인해 주세요."
        w = u.setdefault("watch", [])
        if any(x["code"] == code for x in w):
            return f"{name}은(는) 이미 담겨 있어요."
        if len(w) >= MAX_WATCH:
            return f"관심종목은 {MAX_WATCH}개까지 담을 수 있어요."
        w.append({"code": code, "name": name, "memo": (memo or "").strip()[:60],
                  "added": holdings.now_kst().strftime("%Y-%m-%d")})
    return None


def remove_watch(store: dict, uid: str, code: str) -> None:
    with _LOCK:
        u = user(store, uid)
        if u:
            u["watch"] = [x for x in u.get("watch") or [] if x["code"] != code]


def move_watch(store: dict, uid: str, code: str, step: int) -> None:
    with _LOCK:
        u = user(store, uid)
        if not u:
            return
        w = u.get("watch") or []
        i = next((i for i, x in enumerate(w) if x["code"] == code), None)
        if i is None:
            return
        j = max(0, min(len(w) - 1, i + step))
        w[i], w[j] = w[j], w[i]


def set_memo(store: dict, uid: str, code: str, memo: str) -> None:
    with _LOCK:
        for x in (user(store, uid) or {}).get("watch") or []:
            if x["code"] == code:
                x["memo"] = (memo or "").strip()[:60]
