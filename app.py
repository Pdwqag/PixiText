import os
import json
import random
import re
import time
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
    SYNC_UPLOADS_URL=os.getenv("SYNC_UPLOADS_URL", "https://cp.sync.com/files"),
    SYNC_SAVES_URL=os.getenv("SYNC_SAVES_URL", "https://cp.sync.com/files"),
)

# 3) Flask-Session を初期化（requirements.txt に Flask-Session を入れること）
Session(app)


@app.after_request
def _no_cache_static_css(resp):
    if request.path.endswith("/static/style.css"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


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

    resp = make_response(render_template(
        "index.html",
        default_text=default_text,
        writing_mode=writing_mode,
        gallery_items=gallery_items,
        last_filename=last_filename,
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

