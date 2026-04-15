"""Stage 2 — Clean & Validate: sanitise articles, detect paywalls, deduplicate."""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher

from config import PAYWALL_THRESHOLD, WORDS_PER_MINUTE
from models import Article

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------

def _clean_body(text: str) -> str:
    """Strip noise from extracted article body text."""
    lines = text.splitlines()
    cleaned: list[str] = []

    skip_patterns = [
        re.compile(r"^(share|tweet|email|print|subscribe|sign up|newsletter)", re.I),
        re.compile(r"^(advertisement|sponsored|related articles|read more|see also)", re.I),
        re.compile(r"^(cookie|privacy|terms of)", re.I),
        re.compile(r"^\s*\d+\s*(comments?|shares?|likes?|views?)\s*$", re.I),
    ]

    code_block_lines = 0

    for line in lines:
        stripped = line.strip()

        # Skip empty lines in sequence (keep at most one)
        if not stripped:
            if cleaned and cleaned[-1] == "":
                continue
            cleaned.append("")
            continue

        # Skip lines matching noise patterns
        if any(p.match(stripped) for p in skip_patterns):
            continue

        # Detect code blocks (indented or fenced) — truncate at 15 lines
        if stripped.startswith("```"):
            code_block_lines = 0 if code_block_lines > 0 else 1
            cleaned.append(stripped)
            continue

        if code_block_lines > 0:
            code_block_lines += 1
            if code_block_lines <= 15:
                cleaned.append(line)
            elif code_block_lines == 16:
                cleaned.append("    ...")
            continue

        cleaned.append(line)

    # Strip trailing blank lines
    while cleaned and cleaned[-1] == "":
        cleaned.pop()

    return "\n".join(cleaned)


# ---------------------------------------------------------------------------
# Paywall / extraction-quality detection
# ---------------------------------------------------------------------------

def _check_paywall(article: Article) -> None:
    """Check if article body is suspiciously short relative to read time."""
    if article.read_time_minutes <= 0:
        return

    expected_words = article.read_time_minutes * WORDS_PER_MINUTE
    actual_words = article.word_count

    if actual_words < expected_words * PAYWALL_THRESHOLD:
        article.is_paywalled = True
        if article.fetch_status == "ok":
            article.fetch_status = "paywalled"
        logger.info(
            "Paywall detected: '%s' — %d words vs %d expected (%d min read)",
            article.title, actual_words, expected_words, article.read_time_minutes,
        )


def _apply_paywall_fallback(article: Article) -> None:
    """Replace body with TLDR summary for paywalled/failed articles."""
    if article.fetch_status in ("paywalled", "failed", "timeout") or not article.body:
        if article.tldr_summary:
            article.body = (
                f"[Full article paywalled — summary from TLDR]\n\n"
                f"{article.tldr_summary}"
            )
            article.word_count = len(article.body.split())
        article.is_paywalled = True


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def _title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def deduplicate(articles: list[Article], threshold: float = 0.85) -> list[Article]:
    """Remove near-duplicate articles by title similarity. Keep the longer one."""
    keep: list[Article] = []
    for article in articles:
        is_dup = False
        for i, existing in enumerate(keep):
            if _title_similarity(article.title, existing.title) > threshold:
                # Keep the one with more content
                if article.word_count > existing.word_count:
                    keep[i] = article
                is_dup = True
                break
        if not is_dup:
            keep.append(article)
    return keep


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------

def sort_articles(articles: list[Article]) -> list[Article]:
    """Sort by category, then by read time descending (meatiest first)."""
    # Preserve category order as they appear
    category_order: dict[str, int] = {}
    for a in articles:
        if a.category not in category_order:
            category_order[a.category] = len(category_order)

    return sorted(
        articles,
        key=lambda a: (category_order.get(a.category, 999), -a.read_time_minutes),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def clean(articles: list[Article]) -> list[Article]:
    """Full Stage 2: clean, validate, deduplicate, and sort articles."""
    for article in articles:
        if article.body:
            article.body = _clean_body(article.body)
            article.word_count = len(article.body.split())

        _check_paywall(article)
        _apply_paywall_fallback(article)

    # Drop articles with no body at all
    articles = [a for a in articles if a.body]

    articles = deduplicate(articles)
    articles = sort_articles(articles)

    ok = sum(1 for a in articles if a.fetch_status == "ok")
    pw = sum(1 for a in articles if a.is_paywalled)
    logger.info("After cleaning: %d articles (%d ok, %d paywalled/fallback)", len(articles), ok, pw)

    return articles
