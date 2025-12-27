"""PixiText Flask app (cleaned).

This file is a cleaned, runnable re-organization of the user's original app.py.
Main goals:
- Fix syntax errors (config.update / stray top-level code).
- Remove duplicate decorators and imports.
- Group code by feature with clear section headers.
- Keep behavior as close as possible to the original.

NOTE:
- Google Cloud Storage integration remains disabled (stub functions).
"""

# =========================
# Imports
# =========================
from __future__ import annotations

import json
import mimetypes
import os
import random
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from flask import (
    Flask,
    Response,
    abort,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    session,
    url_for,
)
from flask_session import Session
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash  # kept for compatibility (may be used in users.py)
from werkzeug.utils import secure_filename

from parser import parse_document
from users import create_user, verify_login


# =========================
# Environment & Paths
# =========================
load_dotenv()

BASE_DIR = os.path.dirname(__file__)

TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
SAVES_DIR = os.path.join(BASE_DIR, "saves")
SESSION_DIR = os.path.join(BASE_DIR, "flask_session")
DB_PATH = os.path.join(UPLOAD_DIR, "uploads.json")
SAVES_META_PATH = os.path.join(SAVES_DIR, "saves_meta.json")
USERS_DB_PATH = os.path.join(BASE_DIR, "users.json")
TRASH_DIR = os.path.join(BASE_DIR, "trash")
TRASH_UPLOADS_DIR = os.path.join(TRASH_DIR, "uploads")
TRASH_SAVES_DIR = os.path.join(TRASH_DIR, "saves")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
TRASH_LOGS_DIR = os.path.join(TRASH_DIR, "logs")

for d in (
    UPLOAD_DIR,
    SAVES_DIR,
    SESSION_DIR,
    LOGS_DIR,
    TRASH_UPLOADS_DIR,
    TRASH_SAVES_DIR,
    TRASH_LOGS_DIR,
):
    os.makedirs(d, exist_ok=True)


ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "svg"}


# =========================
# Flask App Initialization
# =========================
app = Flask(
    __name__,
    static_url_path="/static",
    static_folder=STATIC_DIR,
    template_folder=TEMPLATES_DIR,
)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# One place to set config
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY", "change-me"),  # override via env in production
    UPLOAD_FOLDER=UPLOAD_DIR,
    BUILD_VER=10.5,  # cache buster

    # Flask-Session
    SESSION_TYPE="filesystem",
    SESSION_FILE_DIR=SESSION_DIR,
    SESSION_PERMANENT=False,
    SESSION_USE_SIGNER=True,

    # Cookies
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=bool(os.getenv("RENDER")) or bool(os.getenv("CLOUDFLARE")),

    # GCS config placeholders (integration disabled)
    GCS_PROJECT_ID="",
    GCS_BUCKET_NAME="",
    GCS_UPLOAD_PREFIX="",
    GCS_SAVES_PREFIX="",
    GCS_SERVICE_ACCOUNT_KEY="",
    GCS_SERVICE_ACCOUNT_JSON="",
    GCS_SERVICE_ACCOUNT_EMAIL="",
    GCS_BROWSER_BASE_URL="",
    
    # Logs  ← ここで統一
    AUTH_LOG_PATH=os.path.join(TRASH_LOGS_DIR, "auth_log.jsonl"),
    TRASH_LOG_PATH=os.path.join(TRASH_LOGS_DIR, "trash_log.jsonl"),
)

Session(app)

storage = None
NotFound = None

# Manifest cache kept for compatibility with templates
_CLOUD_MANIFEST_CACHE: Dict[str, Any] = {"timestamp": 0.0, "value": None}
_CLOUD_MANIFEST_LOCK = threading.Lock()


# =========================
# Cloud (Disabled / Stubs)
# =========================
def gcs_upload_file(local_path: str, filename: str, *, prefix: Optional[str] = None, content_type: Optional[str] = None):
    """GCS upload stub (disabled)."""
    return None


def gcs_delete_blob(filename: str, *, prefix: Optional[str] = None):
    """GCS delete stub (disabled)."""
    return None


def load_cloud_manifest(*, force_refresh: bool = False) -> Dict[str, Any]:
    """Cloud integration disabled: return empty manifest."""
    return {
        "targets": [],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gcs": {"enabled": False},
    }


# =========================
# Common Helpers
# =========================
def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _client_ip() -> str:
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or ""


def _write_auth_log(event: dict) -> None:
    path = app.config.get("AUTH_LOG_PATH")
    if not path:
        return
    payload = {
        **event,
        "ts": int(time.time()),
        "iso": datetime.now(timezone.utc).isoformat(),
        "ip": _client_ip(),
    }
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        # logging should never break the app
        pass
    
