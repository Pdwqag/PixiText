import os
import json
import random
import re
import time
import mimetypes
import threading
import importlib
from datetime import datetime, timezone

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
from werkzeug.utils import secure_filename

from parser import parse_document, to_html_document

storage = None
NotFound = None

_google_pkg = importlib.util.find_spec("google")
if _google_pkg is not None:  # pragma: no branch - import resolution only
    _storage_spec = importlib.util.find_spec("google.cloud.storage")
    if _storage_spec is not None:
        storage = importlib.import_module("google.cloud.storage")  # type: ignore[assignment]

    _exceptions_spec = importlib.util.find_spec("google.api_core.exceptions")
    if _exceptions_spec is not None:
        NotFound = getattr(importlib.import_module("google.api_core.exceptions"), "NotFound", None)

BASE_DIR = os.path.dirname(__file__)
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
SAVES_DIR = os.path.join(BASE_DIR, "saves")
SESSION_DIR = os.path.join(BASE_DIR, "flask_session")
DB_PATH = os.path.join(UPLOAD_DIR, "uploads.json")

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
    SESSION_PERMANENT=False,
    BUILD_VER=24,  # キャッシュバスター
    SYNC_UPLOADS_URL=os.getenv("SYNC_UPLOADS_URL", "https://ln5.sync.com/dl/6643d5940#v74nbenq-egdk7g8k-fnxm268q-a2qmhqnh"),
    SYNC_SAVES_URL=os.getenv("SYNC_SAVES_URL", "https://ln5.sync.com/dl/09e74e690#7ufepdfq-nxtt4d66-y5pqdnsy-rvub53cy"),
    GCS_PROJECT_ID=os.getenv("GCS_PROJECT_ID", "PixiText"),
    GCS_BUCKET_NAME=os.getenv("GCS_BUCKET_NAME", "pixitext-storage"),
    GCS_UPLOAD_PREFIX=os.getenv("GCS_UPLOAD_PREFIX", "uploads"),
    GCS_SAVES_PREFIX=os.getenv("GCS_SAVES_PREFIX", "saves"),
    GCS_SERVICE_ACCOUNT_KEY=os.getenv(
        "GCS_SERVICE_ACCOUNT_KEY",
        os.path.join(BASE_DIR, "pixitext-475704-6c5d65f6c0cf.json"),
    ),
    GCS_SERVICE_ACCOUNT_EMAIL=os.getenv(
        "GCS_SERVICE_ACCOUNT_EMAIL",
        "pikusaitekisuto@pixitext-475704.iam.gserviceaccount.com",
    ),
    GCS_BROWSER_BASE_URL=os.getenv("GCS_BROWSER_BASE_URL", ""),
)

# 3) Flask-Session を初期化（requirements.txt に Flask-Session を入れること）
Session(app)


_GCS_LOCK = threading.Lock()
_GCS_STATE = {"bucket": None, "checked": False, "error": None}
_CLOUD_MANIFEST_CACHE = {"timestamp": 0.0, "value": None}
_CLOUD_MANIFEST_LOCK = threading.Lock()


def _gcs_configured():
    return storage is not None and bool(app.config.get("GCS_BUCKET_NAME"))


def _ensure_gcs_bucket(force=False):
    global _GCS_STATE
    if not _gcs_configured():
        return None
    with _GCS_LOCK:
        if _GCS_STATE["bucket"] is not None and not force:
            return _GCS_STATE["bucket"]
        if _GCS_STATE["checked"] and _GCS_STATE["bucket"] is None and not force:
            return None

        bucket_name = app.config.get("GCS_BUCKET_NAME")
        project_id = app.config.get("GCS_PROJECT_ID") or None
        key_path = app.config.get("GCS_SERVICE_ACCOUNT_KEY")

        try:
            if key_path and os.path.exists(key_path):
                client = storage.Client.from_service_account_json(key_path, project=project_id)
            else:
                client = storage.Client(project=project_id)

            bucket = client.bucket(bucket_name)
            client.get_bucket(bucket_name)
        except Exception as exc:  # pragma: no cover - relies on external service
            app.logger.warning("Google Cloud Storage is unavailable: %s", exc)
            _GCS_STATE = {"bucket": None, "checked": True, "error": exc}
            return None

        _GCS_STATE = {"bucket": bucket, "checked": True, "error": None}
        return bucket


