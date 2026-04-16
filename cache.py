"""Local file cache for articles and thumbnail images."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict
from pathlib import Path

from models import Article

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).parent / "cache"
ARTICLES_DIR = CACHE_DIR / "articles"
IMAGES_DIR = CACHE_DIR / "images"


def _url_hash(url: str) -> str:
    """Short deterministic hash of a URL for use as a filename."""
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def _ensure_dirs():
    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Article cache
# ---------------------------------------------------------------------------

def get_article(source_url: str) -> Article | None:
    """Load a cached article by source URL, or None if not cached."""
    path = ARTICLES_DIR / f"{_url_hash(source_url)}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Article(**data)
    except Exception as e:
        logger.debug("Cache read error for %s: %s", source_url, e)
        return None


def put_article(article: Article) -> None:
    """Save an article to the cache."""
    if not article.source_url:
        return
    _ensure_dirs()
    path = ARTICLES_DIR / f"{_url_hash(article.source_url)}.json"
    try:
        data = asdict(article)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.debug("Cache write error for %s: %s", article.source_url, e)


# ---------------------------------------------------------------------------
# Image cache
# ---------------------------------------------------------------------------

def get_image(url: str) -> bytes | None:
    """Load a cached image by URL, or None if not cached."""
    path = IMAGES_DIR / f"{_url_hash(url)}.jpg"
    if not path.exists():
        return None
    try:
        return path.read_bytes()
    except Exception:
        return None


def put_image(url: str, data: bytes) -> None:
    """Save image bytes to the cache."""
    if not url:
        return
    _ensure_dirs()
    path = IMAGES_DIR / f"{_url_hash(url)}.jpg"
    try:
        path.write_bytes(data)
    except Exception as e:
        logger.debug("Image cache write error for %s: %s", url, e)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def stats() -> dict:
    """Return cache statistics."""
    articles = list(ARTICLES_DIR.glob("*.json")) if ARTICLES_DIR.exists() else []
    images = list(IMAGES_DIR.glob("*.jpg")) if IMAGES_DIR.exists() else []
    return {
        "articles": len(articles),
        "images": len(images),
        "articles_size_mb": sum(f.stat().st_size for f in articles) / (1024 * 1024),
        "images_size_mb": sum(f.stat().st_size for f in images) / (1024 * 1024),
    }
