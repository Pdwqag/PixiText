import os
import json
import random
import re
import time
import mimetypes
import threading
import re
from datetime import datetime, timezone
from werkzeug.security import check_password_hash
from dotenv import load_dotenv
load_dotenv()
import secrets
from pathlib import Path
from flask import Response
from users import create_user, verify_login
from werkzeug.utils import secure_filename





from flask import (
    Flask,
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
from functools import wraps

from parser import parse_document

storage = None
NotFound = None
# Google Cloud Storage 関連のコードは一時的に無効化しています。
# 旧実装では importlib で google.cloud.storage を動的ロードしていました。

BASE_DIR = os.path.dirname(__file__)

TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
SAVES_DIR = os.path.join(BASE_DIR, "saves")
SESSION_DIR = os.path.join(BASE_DIR, "flask_session")
DB_PATH = os.path.join(UPLOAD_DIR, "uploads.json")
SAVES_META_PATH = os.path.join(SAVES_DIR, "saves_meta.json")

# 必要なディレクトリは必ず作成
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(SAVES_DIR, exist_ok=True)
os.makedirs(SESSION_DIR, exist_ok=True)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "svg"}

# 1) まずアプリ生成
app = Flask(
    __name__,
    static_url_path="/static",
    static_folder=STATIC_DIR,
    template_folder=TEMPLATES_DIR,
)

# 2) 設定をまとめて投入（重複を避ける）
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY", "change-me"),  # Renderなら環境変数で上書き
    UPLOAD_FOLDER=UPLOAD_DIR,
    SESSION_TYPE="filesystem",
    SESSION_FILE_DIR=SESSION_DIR,
    BUILD_VER=10.3,  # キャッシュバスター
    # Google Cloud Storage 連携は停止中。必要になったら環境変数を再度読み込む。
    GCS_PROJECT_ID="",
    GCS_BUCKET_NAME="",
    GCS_UPLOAD_PREFIX="",
    GCS_SAVES_PREFIX="",
    GCS_SERVICE_ACCOUNT_KEY="",
    GCS_SERVICE_ACCOUNT_JSON="",
    GCS_SERVICE_ACCOUNT_EMAIL="",
    GCS_BROWSER_BASE_URL="",
    SESSION_PERMANENT = False,
    PERMANENT_SESSION_LIFETIME = 0,
    SESSION_USE_SIGNER = True,
    SESSION_COOKIE_SAMESITE = "Lax",
    SESSION_COOKIE_HTTPONLY = True,
    SESSION_COOKIE_SECURE = bool(os.getenv("RENDER")) or bool(os.getenv("CLOUDFLARE")),
    AUTH_LOG_PATH=os.path.join(BASE_DIR, "auth_log.jsonl"),
)

# 3) Flask-Session を初期化（requirements.txt に Flask-Session を入れること）
Session(app)


_CLOUD_MANIFEST_CACHE = {"timestamp": 0.0, "value": None}
_CLOUD_MANIFEST_LOCK = threading.Lock()


def gcs_upload_file(local_path, filename, *, prefix=None, content_type=None):
    """Google Cloud Storage 連携のスタブ。

    旧実装では _ensure_gcs_bucket() 経由でアップロードしていましたが、
    現在はクラウド連携を停止しているため何も行いません。
    """

    # 旧実装参考:
    # bucket = _ensure_gcs_bucket()
    # if bucket is None:
    #     return None
    # remote_path = _gcs_build_remote_path(prefix, filename)
    # blob = bucket.blob(remote_path)
    # blob.upload_from_filename(local_path, content_type=content_type)
    return None


def gcs_delete_blob(filename, *, prefix=None):
    """Google Cloud Storage 連携のスタブ (削除処理無効化)。"""

    # 旧実装参考:
    # bucket = _ensure_gcs_bucket()
    # blob = bucket.blob(remote_path)
    # blob.delete()
    return None