def _gcs_build_remote_path(prefix, filename):
    safe_name = os.path.basename(filename or "")
    if not safe_name:
        return ""
    prefix = (prefix or "").strip("/")
    if prefix:
        return f"{prefix}/{safe_name}"
    return safe_name


def gcs_upload_file(local_path, filename, *, prefix=None, content_type=None):
    bucket = _ensure_gcs_bucket()
    if bucket is None:
        return None

    if not filename:
        return None

    remote_path = _gcs_build_remote_path(prefix, filename)
    if not remote_path:
        return None

    try:
        blob = bucket.blob(remote_path)
        blob.upload_from_filename(local_path, content_type=content_type)
        return True
    except Exception as exc:  # pragma: no cover - relies on external service
        app.logger.warning(
            "Failed to upload %s to Google Cloud Storage: %s",
            remote_path,
            exc,
        )
        return False


def gcs_delete_blob(filename, *, prefix=None):
    bucket = _ensure_gcs_bucket()
    if bucket is None:
        return None

    if not filename:
        return None

    remote_path = _gcs_build_remote_path(prefix, filename)
    if not remote_path:
        return None
    try:
        blob = bucket.blob(remote_path)
        blob.delete()
        return True
    except Exception as exc:  # pragma: no cover - relies on external service
        if NotFound is not None and isinstance(exc, NotFound):
            return True
        app.logger.warning(
            "Failed to delete %s from Google Cloud Storage: %s",
            remote_path,
            exc,
        )
        return False


def _compose_browser_url(base_url, prefix):
    if not base_url:
        return ""
    base = base_url.rstrip("/")
    if not prefix:
        return base
    segment = (prefix or "").strip("/")
    if not segment:
        return base
    return f"{base}/{segment}"


def _generate_cloud_manifest():
    targets = []

    sync_uploads = app.config.get("SYNC_UPLOADS_URL")
    sync_saves = app.config.get("SYNC_SAVES_URL")
    if sync_uploads or sync_saves:
        targets.append(
            dict(
                key="sync",
                label="Sync.com",
                uploads_url=sync_uploads or "",
                saves_url=sync_saves or "",
            )
        )

    if _gcs_configured():
        bucket_name = app.config.get("GCS_BUCKET_NAME")
        base_url = app.config.get("GCS_BROWSER_BASE_URL")
        if not base_url and bucket_name:
            base_url = f"https://console.cloud.google.com/storage/browser/{bucket_name}"
        targets.append(
            dict(
                key="gcs",
                label="Google Cloud Storage",
                uploads_url=_compose_browser_url(base_url, app.config.get("GCS_UPLOAD_PREFIX")),
                saves_url=_compose_browser_url(base_url, app.config.get("GCS_SAVES_PREFIX")),
                bucket=bucket_name or "",
                project=app.config.get("GCS_PROJECT_ID") or "",
                service_account=app.config.get("GCS_SERVICE_ACCOUNT_EMAIL") or "",
            )
        )

    return {
        "targets": targets,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gcs": {
            "enabled": _gcs_configured(),
            "bucket": app.config.get("GCS_BUCKET_NAME"),
            "project": app.config.get("GCS_PROJECT_ID"),
            "service_account": app.config.get("GCS_SERVICE_ACCOUNT_EMAIL"),
        },
    }


def load_cloud_manifest(*, force_refresh=False):
    now = time.time()
    with _CLOUD_MANIFEST_LOCK:
        cached = _CLOUD_MANIFEST_CACHE["value"]
        if (
            cached is not None
            and not force_refresh
            and now - _CLOUD_MANIFEST_CACHE["timestamp"] < 30
        ):
            return cached

    manifest = _generate_cloud_manifest()
    with _CLOUD_MANIFEST_LOCK:
        _CLOUD_MANIFEST_CACHE["timestamp"] = now
        _CLOUD_MANIFEST_CACHE["value"] = manifest
    return manifest


