print(">> app loaded:", __file__)

import os, json, random, time, re, threading
from flask import Flask, render_template, request, redirect, url_for, send_file, send_from_directory, flash, session, abort, jsonify, make_response
from werkzeug.utils import secure_filename
from parser import parse_document, to_html_document
from datetime import datetime, timezone
import urllib.request
import urllib.error
from flask_session import Session

BASE_DIR      = os.path.dirname(__file__)
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR    = os.path.join(BASE_DIR, "static")
UPLOAD_DIR    = os.path.join(BASE_DIR, "uploads")
SAVES_DIR     = os.path.join(BASE_DIR, "saves")
SESSION_DIR   = os.path.join(BASE_DIR, "flask_session")
DB_PATH       = os.path.join(UPLOAD_DIR, "uploads.json")

# 必要なディレクトリは必ず作成
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(SAVES_DIR,  exist_ok=True)
os.makedirs(SESSION_DIR, exist_ok=True)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "svg"}

# 1) まずアプリ生成
app = Flask(
    __name__,
    static_url_path="/static",
    static_folder=STATIC_DIR,
    template_folder=TEMPLATES_DIR
)

# 2) 設定をまとめて投入（重複を避ける）
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY", "change-me"),  # Renderなら環境変数で上書き
    UPLOAD_FOLDER=UPLOAD_DIR,
    SESSION_TYPE="filesystem",
    SESSION_FILE_DIR=SESSION_DIR,
    SESSION_PERMANENT=False,
    BUILD_VER=24,   # キャッシュバスター
    SYNC_UPLOADS_URL=os.getenv("SYNC_UPLOADS_URL", "https://cp.sync.com/files"),
    SYNC_SAVES_URL=os.getenv("SYNC_SAVES_URL", "https://cp.sync.com/files"),
    SYNC_MANIFEST_URL=os.getenv("SYNC_MANIFEST_URL", ""),
    SYNC_MANIFEST_TTL=int(os.getenv("SYNC_MANIFEST_TTL", "180")),
    SYNC_MANIFEST_TIMEOUT=float(os.getenv("SYNC_MANIFEST_TIMEOUT", "6")),
)

# 3) Flask-Session を初期化（requirements.txt に Flask-Session を入れること）
Session(app)

@app.after_request
def _no_cache_static_css(resp):
    try:
        from flask import request
        if request.path.endswith('/static/style.css'):
            resp.headers['Cache-Control'] = 'no-store'
    except Exception:
        pass
    return resp

sess_dir = os.path.join(BASE_DIR, "flask_session")
os.makedirs(sess_dir, exist_ok=True) 
app.config['SESSION_FILE_DIR'] = sess_dir

Session(app)


def allowed_file(fn): return "." in fn and fn.rsplit(".",1)[1].lower() in ALLOWED_EXTENSIONS

# === テンプレート共通変数 ===
@app.context_processor
def inject_cloud_links():
    providers = []

    def _provider(key, label, uploads_url, saves_url):
        if not uploads_url and not saves_url:
            return None
        return dict(
            key=key,
            label=label,
            uploads_url=uploads_url or "",
            saves_url=saves_url or "",
        )

    sync = _provider(
        "sync",
        "Sync.com",
        app.config.get("SYNC_UPLOADS_URL"),
        app.config.get("SYNC_SAVES_URL"),
    )
    for entry in (sync,):
        if entry:
            providers.append(entry)

    return dict(
        sync_uploads_url=app.config.get("SYNC_UPLOADS_URL"),
        sync_saves_url=app.config.get("SYNC_SAVES_URL"),
        cloud_targets=providers,
    )


_sync_cache = {"ts": 0.0, "data": {"uploads": [], "saves": []}}
_sync_cache_lock = threading.Lock()


def _human_size(num):
    if num is None:
        return None
    try:
        num = float(num)
    except Exception:
        return None
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    while num >= 1024 and idx < len(units) - 1:
        num /= 1024
        idx += 1
    return f"{num:.1f}{units[idx]}"