def load_cloud_manifest(*, force_refresh=False):
    """クラウド連携が無効なため、空のマニフェストを返す。"""
    return {
        "targets": [],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gcs": {"enabled": False},
    }

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
    ):
        return

    if not session.get("user_id"):
        return redirect(url_for("login", next=request.full_path))




@app.after_request
def _no_cache_static_css(resp):
    if request.path.endswith("/static/style.css"):
        resp.headers["Cache-Control"] = "no-store"
    return resp



def allowed_file(fn): return "." in fn and fn.rsplit(".",1)[1].lower() in ALLOWED_EXTENSIONS

# === テンプレート共通変数 ===
def _client_ip():
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or ""

def _write_auth_log(event: dict):
    path = app.config.get("AUTH_LOG_PATH")
    if not path:
        return
    event = {
        **event,
        "ts": int(time.time()),
        "iso": datetime.now(timezone.utc).isoformat(),
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")

@app.context_processor
def inject_cloud_links():
    manifest = load_cloud_manifest()
    return dict(
        cloud_targets=manifest.get("targets", []),
        cloud_manifest=manifest,
        gcs_upload_prefix=app.config.get("GCS_UPLOAD_PREFIX"),
        gcs_saves_prefix=app.config.get("GCS_SAVES_PREFIX"),
    )


# --- 簡易DB ---
def _load_db():
    if not os.path.exists(DB_PATH): return {}
    with open(DB_PATH, "r", encoding="utf-8") as f: return json.load(f)

def _save_db(db):
    with open(DB_PATH, "w", encoding="utf-8") as f: json.dump(db, f, ensure_ascii=False, indent=2)

def _gen_id(db):
    while True:
        nid = f"{random.randint(100000,999999)}"
        if nid not in db: return nid

def _load_saves_meta():
    if not os.path.exists(SAVES_META_PATH):
        return {}
    try:
        with open(SAVES_META_PATH, "r", encoding="utf-8") as f:
            raw = f.read().strip()
            if not raw:
                return {}
            return json.loads(raw)
    except Exception:
        return {}

def _save_saves_meta(db):
    tmp = SAVES_META_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SAVES_META_PATH)



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
            return redirect(url_for("login", next=next_url))

        session.clear()
        session["user_id"] = uid
        return redirect(next_url)

    return render_template("login.html", next=request.args.get("next", url_for("index")))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/_whoami")
def _whoami():
    return {"user_id": session.get("user_id"), "endpoint": request.endpoint}


