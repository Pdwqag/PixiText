"""Helpers for fetching Pixiv illustration data for PixiText."""

from __future__ import annotations

import os
import time
import threading
from typing import Dict, List, Tuple

import mimetypes

import requests

__all__ = [
    "PixivFetchError",
    "fetch_pixiv_metadata",
    "fetch_pixiv_pages",
    "fetch_pixiv_image",
]


class PixivFetchError(RuntimeError):
    """Raised when the Pixiv API returns an error response."""


_API_ROOT = "https://www.pixiv.net/ajax"
_REFERER = "https://www.pixiv.net/"
_USER_AGENT = os.getenv(
    "PIXIV_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36",
)
_CACHE_TTL = int(os.getenv("PIXIV_CACHE_TTL", "900"))

_session = requests.Session()
_session.headers.update(
    {
        "User-Agent": _USER_AGENT,
        "Referer": _REFERER,
        "Accept": "application/json",
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    }
)

_cache_lock = threading.Lock()
_metadata_cache: Dict[str, Tuple[float, dict]] = {}
_pages_cache: Dict[str, Tuple[float, List[dict]]] = {}
_image_cache: Dict[Tuple[str, int], Tuple[float, bytes, str]] = {}


def _within_ttl(entry: Tuple[float, object]) -> bool:
    return bool(entry) and (time.time() - entry[0] < _CACHE_TTL)


def _get_json(url: str):
    try:
        resp = _session.get(url, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise PixivFetchError("Pixiv APIへの接続に失敗しました") from exc
    try:
        data = resp.json()
    except ValueError as exc:
        raise PixivFetchError("Pixiv APIの応答を解析できませんでした") from exc
    if data.get("error"):
        raise PixivFetchError(data.get("message", "Pixiv API error"))
    return data.get("body")


def fetch_pixiv_metadata(pid: str) -> dict:
    with _cache_lock:
        cached = _metadata_cache.get(pid)
        if cached and _within_ttl(cached):
            return cached[1]
    url = f"{_API_ROOT}/illust/{pid}"
    body = _get_json(url)
    with _cache_lock:
        _metadata_cache[pid] = (time.time(), body)
    return body


def fetch_pixiv_pages(pid: str) -> List[dict]:
    with _cache_lock:
        cached = _pages_cache.get(pid)
        if cached and _within_ttl(cached):
            return cached[1]
    url = f"{_API_ROOT}/illust/{pid}/pages"
    body = _get_json(url)
    if not isinstance(body, list):
        raise PixivFetchError("unexpected Pixiv API payload")
    with _cache_lock:
        _pages_cache[pid] = (time.time(), body)
    return body


def fetch_pixiv_image(pid: str, page: int = 0) -> Tuple[bytes, str]:
    key = (pid, page)
    with _cache_lock:
        cached = _image_cache.get(key)
        if cached and _within_ttl(cached):
            return cached[1], cached[2]
    pages = fetch_pixiv_pages(pid)
    if page < 0 or page >= len(pages):
        raise PixivFetchError("指定されたページが存在しません")
    urls = pages[page].get("urls") or {}
    image_url = urls.get("original") or urls.get("regular")
    if not image_url:
        raise PixivFetchError("画像URLを取得できませんでした")

    try:
        resp = _session.get(image_url, timeout=20, headers={"Referer": _REFERER})
    except requests.RequestException as exc:
        raise PixivFetchError("Pixiv画像の取得に失敗しました") from exc
    if resp.status_code == 404:
        raise PixivFetchError("画像が削除されています")
    try:
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise PixivFetchError("Pixiv画像の取得に失敗しました") from exc
    content_type = resp.headers.get("Content-Type")
    if not content_type:
        content_type, _ = mimetypes.guess_type(image_url)
        content_type = content_type or "image/jpeg"
    data = resp.content
    with _cache_lock:
        _image_cache[key] = (time.time(), data, content_type)
    return data, content_type
