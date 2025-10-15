print(">> parser loaded:", __file__)

import re, os, json
from html import escape
from urllib.parse import quote

# ---------- 正規表現 ----------
RE_NEWPAGE  = re.compile(r'\[newpage\]')
RE_UPLOADED = re.compile(r'^\[uploadedimage:(.*?)\]$')
RE_PIXIV    = re.compile(r'^\[pixivimage:(\d+)\]$')
RE_JUMP_BLK = re.compile(r'^\[jump:(\d+)\]$')
RE_JUMP_INL = re.compile(r'\[jump:(\d+)\]')
RE_JUMPURI  = re.compile(r'\[\[jumpuri:(.*?)\s*(?:>|&gt;)\s*(.*?)\]\]')
RE_RUBY     = re.compile(r'\[\[rb:(.*?)\s*(?:>|&gt;)\s*(.*?)\]\]')

BASE_DIR   = os.path.dirname(__file__)
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
DB_PATH    = os.path.join(UPLOAD_DIR, "uploads.json")

# ---------- 前処理 ----------
def _preprocess(text: str) -> str:
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    import re
    text = re.sub(r'(?m)(?<!\n)\n(\s*\[chapter:[^\]]+\])', r'\n\n\1', text)
    text = re.sub(r'(\[chapter:[^\]]+\])(\S)', r'\1\n\n\2', text)
    text = re.sub(r'(\[chapter:[^\]]+\])\n(\S)', r'\1\n\n\2', text)
    return text

def split_pages(text: str):
    parts = RE_NEWPAGE.split(text)
    return [p.strip() for p in parts]

# ---------- アップロード画像解決 ----------
def _load_upload_db():
    try:
        with open(DB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _resolve_uploaded_src(token: str) -> tuple[str, str]:
    token = token.strip()
    db = _load_upload_db()
    if token.isdigit() and 4 <= len(token) <= 8:
        rec = db.get(token)
        if rec:
            stored = rec.get("stored_name", "")
            path = os.path.join(UPLOAD_DIR, stored)
            if stored and os.path.exists(path):
                return f"/uploads/{stored}", token
        return f"/image/{token}", token
    return f"/uploads/{quote(token)}", token

# ---------- インライン ----------
def render_inline(text: str) -> str:
    def rb_sub(m): return f'<ruby>{m.group(1)}<rt>{m.group(2)}</rt></ruby>'
    text = RE_RUBY.sub(rb_sub, text)

    def jumpuri_sub(m): return f'<a href="{m.group(2)}" target="_blank" rel="noopener noreferrer">{m.group(1)}</a>'
    text = RE_JUMPURI.sub(jumpuri_sub, text)
    text = RE_JUMP_INL.sub(lambda m: f'<a class="jump" href="#{m.group(1)}">{m.group(1)}ページへ</a>', text)
    return text

# ---------- ブロック ----------
def render_block(block: str, page_index: int) -> str:
    s = block.rstrip("\n")

    # --- 章見出し ---
    if s.startswith("[chapter:"):
        end = s.find("]")
        if end != -1:
            raw_title = s[len("[chapter:"):end]
            rest = s[end+1:].lstrip()
            html = f'<h2 class="chapter">{escape(raw_title)}</h2>'
            if rest:
                html += f"<p>{render_inline(escape(rest).replace('\\n','<br>'))}</p>"
            return html

    # --- 行単位で混在処理（テキストと [uploadedimage:*] が同ブロックにあってもOK） ---
    lines = s.split("\n")
    out_parts = []
    buf = []  # テキスト行バッファ

    def flush_buf():
        if buf:
            # バッファを段落として出力
            esc = escape("\n".join(buf)).replace("\n", "<br>")
            out_parts.append(f"<p>{render_inline(esc)}</p>")
            buf.clear()

    for line in lines:
        m = RE_UPLOADED.match(line.strip())
        if m:
            flush_buf()
            token = m.group(1)
            src, alt = _resolve_uploaded_src(token)
            if src.startswith("/image/") and alt == token:
                out_parts.append(
                    f'<figure class="illustration missing"><div class="img-missing">画像が見つかりません: {alt}</div></figure>'
                )
            else:
                # ※ figcaption を付けない（= 謎の数字を出さない）
                out_parts.append(f'<figure class="illustration"><img src="{src}" alt="{alt}"></figure>')
        else:
            if line == "":            # ← 空行に遭遇
                flush_buf()
                out_parts.append('<div class="blankline" aria-hidden="true"></div>')
            else:
                buf.append(line)
    flush_buf()

    if out_parts:
        return "".join(out_parts)

    # --- pixiv（ブロックが丸ごと一致の時だけ） ---
    m = RE_PIXIV.match(s)
    if m:
        pid = m.group(1)
        link = f'https://www.pixiv.net/artworks/{pid}'
        return (f'<figure class="pixiv-illustration"><a href="{link}" target="_blank" rel="noopener noreferrer">'
                f'pixiv作品 {pid} を開く</a><figcaption>pixiv作品ID: {pid}</figcaption></figure>')

    # --- jump ブロック ---
    m = RE_JUMP_BLK.match(s)
    if m:
        target = int(m.group(1))
        return f'<a class="jump" href="#{target}">{target}ページへ</a>'

    # --- 通常テキスト ---
    esc = escape(block).replace('\n', '<br>')
    return f'<p>{render_inline(esc)}</p>'



# ---------- 文書 ----------
def parse_document(text: str):
    text = _preprocess(text)
    pages_raw = split_pages(text)
    pages = []
    for i, raw in enumerate(pages_raw, start=1):
        blocks = [b for b in re.split(r'(?=^\s*\[chapter:[^\]]+\])', raw, flags=re.M) if b != ""]
        html_blocks = [render_block(b, i) for b in blocks]
        pages.append({"index": i, "html": "\n".join(html_blocks)})
    return pages

# ---------- HTML出力 ----------
def to_html_document(pages, writing_mode: str = "horizontal", include_boilerplate: bool = False) -> str:
    body = []
    total = len(pages)
    for p in pages:
        idx = p["index"]
        body.append(f'<section class="page" id="page-{idx}" data-index="{idx}">')
        body.append(f'<span id="{idx}" class="page-anchor" aria-hidden="true"></span>')
        body.append(f'<div class="page-inner">{p["html"]}</div>')
        body.append('</section>')

    pager = ['<div class="bottom-pager" role="navigation" aria-label="ページ移動"><div class="pager-center">']
    pager.append('<a class="page-arrow prev" href="#1">&lsaquo;</a>')
    for i in range(1, total + 1):
        pager.append(f'<a class="page-number" href="#{i}" data-page="{i}">{i}</a>')
    pager.append(f'<a class="page-arrow next" href="#{total}">&rsaquo;</a>')
    pager.append('</div></div>')

    content = "\n".join(body) + "\n" + "\n".join(pager)
    wrapper = f'<div class="document {"vertical" if writing_mode=="vertical" else "horizontal"}">{content}</div>'

    if include_boilerplate:
        return f'''<!doctype html>
<html lang="ja">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>PixiText Export</title><link rel="stylesheet" href="static/style.css"></head>
<body>{wrapper}<script src="static/app.js"></script></body></html>'''

    return wrapper