def _format_sync_timestamp(value):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return str(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            iso = text.replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo:
                dt = dt.astimezone(timezone.utc).astimezone()
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return text
    return None


def _normalize_sync_items(items, fallback_category):
    normalized = []
    if not items:
        return normalized

    iterable = []
    if isinstance(items, dict):
        for key, value in items.items():
            if isinstance(value, dict):
                entry = value.copy()
                entry.setdefault("name", key)
            else:
                entry = {"name": key, "url": value}
            iterable.append(entry)
    elif isinstance(items, list):
        for value in items:
            if isinstance(value, dict):
                iterable.append(value)
            else:
                iterable.append({"name": value})
    else:
        return normalized

    for entry in iterable:
        name = (
            entry.get("name")
            or entry.get("title")
            or entry.get("filename")
            or entry.get("id")
            or "item"
        )
        url = entry.get("url") or entry.get("link") or entry.get("href")
        size = entry.get("size") or entry.get("bytes") or entry.get("length")
        updated = (
            entry.get("updated")
            or entry.get("modified")
            or entry.get("mtime")
            or entry.get("timestamp")
        )
        try:
            size_int = int(size)
        except Exception:
            try:
                size_int = int(float(size)) if size is not None else None
            except Exception:
                size_int = None
        normalized.append(
            dict(
                name=str(name),
                url=url,
                size=size_int,
                size_label=_human_size(size_int) if size_int is not None else (str(size) if size else None),
                updated=_format_sync_timestamp(updated),
                category=fallback_category,
            )
        )
    return normalized


def _normalize_sync_payload(payload):
    uploads = []
    saves = []
    if isinstance(payload, dict):
        uploads = _normalize_sync_items(
            payload.get("uploads")
            or payload.get("images")
            or payload.get("gallery")
            or payload.get("illustrations"),
            "uploads",
        )
        saves = _normalize_sync_items(
            payload.get("saves")
            or payload.get("texts")
            or payload.get("documents")
            or payload.get("stories"),
            "saves",
        )
    elif isinstance(payload, list):
        uploads = _normalize_sync_items(payload, "uploads")
    return {"uploads": uploads, "saves": saves}


def get_sync_manifest(force_refresh=False):
    url = app.config.get("SYNC_MANIFEST_URL")
    if not url:
        return {"uploads": [], "saves": []}

    ttl = max(0, int(app.config.get("SYNC_MANIFEST_TTL", 180)))
    timeout = max(1.0, float(app.config.get("SYNC_MANIFEST_TIMEOUT", 6)))
    now = time.time()

    with _sync_cache_lock:
        if (
            not force_refresh
            and _sync_cache["ts"] > 0
            and now - _sync_cache["ts"] < ttl
        ):
            return _sync_cache["data"]

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PixiTextSync/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        if not raw:
            payload = {}
        else:
            try:
                payload = json.loads(raw.decode("utf-8"))
            except UnicodeDecodeError:
                payload = json.loads(raw.decode("utf-8", errors="ignore"))
        data = _normalize_sync_payload(payload)
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, ValueError) as exc:
        app.logger.warning("Sync manifest fetch failed: %s", exc)
        data = {"uploads": [], "saves": []}

    with _sync_cache_lock:
        _sync_cache["ts"] = now
        _sync_cache["data"] = data
    return data


def fetch_sync_manifest(force_refresh=False):
    """Wrapper that tolerates deployments missing the helper."""
    fn = globals().get("get_sync_manifest")
    if callable(fn):
        return fn(force_refresh=force_refresh)
    app.logger.warning("get_sync_manifest missing; returning empty sync manifest")
    return {"uploads": [], "saves": []}


def _stat_path(path):
    if not path:
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    return {"size": st.st_size, "mtime": st.st_mtime}


