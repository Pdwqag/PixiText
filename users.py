from pathlib import Path
import json
import time
import secrets
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = Path(__file__).resolve().parent
USERS_DB = BASE_DIR / "users.json"

def load_users():
    empty = {"users": {}}
    if not USERS_DB.exists():
        return empty

    try:
        raw = USERS_DB.read_text(encoding="utf-8").strip()
        if not raw:
            return empty
        data = json.loads(raw)
    except Exception:
        return empty

    if not isinstance(data, dict):
        return empty

    # 旧形式（uid直下）を救済したいならこれも入れる
    if "users" not in data and all(isinstance(v, dict) for v in data.values()):
        data = {"users": data}

    if "users" not in data or not isinstance(data["users"], dict):
        data["users"] = {}

    return data


def save_users(db):
    USERS_DB.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")


def find_by_username(username: str):
    db = load_users()
    users = db.get("users", {})
    for uid, u in users.items():
        if u.get("username") == username:
            return uid, u
    return None, None


def create_user(username: str, password: str):
    username = (username or "").strip()
    if not username:
        raise ValueError("username empty")
    if not password:
        raise ValueError("password empty")

    db = load_users()
    if any(u.get("username") == username for u in db["users"].values()):
        raise ValueError("username exists")

    uid = f"u_{int(time.time() * 1000)}"
    db["users"][uid] = {
        "username": username,
        "password_hash": generate_password_hash(password),
        "created_at": int(time.time())
    }
    save_users(db)
    return uid

def verify_login(username: str, password: str):
    uid, u = find_by_username((username or "").strip())
    if not u:
        return None
    if check_password_hash(u.get("password_hash", ""), password or ""):
        return uid
    return None