def _write_trash_log(event: dict) -> None:
    path = app.config.get("TRASH_LOG_PATH")
    if not path:
        return
    payload = {
        **event,
        "ts": int(time.time()),
        "iso": datetime.now(timezone.utc).isoformat(),
        "ip": _client_ip(),
        "user_id": session.get("user_id"),
    }
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass



# =========================
# DB Helpers (uploads.json)
# =========================
def _load_db() -> Dict[str, Any]:
    if not os.path.exists(DB_PATH):
        return {}
    try:
        with open(DB_PATH, "r", encoding="utf-8") as f:
            raw = f.read().strip()
            return json.loads(raw) if raw else {}
    except Exception:
        return {}


def _save_db(db: Dict[str, Any]) -> None:
    tmp = DB_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DB_PATH)


def _gen_id(db: Dict[str, Any]) -> str:
    while True:
        nid = f"{random.randint(100000, 999999)}"
        if nid not in db:
            return nid


# =========================
# Saves meta (saves_meta.json)
# =========================
def _load_saves_meta() -> Dict[str, Any]:
    if not os.path.exists(SAVES_META_PATH):
        return {}
    try:
        with open(SAVES_META_PATH, "r", encoding="utf-8") as f:
            raw = f.read().strip()
            return json.loads(raw) if raw else {}
    except Exception:
        return {}


def _save_saves_meta(meta: Dict[str, Any]) -> None:
    tmp = SAVES_META_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SAVES_META_PATH)


# =========================
# Trash Helpers
# =========================
def _move_upload_to_trash(img_id: str, rec: Dict[str, Any]) -> Dict[str, Any]:
    """Move an uploaded image file into trash and mark record."""
    src = os.path.join(app.config["UPLOAD_FOLDER"], rec["stored_name"])
    dst = os.path.join(TRASH_UPLOADS_DIR, f"{img_id}__{rec['stored_name']}")

    if os.path.exists(src):
        shutil.move(src, dst)

    rec["deleted_at"] = int(time.time())
    rec["trash_path"] = os.path.basename(dst)
    return rec


# =========================
# Template Context
# =========================
@app.context_processor
def inject_cloud_links():
    manifest = load_cloud_manifest()
    return dict(
        cloud_targets=manifest.get("targets", []),
        cloud_manifest=manifest,
        gcs_upload_prefix=app.config.get("GCS_UPLOAD_PREFIX"),
        gcs_saves_prefix=app.config.get("GCS_SAVES_PREFIX"),
    )


# =========================
# Auth Gate
# =========================
@app.before_request
def require_login():
    if request.endpoint is None:
        return

    if request.endpoint in (
        "login",
        "signup",
        "static",
        "uploaded",
        "image_by_id",
        "gallery_public",
        "saves_public",
        "saves_public_view",
        "explore",
        "saves_public_raw",
    ):
        return

    if not session.get("user_id"):
        return redirect(url_for("login", next=request.full_path))


# =========================
# Cache Control
# =========================
@app.after_request
def no_cache_static(resp: Response):
    p = request.path
    if p.startswith("/static/") and (p.endswith(".css") or p.endswith(".js")):
        resp.headers["Cache-Control"] = "no-store, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp

def _move_save_to_trash(fname: str, meta_rec: dict) -> dict:
    src = os.path.join(SAVES_DIR, fname)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dst_name = f"{stamp}__{fname}"
    dst = os.path.join(TRASH_SAVES_DIR, dst_name)

    if os.path.exists(src):
        shutil.move(src, dst)

    meta_rec["deleted_at"] = int(time.time())
    meta_rec["trash_path"] = dst_name
    return meta_rec


# =========================
# Auth Routes
# =========================
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")

        try:
            uid = create_user(username, password)
        except ValueError as e:
            msg = "登録に失敗しました"
            if str(e) == "username exists":
                msg = "そのユーザー名は使われています"
            elif str(e) == "username empty":
                msg = "ユーザー名が空です"
            elif str(e) == "password empty":
                msg = "パスワードが空です"
            flash(msg)
            return redirect(url_for("signup"))

        session.clear()
        session["user_id"] = uid
        _write_auth_log({"event": "signup", "user_id": uid, "username": username})
        return redirect(url_for("index"))

    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        next_url = request.form.get("next") or url_for("index")

        uid = verify_login(username, password)
        if not uid:
            flash("ユーザー名またはパスワードが違います")
            _write_auth_log({"event": "login_failed", "username": username})
            return redirect(url_for("login", next=next_url))

        session.clear()
        session["user_id"] = uid
        _write_auth_log({"event": "login_ok", "user_id": uid, "username": username})
        return redirect(next_url)

    return render_template("login.html", next=request.args.get("next", url_for("index")))