def _iso_timestamp(ts):
    if not ts:
        return None
    try:
        dt = datetime.fromtimestamp(float(ts), timezone.utc).astimezone()
        return dt.replace(microsecond=0).isoformat()
    except Exception:
        return None


def build_local_sync_manifest():
    uploads = []
    db = _load_db()
    for img_id, rec in sorted(db.items(), key=lambda x: x[1].get("ts", 0), reverse=True):
        stored = rec.get("stored_name")
        path = os.path.join(app.config["UPLOAD_FOLDER"], stored) if stored else None
        meta = _stat_path(path)
        uploads.append(
            {
                "id": img_id,
                "name": rec.get("original_name") or stored or img_id,
                "stored_name": stored,
                "url": f"/image/{img_id}",
                "size": meta["size"] if meta else None,
                "size_label": _human_size(meta["size"]) if meta else None,
                "updated": _iso_timestamp(meta["mtime"]) if meta else None,
                "shortcode": f"[uploadedimage:{img_id}]",
            }
        )

    saves = []
    if os.path.isdir(SAVES_DIR):
        for name in sorted(os.listdir(SAVES_DIR)):
            path = os.path.join(SAVES_DIR, name)
            if not os.path.isfile(path):
                continue
            meta = _stat_path(path)
            saves.append(
                {
                    "name": name,
                    "url": f"/saves/download/{name}",
                    "size": meta["size"] if meta else None,
                    "size_label": _human_size(meta["size"]) if meta else None,
                    "updated": _iso_timestamp(meta["mtime"]) if meta else None,
                }
            )

    return {"uploads": uploads, "saves": saves}


@app.route("/sync/manifest.json")
def sync_manifest_export():
    data = build_local_sync_manifest()
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    resp = make_response(payload)
    resp.headers["Content-Type"] = "application/json; charset=utf-8"
    resp.headers["Content-Disposition"] = f"attachment; filename=sync-manifest-{ts}.json"
    resp.headers["Cache-Control"] = "no-store"
    return resp

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
    refresh = request.args.get("sync_refresh") == "1"
    sync_manifest = fetch_sync_manifest(force_refresh=refresh)
    return render_template(
        "gallery.html",
        items=items,
        sync_uploads=sync_manifest.get("uploads", []),
        sync_refreshing=refresh,
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
    cloud_manifest = fetch_sync_manifest(force_refresh=refresh)

    resp = make_response(render_template(
        "index.html",
        default_text=default_text,
        writing_mode=writing_mode,
        gallery_items=gallery_items,
        last_filename=last_filename,
        cloud_manifest=cloud_manifest,
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

    # DB から削除して保存
    db.pop(img_id, None)
    _save_db(db)

    flash(f"ID {img_id} を削除しました。")
    dest = request.args.get("next", "index")
    return redirect(url_for(dest) if dest in ("index", "gallery") else url_for("index"))

# 末尾の他ルートと同じ場所に追記
from io import BytesIO

def _safe_txt_name(name: str) -> str:
    import re, os
    name = (name or "").strip()
    name = re.sub(r'[\\/:*?"<>|]+', "_", name).replace("\0","")
    if not name.lower().endswith(".txt"):
        name += ".txt"
    return os.path.basename(name)


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
        return jsonify(success=True, message=f"「{name}」を保存しました", filename=name)
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
    refresh = request.args.get("sync_refresh") == "1"
    sync_manifest = fetch_sync_manifest(force_refresh=refresh)
    return render_template(
        "saves.html",
        files=files,
        sync_saves=sync_manifest.get("saves", []),
        sync_refreshing=refresh,
    )


@app.route("/saves/download/<path:fname>")
def saves_download(fname):
    fname = os.path.basename(fname)
    if not fname.lower().endswith(".txt"):
        abort(404)
    path = os.path.join(SAVES_DIR, fname)
    if not os.path.exists(path) or not os.path.isfile(path):
        abort(404)
    return send_from_directory(SAVES_DIR, fname, as_attachment=True, download_name=fname)


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