@app.after_request
def _no_cache_static_css(resp):
    if request.path.endswith("/static/style.css"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def allowed_file(fn): return "." in fn and fn.rsplit(".",1)[1].lower() in ALLOWED_EXTENSIONS

# === テンプレート共通変数 ===
@app.context_processor
def inject_cloud_links():
    manifest = load_cloud_manifest()
    return dict(
        sync_uploads_url=app.config.get("SYNC_UPLOADS_URL"),
        sync_saves_url=app.config.get("SYNC_SAVES_URL"),
        cloud_targets=manifest.get("targets", []),
        cloud_manifest=manifest,
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

@app.route("/uploads/<path:filename>")
def uploaded(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

# IDで解決する画像URL: /image/123456
@app.route("/image/<img_id>")
def image_by_id(img_id):
    db = _load_db()
    rec = db.get(img_id)
    if not rec: abort(404)
    path = os.path.join(app.config["UPLOAD_FOLDER"], rec["stored_name"])
    if not os.path.exists(path):
        abort(404)
    download_name = rec.get("original_name") or rec.get("stored_name")
    resp = send_file(path, as_attachment=False, download_name=download_name)
    resp.headers.setdefault("Cache-Control", "public, max-age=86400")
    return resp


# ギャラリー（一覧）
@app.route("/gallery")
def gallery():
    db = _load_db()
    # 新しい順に並べ替え（簡易）
    items = [{"id": k, **v} for k,v in db.items()]
    items.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return render_template(
        "gallery.html",
        items=items,
    )

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
    gallery_items = [{"id": k, **v} for k, v in db.items()]
    gallery_items.sort(key=lambda x: x.get("ts", 0), reverse=True)

    refresh = request.args.get("sync_refresh") == "1"
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
        sync_refreshing=refresh,
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
    "original_name": orig_name,                # 表示用（日本語そのまま）
    "original_name_safe": secure_filename(orig_name),  # 参考/予備
    "ts": int(time.time())
    }
    _save_db(db)

    flash(f"アップロード完了: ID {nid}")
    return redirect(url_for("index"))  # アップ後は一覧へ

@app.route("/preview", methods=["POST"])
def preview():
    session['last_text'] = request.form.get("text","")
    session['last_writing_mode'] = request.form.get("writing_mode","horizontal")
    text = session['last_text']; writing_mode = session['last_writing_mode']
    pages = parse_document(text)
    html = to_html_document(pages, writing_mode=writing_mode)
    return render_template("preview.html", html=html, writing_mode=writing_mode)  # :contentReference[oaicite:4]{index=4}

@app.route("/export", methods=["POST"])
def export():
    text = request.form.get("text","")
    writing_mode = request.form.get("writing_mode","horizontal")
    session['last_text'] = text; session['last_writing_mode'] = writing_mode
    pages = parse_document(text)
    html_doc = to_html_document(
        pages,
        writing_mode=writing_mode,
        include_boilerplate=True,
        inline_assets=True,
    )
    out_path = os.path.join(BASE_DIR, "export.html")
    with open(out_path, "w", encoding="utf-8") as f: f.write(html_doc)
    return send_file(out_path, as_attachment=True, download_name="export.html")

@app.route("/read")
def read_single():
    text = session.get("last_text", "")
    writing_mode = session.get("last_writing_mode", "horizontal")
    if not text:
        return redirect(url_for("index"))
    try:
        p = int(request.args.get("p", "1"))
    except Exception:
        p = 1
    pages = parse_document(text)
    total = len(pages)
    p = max(1, min(total, p))
    page = pages[p-1]
    nums = list(range(1, total+1))
    return render_template("read.html", page=page, total=total, p=p, nums=nums, writing_mode=writing_mode)

@app.route("/delete_image/<img_id>", methods=["POST"])
def delete_image(img_id):
    """ID で指定された画像を削除（DBと実ファイルの両方）"""
    db = _load_db()
    rec = db.get(img_id)
    if not rec:
        flash(f"ID {img_id} の画像が見つかりませんでした。")
        # どこから来たかに応じて戻る
        dest = request.args.get("next", "index")
        return redirect(url_for(dest) if dest in ("index", "gallery") else url_for("index"))

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

    # DB から削除して保存
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
    files = []
    try:
        for name in os.listdir(SAVES_DIR):
            if not name.lower().endswith(".txt"):
                continue
            p = os.path.join(SAVES_DIR, name)
            st = os.stat(p)
            files.append({
                "name": name,
                "size": st.st_size,
                "size_kb": round(st.st_size/1024, 1),
                "mtime": st.st_mtime,
                "mtime_str": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
        # 新しい順
        files.sort(key=lambda x: x["mtime"], reverse=True)
    except Exception as e:
        flash(f"保存一覧の取得に失敗しました: {e}")
        files = []
    return render_template(
        "saves.html",
        files=files,
    )


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