@app.route("/logout")
def logout():
    uid = session.get("user_id")
    session.clear()
    _write_auth_log({"event": "logout", "user_id": uid})
    return redirect(url_for("login"))


@app.route("/_whoami")
def _whoami():
    return {"user_id": session.get("user_id"), "endpoint": request.endpoint}


# =========================
# Static Upload Serving
# =========================
@app.route("/uploads/<path:filename>")
def uploaded(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


# =========================
# Gallery / Images
# =========================
@app.route("/gallery")
def gallery():
    db = _load_db()
    uid = session.get("user_id")
    q = (request.args.get("q") or "").strip().lower()

    items = []
    for k, v in db.items():
        v = dict(v)
        v.setdefault("visibility", "private")

        # Hide trashed/deleted by default
        if v.get("deleted_at"):
            continue

        if v.get("owner") != uid:
            continue

        if q:
            hay = " ".join([
                str(k or ""),
                str(v.get("title") or ""),
                str(v.get("original_name") or ""),
                str(v.get("stored_name") or ""),
            ]).lower()
            if q not in hay:
                continue

        items.append({"id": k, **v})

    items.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return render_template("gallery.html", items=items, q=request.args.get("q", ""))


@app.route("/gallery/public")
def gallery_public():
    db = _load_db()
    q = (request.args.get("q") or "").strip().lower()

    items = []
    for k, v in db.items():
        v = dict(v)
        v.setdefault("visibility", "private")
        if v.get("deleted_at"):
            continue
        if v.get("visibility") != "public":
            continue

        if q:
            hay = " ".join([
                str(k or ""),
                str(v.get("title") or ""),
                str(v.get("original_name") or ""),
                str(v.get("stored_name") or ""),
            ]).lower()
            if q not in hay:
                continue

        items.append({"id": k, **v})

    items.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return render_template("gallery.html", items=items, q=request.args.get("q",""))



@app.route("/explore")
def explore():
    """Public explorer (saves/images)"""
    t = (request.args.get("type") or "saves").strip()
    q = (request.args.get("q") or "").strip().lower()
    if t not in ("saves", "images"):
        t = "saves"

    # ★ ここで users.json をロード
    try:
        with open(USERS_DB_PATH, "r", encoding="utf-8") as f:
            users_db = json.load(f).get("users", {})
    except Exception:
        users_db = {}

    if t == "saves":
        meta = _load_saves_meta()
        files = []

        for name in os.listdir(SAVES_DIR):
            if not name.lower().endswith(".txt"):
                continue

            m = meta.get(name, {})
            if m.get("deleted_at"):
                continue

            if m.get("visibility") != "public":
                continue

            if q and q not in name.lower():
                continue

            p = os.path.join(SAVES_DIR, name)
            if not os.path.isfile(p):
                continue

            st = os.stat(p)

            # ★ ここが本命：owner_id → username
            owner_id = m.get("owner", "")
            owner_name = users_db.get(owner_id, {}).get("username", owner_id)

            files.append({
                "name": name,
                "mtime": st.st_mtime,
                "mtime_str": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                "size_kb": round(st.st_size / 1024, 1),
                "owner": owner_name,   # ← username が入る
            })

        files.sort(key=lambda x: -x["mtime"])
        return render_template(
            "explore_saves.html",
            q=request.args.get("q", ""),
            files=files,
            type=t
        )


    # images
    db = _load_db()
    items = []
    for img_id, rec in db.items():
        rec = dict(rec)
        rec.setdefault("visibility", "private")
        if rec.get("deleted_at"):
            continue
        if rec.get("visibility") != "public":
            continue
        title = (rec.get("title") or rec.get("original_name") or rec.get("stored_name") or "")
        hay = f"{img_id} {title}".lower()
        if q and q not in hay:
            continue
        owner_id = rec.get("owner", "")
        owner_name = users_db.get(owner_id, {}).get("username", owner_id)
        items.append({"id": img_id, "owner_name": owner_name, **rec})
    items.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return render_template("explore_gallery.html", q=request.args.get("q", ""), items=items, type=t)


# IDで解決する画像URL: /image/123456
@app.route("/image/<img_id>")
def image_by_id(img_id):
    db = _load_db()
    rec = db.get(img_id)
    if not rec:
        abort(404)

    rec.setdefault("visibility", "private")

    if rec.get("deleted_at"):
        abort(404)

    # private は owner一致のみ
    if rec.get("visibility") == "private":
        if rec.get("owner") != session.get("user_id"):
            abort(403)

    path = os.path.join(app.config["UPLOAD_FOLDER"], rec["stored_name"])
    if not os.path.exists(path):
        abort(404)

    download_name = rec.get("original_name") or rec.get("stored_name")
    resp = send_file(path, as_attachment=False, download_name=download_name)
    resp.headers.setdefault("Cache-Control", "public, max-age=86400")
    return resp


@app.route("/images/import", methods=["POST"])
def images_import():
    uid = session.get("user_id")
    if not uid:
        return redirect(url_for("login", next=request.full_path))

    src_id = (request.form.get("img_id") or "").strip()
    db = _load_db()
    src = db.get(src_id)
    if not src:
        abort(404)
    if src.get("deleted_at"):
        abort(404)
    if (src.get("visibility") or "private") != "public":
        abort(404)

    src_path = os.path.join(app.config["UPLOAD_FOLDER"], src["stored_name"])
    if not os.path.exists(src_path):
        abort(404)

    # コピー先ファイル名（衝突回避）
    root, ext = os.path.splitext(src["stored_name"])
    cand = f"{root}_import{ext}"
    i = 1
    while os.path.exists(os.path.join(app.config["UPLOAD_FOLDER"], cand)):
        cand = f"{root}_import{i}{ext}"
        i += 1

    shutil.copy2(src_path, os.path.join(app.config["UPLOAD_FOLDER"], cand))

    new_id = _gen_id(db)
    db[new_id] = {
        "stored_name": cand,
        "original_name": src.get("original_name") or cand,
        "original_name_safe": src.get("original_name_safe") or cand,
        "title": src.get("title"),
        "ts": int(time.time()),
        "owner": uid,
        "visibility": "private",
        "imported_from": src_id,
        "imported_from_owner": src.get("owner", ""),
    }
    _save_db(db)

    flash(f"ギャラリーに追加しました: ID {new_id}")
    return redirect(url_for("gallery"))


# =========================
# Editor / Index
# =========================
@app.route("/", methods=["GET", "POST"])
def index():
    writing_mode = session.get("last_writing_mode", "horizontal")
    last_filename = session.get("last_filename", "")
    default_text = ""

    if last_filename:
        try:
            with open(os.path.join(SAVES_DIR, last_filename), "r", encoding="utf-8") as f:
                default_text = f.read()
        except Exception as e:
            flash(f"ファイル読込エラー: {e}")
            default_text = ""

    # Gallery list (owner only, newest first, skip trashed)
    db = _load_db()
    uid = session.get("user_id")
    gallery_items = [{"id": k, **v} for k, v in db.items() if v.get("owner") == uid and not v.get("deleted_at")]
    gallery_items.sort(key=lambda x: x.get("ts", 0), reverse=True)

    refresh = request.args.get("cloud_refresh") == "1"
    cloud_manifest = load_cloud_manifest(force_refresh=refresh)
    cloud_targets = cloud_manifest.get("targets", [])

    resp = make_response(render_template(
        "index.html",
        default_text=default_text,
        writing_mode=writing_mode,
        gallery_items=gallery_items,
        last_filename=last_filename,
        cloud_manifest=cloud_manifest,
        cloud_targets=cloud_targets,
    ))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


# =========================
# Upload (Images)
# =========================
@app.route("/upload", methods=["POST"])
def upload():
    file = request.files.get("file")
    if not file:
        flash("ファイルが選択されていません")
        return redirect(url_for("gallery"))

    orig_name = file.filename
    if not orig_name:
        flash("不正なファイル名です")
        return redirect(url_for("gallery"))

    if not allowed_file(orig_name):
        flash("対応していない拡張子です")
        return redirect(url_for("gallery"))

    ext = orig_name.rsplit(".", 1)[1].lower()

    db = _load_db()
    nid = _gen_id(db)

    safe_name = secure_filename(orig_name)
    root, current_ext = os.path.splitext(safe_name)

    if not current_ext:
        current_ext = f".{ext}"
    if current_ext.lower() != f".{ext}":
        root = root or "image"
        current_ext = f".{ext}"

    root = root or "image"
    candidate = f"{root}{current_ext}"
    counter = 1
    while os.path.exists(os.path.join(app.config["UPLOAD_FOLDER"], candidate)):
        candidate = f"{root}-{counter}{current_ext}"
        counter += 1

    stored_name = candidate
    path = os.path.join(app.config["UPLOAD_FOLDER"], stored_name)

    file.save(path)

    mime_type = mimetypes.guess_type(stored_name)[0] or "application/octet-stream"
    gcs_upload_file(
        path,
        stored_name,
        prefix=app.config.get("GCS_UPLOAD_PREFIX"),
        content_type=mime_type,
    )

    db[nid] = {
        "stored_name": stored_name,
        "original_name": orig_name,
        "original_name_safe": secure_filename(orig_name),
        "ts": int(time.time()),
        "owner": session.get("user_id"),
        "visibility": "private",
        "title": request.form.get("title") or None,
    }
    _save_db(db)

    flash(f"アップロード完了: ID {nid}")
    return redirect(url_for("gallery"))



# =========================
# Preview / Reading
# =========================
@app.route("/preview", methods=["GET", "POST"])
def preview():
    if request.method == "POST":
        session["last_text"] = request.form.get("text", "")
        session["last_writing_mode"] = request.form.get("writing_mode", "horizontal")
        return redirect(url_for("preview"))

    text = session.get("last_text", "")

    # ★ 追加：GETパラメータを優先して反映
    req_mode = (request.args.get("writing_mode") or "").strip()
    if req_mode in ("horizontal", "vertical"):
        session["last_writing_mode"] = req_mode

    writing_mode = session.get("last_writing_mode", "horizontal")

    if not text:
        flash("プレビューする文章がありません。先に入力してください。")
        return redirect(url_for("index"))

    try:
        pages = parse_document(text)
    except Exception as e:
        flash(f"プレビュー生成に失敗しました: {e}")
        return redirect(url_for("index"))

    p = request.args.get("p", "1")
    try:
        p = int(p)
    except ValueError:
        p = 1

    if not pages:
        page = {"text": "", "html": ""}
        p = 1
        total = 0
    else:
        p = max(1, min(len(pages), p))
        page = pages[p - 1]
        total = len(pages)

    raw_text = page.get("text", "")

    m = re.match(r"^\s*\[chapter:(.+?)\]\s*(?:\r?\n)*", raw_text)
    if m:
        chapter_title = m.group(1)
        body_text = raw_text[m.end():]
    else:
        chapter_title = None
        body_text = raw_text

    page = {**page, "chapter": chapter_title, "text": body_text}

    nums = list(range(1, total + 1)) if total > 0 else []
    prev_p = 1 if p <= 1 else p - 1
    next_p = total if p >= total else p + 1

    return render_template(
        "preview.html",
        pages=pages,
        page=page,
        p=p,
        nums=nums,
        prev_p=prev_p,
        next_p=next_p,
        total=total,
        writing_mode=writing_mode,
        text=text,
    )


@app.route("/api/preview_page", methods=["GET", "POST"])
def api_preview_page():
    payload = request.get_json(silent=True) or request.form or {}

    if request.method == "POST":
        text = payload.get("text", "")
        writing_mode = payload.get("writing_mode", "horizontal")
        session["last_text"] = text
        session["last_writing_mode"] = writing_mode
        p_param = payload.get("p")
    else:
        text = session.get("last_text", "")
        writing_mode = session.get("last_writing_mode", "horizontal")
        p_param = request.args.get("p")

    if not text:
        return jsonify(success=False, message="プレビューする文章がありません。"), 400

    try:
        p = int(p_param or 1)
    except Exception:
        p = 1

    try:
        pages = parse_document(text)
    except Exception as e:
        return jsonify(success=False, message=f"プレビュー生成に失敗しました: {e}"), 400

    total = len(pages)
    p = max(1, min(total, p))
    page = pages[p - 1]

    raw_text = page.get("text", "")
    m = re.match(r"\[chapter:(.+?)\]\s*\n*", raw_text)
    if m:
        chapter_title = m.group(1)
        body_text = raw_text[m.end():]
    else:
        chapter_title = None
        body_text = raw_text

    page = {**page, "chapter": chapter_title, "text": body_text}

    # Remove duplicated chapter rendering in html (defensive)
    html0 = page.get("html", "")
    html0 = re.sub(
        r'^\s*<[^>]*class="chapter"[^>]*>.*?</[^>]+>\s*',
        "",
        html0,
        flags=re.S
    )
    html0 = re.sub(
        r'^\s*\[chapter:(.+?)\]\s*(?:<br\s*/?>\s*)*',
        "",
        html0,
        flags=re.S | re.I
    )
    page["html"] = html0

    return jsonify(
        success=True,
        p=p,
        total=total,
        page_html=page["html"],
        page_text=page["text"],
        writing_mode=writing_mode,
    )


@app.route("/read")
def read_single():
    text = session.get("last_text", "")
    writing_mode = session.get("last_writing_mode", "horizontal")
    if not text:
        return redirect(url_for("index"))
    try:
        pages = parse_document(text)
    except Exception as e:
        flash(f"本文の読み込みに失敗しました: {e}")
        return redirect(url_for("index"))

    try:
        p = int(request.args.get("p", 1))
    except Exception:
        p = 1

    total = len(pages) or 1
    p = max(1, min(total, p))
    page = pages[p - 1] if pages else {"text": "", "html": ""}

    nums = list(range(1, total + 1))
    prev_p = 1 if p <= 1 else p - 1
    next_p = total if p >= total else p + 1

    return render_template(
        "read.html",
        page=page,
        p=p,
        nums=nums,
        total=total,
        prev_p=prev_p,
        next_p=next_p,
        writing_mode=writing_mode,
    )


# =========================
# Export
# =========================
@app.route("/export", methods=["POST"])
def export():
    text = request.form.get("text", "")
    writing_mode = request.form.get("writing_mode", "horizontal")
    session["last_text"] = text
    session["last_writing_mode"] = writing_mode
    out_path = os.path.join(BASE_DIR, "export.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    return send_file(out_path, as_attachment=True, download_name="export.txt", mimetype="text/plain")


# =========================
# Delete / Trash (Images)
# =========================
@app.route("/trash_image/<img_id>", methods=["POST"])
def trash_image(img_id):
    """Move image to trash (soft delete)."""
    db = _load_db()
    rec = db.get(img_id)
    if not rec:
        flash(f"ID {img_id} の画像が見つかりませんでした。")
        return redirect(url_for("gallery"))

    uid = session.get("user_id")
    if rec.get("owner") != uid:
        abort(403)

    try:
        rec = _move_upload_to_trash(img_id, rec)
    except Exception as e:
        flash(f"ゴミ箱移動に失敗: {e}")
        return redirect(url_for("gallery"))

    db[img_id] = rec
    _save_db(db)

    _write_trash_log({
        "event": "trash_image",
        "img_id": img_id,
        "trash_path": rec.get("trash_path"),
        "owner": uid,
    })

    flash(f"ゴミ箱に移動しました: {img_id}")
    return redirect(url_for("gallery"))


@app.route("/trash_save", methods=["POST"])
def trash_save():
    uid = session.get("user_id")
    fname = os.path.basename((request.form.get("fname") or "").strip())
    if not fname or not fname.lower().endswith(".txt"):
        abort(400)

    meta = _load_saves_meta()
    rec = meta.get(fname, {})
    if rec.get("owner") != uid:
        abort(403)

    rec = _move_save_to_trash(fname, rec)
    meta[fname] = rec
    _save_saves_meta(meta)

    _write_trash_log({
        "event": "trash_save",
        "fname": fname,
        "trash_path": rec.get("trash_path"),
    })

    flash("ゴミ箱に移動しました")
    return redirect(url_for("saves_list"))

@app.route("/delete_image/<img_id>", methods=["POST"])
def delete_image(img_id):
    return trash_image(img_id)

# @app.route("/delete_image/<img_id>", methods=["POST"])
# def delete_image(img_id):
#     """Permanent delete (kept for compatibility)."""
#     db = _load_db()
#     rec = db.get(img_id)
#     if not rec:
#         flash(f"ID {img_id} の画像が見つかりませんでした。")
#         dest = request.args.get("next", "index")
#         return redirect(url_for(dest) if dest in ("index", "gallery") else url_for("index"))

#     uid = session.get("user_id")
#     if rec.get("owner") != uid:
#         abort(403)

#     # file delete
#     try:
#         os.remove(os.path.join(app.config["UPLOAD_FOLDER"], rec["stored_name"]))
#     except FileNotFoundError:
#         pass
#     except Exception as e:
#         flash(f"ファイル削除時にエラー: {e}")

#     gcs_result = gcs_delete_blob(rec.get("stored_name"), prefix=app.config.get("GCS_UPLOAD_PREFIX"))
#     if gcs_result is False:
#         flash("クラウドバックアップ（Google Cloud Storage）の削除に失敗しました。")

#     db.pop(img_id, None)
#     _save_db(db)

#     flash(f"ID {img_id} を削除しました。")
#     dest = request.args.get("next", "index")
#     return redirect(url_for(dest) if dest in ("index", "gallery") else url_for("index"))


# =========================
# Saves
# =========================
@app.route("/save_local", methods=["POST"])
def save_local():
    text = request.form.get("text", "")
    raw_name = (request.form.get("filename", "") or "").strip()

    # sanitize filename + add .txt
    name = re.sub(r'[\\/:*?"<>|]+', "_", raw_name).replace("\0", "")
    if not name:
        name = "untitled.txt"
    if not name.lower().endswith(".txt"):
        name += ".txt"
    name = os.path.basename(name)

    path = os.path.join(SAVES_DIR, name)

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

        session["last_text"] = text
        session["last_filename"] = name

        meta = _load_saves_meta()
        rec = meta.get(name, {})
        rec.setdefault("owner", session.get("user_id"))
        rec.setdefault("visibility", "private")
        rec.setdefault("pinned", False)
        rec["updated_at"] = int(time.time())
        meta[name] = rec
        _save_saves_meta(meta)

        payload: Dict[str, Any] = dict(success=True, message=f"「{name}」を保存しました", filename=name)

        gcs_result = gcs_upload_file(path, name, prefix=app.config.get("GCS_SAVES_PREFIX"), content_type="text/plain; charset=utf-8")
        if gcs_result:
            payload["cloud_synced"] = True
        elif gcs_result is False:
            payload["cloud_warning"] = "Google Cloud Storage へのバックアップに失敗しました。設定を確認してください。"

        return jsonify(**payload)
    except Exception as e:
        return jsonify(success=False, message=f"保存に失敗：{e}"), 500


@app.route("/saves")
def saves_list():
    uid = session.get("user_id")
    meta = _load_saves_meta()
    q = (request.args.get("q") or "").strip().lower()

    files = []
    try:
        for name in os.listdir(SAVES_DIR):
            if not name.lower().endswith(".txt"):
                continue

            if q and (q not in name.lower()):
                continue

            p = os.path.join(SAVES_DIR, name)
            if not os.path.isfile(p):
                continue

            m = meta.get(name, {})
            if m.get("deleted_at"):
                continue
            if m.get("owner") != uid:
                continue

            st = os.stat(p)
            files.append({
                "name": name,
                "size": st.st_size,
                "size_kb": round(st.st_size / 1024, 1),
                "mtime": st.st_mtime,
                "mtime_str": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                "visibility": m.get("visibility", "private"),
                "pinned": bool(m.get("pinned", False)),
            })

        files.sort(key=lambda x: (not x["pinned"], -x["mtime"]))
    except Exception as e:
        flash(f"保存一覧の取得に失敗しました: {e}")
        files = []

    return render_template("saves.html", files=files, q=request.args.get("q", ""))


@app.route("/saves/open")
def saves_open():
    uid = session.get("user_id")
    fname = os.path.basename((request.args.get("fname", "") or "").strip())

    if not fname or not fname.lower().endswith(".txt"):
        flash("不正なファイル名です")
        return redirect(url_for("saves_list"))

    path = os.path.join(SAVES_DIR, fname)
    if not os.path.exists(path):
        flash("ファイルが見つかりません")
        return redirect(url_for("saves_list"))

    meta = _load_saves_meta()
    rec = meta.get(fname)

    if not rec or rec.get("owner") != uid:
        flash("このファイルを開く権限がありません")
        return redirect(url_for("saves_list"))

    session["last_filename"] = fname
    flash(f"読み込みました: {fname}")
    return redirect(url_for("index"))

@app.get("/_raw/<path:fname>")
def saves_public_raw(fname):
    fname = os.path.basename((fname or "").strip())
    if not fname or not fname.lower().endswith(".txt"):
        abort(404)

    meta = _load_saves_meta()
    m = meta.get(fname, {})
    if m.get("deleted_at"):
        abort(404)
    if m.get("visibility") != "public":
        abort(404)

    p = os.path.join(SAVES_DIR, fname)
    if not os.path.isfile(p):
        abort(404)

    text = Path(p).read_text(encoding="utf-8", errors="replace")
    return Response(text, mimetype="text/plain; charset=utf-8")


@app.route("/saves/delete", methods=["POST"])
def saves_delete():
    return trash_save()



@app.route("/saves/visibility", methods=["POST"])
def saves_set_visibility():
    uid = session.get("user_id")
    fname = os.path.basename((request.form.get("fname") or "").strip())
    vis = (request.form.get("visibility") or "private").strip()

    if not fname or not fname.lower().endswith(".txt"):
        abort(400)
    if vis not in ("private", "unlisted", "public"):
        abort(400)

    meta = _load_saves_meta()
    rec = meta.get(fname, {})

    if rec.get("owner") and rec.get("owner") != uid:
        abort(403)

    rec.setdefault("owner", uid)
    rec.setdefault("pinned", False)
    rec["visibility"] = vis
    rec["updated_at"] = int(time.time())
    meta[fname] = rec
    _save_saves_meta(meta)

    flash(f"{fname} の公開設定を {vis} にしました")
    return redirect(url_for("saves_list"))
    
@app.route("/image/<img_id>/visibility", methods=["POST"])
def set_visibility(img_id):
    uid = session.get("user_id")
    vis = (request.form.get("visibility") or "private").strip()

    db = _load_db()
    rec = db.get(img_id)

    if not rec:
        abort(404)
    if rec.get("owner") != uid:
        abort(403)

    rec["visibility"] = vis
    db[img_id] = rec
    _save_db(db)

    return redirect(url_for("gallery"))



@app.route("/saves/pin", methods=["POST"])
def saves_toggle_pin():
    uid = session.get("user_id")
    fname = os.path.basename((request.form.get("fname") or "").strip())

    if not fname or not fname.lower().endswith(".txt"):
        abort(400)

    meta = _load_saves_meta()
    rec = meta.get(fname, {})

    if rec.get("owner") and rec.get("owner") != uid:
        abort(403)

    rec.setdefault("owner", uid)
    rec.setdefault("visibility", "private")
    rec["pinned"] = not bool(rec.get("pinned", False))
    rec["updated_at"] = int(time.time())
    meta[fname] = rec
    _save_saves_meta(meta)

    return redirect(url_for("saves_list"))


@app.route("/saves/public")
def saves_public():
    meta = _load_saves_meta()
    q = (request.args.get("q") or "").strip().lower()

    files = []
    try:
        for name in os.listdir(SAVES_DIR):
            if not name.lower().endswith(".txt"):
                continue

            m = meta.get(name, {})
            if m.get("deleted_at"):
                continue

            if m.get("visibility") != "public":
                continue

            if q and (q not in name.lower()):
                continue

            p = os.path.join(SAVES_DIR, name)
            if not os.path.isfile(p):
                continue

            st = os.stat(p)
            files.append({
                "name": name,
                "size": st.st_size,
                "size_kb": round(st.st_size / 1024, 1),
                "mtime": st.st_mtime,
                "mtime_str": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
            })

        files.sort(key=lambda x: -x["mtime"])
    except Exception as e:
        flash(f"公開保存一覧の取得に失敗しました: {e}")
        files = []

    return render_template("saves_public.html", files=files, q=request.args.get("q", ""))


@app.route("/saves/public/view")
def saves_public_view():
    # 1) fname を先に確定
    fname = request.args.get("fname", "")
    if not fname:
        abort(404)

    # 2) メタ参照
    meta = _load_saves_meta()
    m = meta.get(fname, {})
    if m.get("deleted_at"):
        abort(404)
    if m.get("visibility") != "public":
        abort(404)

    # 3) 本文ファイル存在チェック
    path = os.path.join(SAVES_DIR, fname)
    if not os.path.isfile(path):
        abort(404)

    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    # 4) ページ分割
    pages = parse_document(text)
    if not pages:
        abort(404)

    # 5) ページ番号（不正値対策）
    try:
        p = int(request.args.get("p", 1))
    except (TypeError, ValueError):
        p = 1
    p = max(1, min(p, len(pages)))

    page = pages[p - 1]
    prev_p = max(1, p - 1)
    next_p = min(len(pages), p + 1)

    nums = range(1, len(pages) + 1)
    writing_mode = request.args.get("writing_mode", "horizontal")

    return render_template(
        "saves_public_view.html",
        fname=fname,
        page=page,
        p=p,
        prev_p=prev_p,
        next_p=next_p,
        nums=nums,
        writing_mode=writing_mode,
    )


@app.context_processor
def inject_preview_like():
    ep = request.endpoint or ""
    is_preview_like = ep in ("preview", "saves_public_view")

    # 画面の現在値（無ければ安全なデフォルト）
    p = request.args.get("p") or 1
    fname = request.args.get("fname") or ""

    # writing_mode は URL を最優先、無ければ session の最後、無ければ horizontal
    mode = (request.args.get("writing_mode") or session.get("last_writing_mode") or "horizontal").strip()
    if mode not in ("horizontal", "vertical"):
        mode = "horizontal"

    return {
        "is_preview_like": is_preview_like,
        "ui_p": p,
        "ui_fname": fname,
        "ui_writing_mode": mode,
        "ui_endpoint": ep,
    }


@app.route("/saves/import", methods=["POST"])
def saves_import():
    uid = session.get("user_id")
    if not uid:
        return redirect(url_for("login", next=request.full_path))

    fname = os.path.basename((request.form.get("fname") or "").strip())
    if not fname.lower().endswith(".txt"):
        abort(400)

    meta = _load_saves_meta()
    src_rec = meta.get(fname, {})
    if src_rec.get("visibility") != "public":
        abort(404)

    src_path = os.path.join(SAVES_DIR, fname)
    if not os.path.exists(src_path):
        abort(404)

    base, ext = os.path.splitext(fname)
    new_name = f"{base}_import{ext}"
    i = 1
    while os.path.exists(os.path.join(SAVES_DIR, new_name)):
        new_name = f"{base}_import{i}{ext}"
        i += 1

    shutil.copy2(src_path, os.path.join(SAVES_DIR, new_name))

    meta[new_name] = {
        "owner": uid,
        "visibility": "private",
        "pinned": False,
        "updated_at": int(time.time()),
        "imported_from": fname,
        "imported_from_owner": src_rec.get("owner", ""),
    }
    _save_saves_meta(meta)

    flash(f"取り込みました: {new_name}")
    return redirect(url_for("saves_list"))


@app.route("/saves/auto_open")
def saves_auto_open():
    return ("", 204)


# =========================
# Entrypoint
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "7860"))  # Render's PORT respected
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)