@app.route("/uploads/<path:filename>")
def uploaded(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


# ギャラリー（一覧）
@app.route("/gallery")
def gallery():
    db = _load_db()
    uid = session.get("user_id")

    items = []
    for k, v in db.items():
        v = dict(v)
        v.setdefault("visibility", "private")
        # 古いデータは owner がないので、とりあえず自分のギャラリーに出さない（安全）
        # → 取り込みたいなら「移行」で owner を付ける
        if v.get("owner") != uid:
            continue
        items.append({"id": k, **v})

    items.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return render_template("gallery.html", items=items)

@app.route("/gallery/public")
def gallery_public():
    db = _load_db()
    items = []
    for k, v in db.items():
        v = dict(v)
        v.setdefault("visibility", "private")
        if v.get("visibility") != "public":
            continue
        items.append({"id": k, **v})
    items.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return render_template("gallery.html", items=items)

# IDで解決する画像URL: /image/123456
@app.route("/image/<img_id>")
def image_by_id(img_id):
    db = _load_db()
    rec = db.get(img_id)
    if not rec:
        abort(404)

    # --- 互換（古いデータ救済） ---
    rec.setdefault("visibility", "private")

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


@app.route("/image/<img_id>/visibility", methods=["POST"])
def set_visibility(img_id):
    db = _load_db()
    rec = db.get(img_id)
    if not rec:
        abort(404)

    uid = session.get("user_id")
    rec.setdefault("visibility", "private")

    if rec.get("owner") != uid:
        abort(403)

    vis = (request.form.get("visibility") or "private").strip()
    if vis not in ("private", "unlisted", "public"):
        abort(400)

    rec["visibility"] = vis
    db[img_id] = rec
    _save_db(db)

    flash(f"公開設定を {vis} にしました")
    return redirect(url_for("gallery"))

@app.route("/", methods=["GET","POST"])
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

    # ★ ギャラリー用の一覧（新しい順）
    db = _load_db()
    uid = session.get("user_id")
    gallery_items = [{"id": k, **v} for k, v in db.items() if v.get("owner") == uid]
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

@app.route("/upload", methods=["POST"])
def upload():
    file = request.files.get("file")
    if not file or file.filename == "":
        flash("ファイルが選択されていません"); return redirect(url_for("index"))
    if not allowed_file(file.filename):
        flash("対応していない拡張子です"); return redirect(url_for("index"))

    # 元名・拡張子
    orig_name = file.filename
    ext = orig_name.rsplit(".",1)[1].lower()

    db = _load_db()
    nid = _gen_id(db)
    safe_name = secure_filename(orig_name)
    if not safe_name:
        safe_name = f"image.{ext}"

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
    gcs_result = gcs_upload_file(
        path,
        stored_name,
        prefix=app.config.get("GCS_UPLOAD_PREFIX"),
        content_type=mime_type,
    )
    if gcs_result is False:
        flash("クラウドバックアップ（Google Cloud Storage）に失敗しました。設定を確認してください。")

    # DB登録
    db[nid] = {
        "stored_name": stored_name,
        "original_name": orig_name,
        "original_name_safe": secure_filename(orig_name),
        "ts": int(time.time()),
        "owner": session.get("user_id"),
        "visibility": "private",
    }
    _save_db(db)

    flash(f"アップロード完了: ID {nid}")
    return redirect(url_for("index"))  # アップ後は一覧へ

@app.route("/preview", methods=["GET", "POST"])
def preview():
    if request.method == "POST":
        session["last_text"] = request.form.get("text", "")
        session["last_writing_mode"] = request.form.get("writing_mode", "horizontal")
        return redirect(url_for("preview"))

    text = session.get("last_text", "")
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

    page = {
        **page,
        "chapter": chapter_title,
        "text": body_text,
    }


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

    page = {
        **page,
        "chapter": chapter_title,
        "text": body_text,
    }

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



@app.route("/export", methods=["POST"])
def export():
    text = request.form.get("text","")
    writing_mode = request.form.get("writing_mode","horizontal")
    session['last_text'] = text; session['last_writing_mode'] = writing_mode
    out_path = os.path.join(BASE_DIR, "export.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    return send_file(out_path, as_attachment=True, download_name="export.txt", mimetype="text/plain")

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

@app.route("/delete_image/<img_id>", methods=["POST"])
def delete_image(img_id):
    db = _load_db()
    rec = db.get(img_id)
    if not rec:
        flash(f"ID {img_id} の画像が見つかりませんでした。")
        dest = request.args.get("next", "index")
        return redirect(url_for(dest) if dest in ("index", "gallery") else url_for("index"))

    uid = session.get("user_id")
    if rec.get("owner") != uid:
        abort(403)

    # ファイル削除（存在しなくてもスルー）
    try:
        os.remove(os.path.join(app.config["UPLOAD_FOLDER"], rec["stored_name"]))
    except FileNotFoundError:
        pass
    except Exception as e:
        flash(f"ファイル削除時にエラー: {e}")

    gcs_result = gcs_delete_blob(
        rec.get("stored_name"),
        prefix=app.config.get("GCS_UPLOAD_PREFIX"),
    )
    if gcs_result is False:
        flash("クラウドバックアップ（Google Cloud Storage）の削除に失敗しました。")

    db.pop(img_id, None)
    _save_db(db)

    flash(f"ID {img_id} を削除しました。")
    dest = request.args.get("next", "index")
    return redirect(url_for(dest) if dest in ("index", "gallery") else url_for("index"))


# 末尾の他ルートと同じ場所に追記
# === 保存をエディタへ読み込む =========================
@app.route("/saves/open")
def saves_open():
    fname = request.args.get("fname", "").strip()
    fname = os.path.basename(fname)
    if not fname or not fname.lower().endswith(".txt"):
        flash("不正なファイル名です"); return redirect(url_for("saves_list"))

    path = os.path.join(SAVES_DIR, fname)
    if not os.path.exists(path):
        flash("ファイルが見つかりません"); return redirect(url_for("saves_list"))

    # ★ ここで本文は session に入れない
    session["last_filename"] = fname
    flash(f"読み込みました: {fname}")
    return redirect(url_for("index"))


# === 保存ファイルを削除 ===============================
@app.route("/saves/delete", methods=["POST"])
def saves_delete():
    fname = request.form.get("fname", "").strip()
    fname = os.path.basename(fname)
    if not fname or not fname.lower().endswith(".txt"):
        flash("不正なファイル名です"); return redirect(url_for("saves_list"))

    path = os.path.join(SAVES_DIR, fname)
    try:
        if os.path.exists(path):
            os.remove(path)
            flash(f"削除しました: {fname}")
        else:
            flash("ファイルが見つかりません")
    except Exception as e:
        flash(f"削除に失敗しました: {e}")
        remove_remote = False
    else:
        remove_remote = True

    if remove_remote:
        gcs_result = gcs_delete_blob(
            fname,
            prefix=app.config.get("GCS_SAVES_PREFIX"),
        )
        if gcs_result is False:
            flash("クラウドバックアップ（Google Cloud Storage）の削除に失敗しました。")
    return redirect(url_for("saves_list"))

@app.route("/save_local", methods=["POST"])
def save_local():
    import re, os
    text = request.form.get("text", "")
    raw_name = (request.form.get("filename", "") or "").strip()

    # ファイル名サニタイズ + .txt 付与
    name = re.sub(r'[\\/:*?"<>|]+', "_", raw_name).replace("\0","")
    if not name:
        name = "untitled.txt"
    if not name.lower().endswith(".txt"):
        name += ".txt"
    name = os.path.basename(name)

    saves_dir = os.path.join(BASE_DIR, "saves")
    os.makedirs(saves_dir, exist_ok=True)
    path = os.path.join(saves_dir, name)

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

        payload = dict(
            success=True,
            message=f"「{name}」を保存しました",
            filename=name,
        )
        gcs_result = gcs_upload_file(
            path,
            name,
            prefix=app.config.get("GCS_SAVES_PREFIX"),
            content_type="text/plain; charset=utf-8",
        )
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

    files = []
    try:
        for name in os.listdir(SAVES_DIR):
            if not name.lower().endswith(".txt"):
                continue

            p = os.path.join(SAVES_DIR, name)
            if not os.path.isfile(p):
                continue

            m = meta.get(name, {})
            # owner が付いていて、他人のものなら表示しない
            if m.get("owner") and m.get("owner") != uid:
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

        # ピン → 新しい順
        files.sort(key=lambda x: (not x["pinned"], -x["mtime"]))

    except Exception as e:
        flash(f"保存一覧の取得に失敗しました: {e}")
        files = []

    return render_template("saves.html", files=files)

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


@app.route("/saves/auto_open")
def saves_auto_open():
    # 何もしない：204 No Contentで返す
    return ("", 204)

@app.after_request
def _no_cache_static(resp):
    from flask import request
    p = request.path
    # CSS/JS は常に最新版
    if p.startswith('/static/') and (p.endswith('.css') or p.endswith('.js')):
        resp.headers['Cache-Control'] = 'no-store, max-age=0'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
    return resp

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", "7860"))  # ← Render が渡すPORTを尊重
    # 0.0.0.0 で待ち受け（127.0.0.1固定はNG）
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)

