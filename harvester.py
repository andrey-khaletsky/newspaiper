"""Stage 1 — Harvest: fetch TLDR newsletters and source articles."""

from __future__ import annotations

import logging
import re
import time
from datetime import date
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests
import trafilatura
from bs4 import BeautifulSoup

from config import (
    DEFAULT_CATEGORIES,
    FETCH_DELAY,
    FETCH_TIMEOUT,
    TLDR_BASE_URL,
    USER_AGENT,
)
from models import Article

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": USER_AGENT}


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _strip_tracking_params(url: str) -> str:
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    for key in list(params):
        if key.startswith("utm_") or key in ("ref", "source"):
            del params[key]
    cleaned = urlencode(params, doseq=True)
    return urlunparse(parsed._replace(query=cleaned))


def _extract_domain(url: str) -> str:
    host = urlparse(url).netloc
    # strip www.
    if host.startswith("www."):
        host = host[4:]
    return host


# ---------------------------------------------------------------------------
# Parse a single TLDR newsletter page
# ---------------------------------------------------------------------------

def _parse_read_time(text: str) -> tuple[str, int]:
    """Extract read time string and integer minutes from article text.

    TLDR puts read time in the article title like '(5 minute read)'.
    """
    m = re.search(r"\((\d+)\s*min(?:ute)?\s*read\)", text, re.IGNORECASE)
    if m:
        minutes = int(m.group(1))
        return f"{minutes} min", minutes
    return "", 0


def _is_sponsor(title: str) -> bool:
    return "(Sponsor)" in title or "(sponsor)" in title


def parse_tldr_page(html: str, category: str) -> list[Article]:
    """Parse a TLDR newsletter page into Article objects."""
    soup = BeautifulSoup(html, "html.parser")
    articles: list[Article] = []
    current_section = category.upper()

    for section in soup.find_all("section"):
        header = section.find("header")
        if header:
            h3 = header.find("h3")
            if h3:
                section_text = h3.get_text(strip=True)
                if section_text:
                    current_section = section_text

        for article_el in section.find_all("article", class_="mt-3"):
            link = article_el.find("a", class_="font-bold")
            if not link:
                continue
            h3 = link.find("h3")
            if not h3:
                continue

            raw_title = h3.get_text(strip=True)
            if _is_sponsor(raw_title):
                continue

            href = link.get("href", "")
            if not href:
                continue

            # Clean the URL
            url = _strip_tracking_params(href)

            # Extract read time from the title text
            read_time_str, read_time_min = _parse_read_time(raw_title)

            # Remove the read-time suffix from the display title
            title = re.sub(r"\s*\(\d+\s*min(?:ute)?\s*read\)", "", raw_title).strip()

            # Get TLDR summary
            summary_div = article_el.find("div", class_="newsletter-html")
            summary = summary_div.get_text(strip=True) if summary_div else ""

            articles.append(Article(
                title=title,
                source_url=url,
                source_domain=_extract_domain(url),
                category=current_section,
                read_time=read_time_str,
                read_time_minutes=read_time_min,
                tldr_summary=summary,
                fetch_status="pending",
            ))

    return articles


# ---------------------------------------------------------------------------
# Fetch TLDR newsletter pages
# ---------------------------------------------------------------------------

def fetch_tldr_pages(
    target_date: date | None = None,
    categories: list[str] | None = None,
) -> list[Article]:
    """Fetch and parse TLDR newsletter pages for the given categories.

    If target_date is None, fetches the latest issue for each category
    via the /api/latest/{category} redirect.
    """
    if categories is None:
        categories = DEFAULT_CATEGORIES

    all_articles: list[Article] = []

    for cat in categories:
        if target_date is None:
            url = f"{TLDR_BASE_URL}/api/latest/{cat}"
            logger.info("Fetching latest TLDR %s: %s", cat, url)
        else:
            url = f"{TLDR_BASE_URL}/{cat}/{target_date.isoformat()}"
            logger.info("Fetching TLDR %s for %s: %s", cat, target_date, url)

        try:
            resp = requests.get(url, headers=HEADERS, timeout=FETCH_TIMEOUT,
                                allow_redirects=True)
            # TLDR redirects to homepage for missing dates
            final = resp.url.rstrip("/")
            if final in (TLDR_BASE_URL, f"{TLDR_BASE_URL}/"):
                logger.warning("No %s newsletter found (redirect to homepage)", cat)
                continue
            if final == f"{TLDR_BASE_URL}/{cat}":
                logger.warning("No %s newsletter found (redirect to category page)", cat)
                continue

            resp.raise_for_status()
            page_articles = parse_tldr_page(resp.text, cat)
            logger.info("  Found %d articles in %s (%s)", len(page_articles), cat, resp.url)
            all_articles.extend(page_articles)

        except requests.RequestException as e:
            logger.warning("Failed to fetch %s newsletter: %s", cat, e)

        time.sleep(FETCH_DELAY)

    return all_articles


# ---------------------------------------------------------------------------
# Fetch full article text from source URLs — fallback chain
# ---------------------------------------------------------------------------

MIN_BODY_LEN = 50
JINA_TIMEOUT = 20


def _try_jina(url: str) -> tuple[str, str]:
    """Fetch article via Jina Reader API. Returns (markdown_body, first_image_url).

    Jina renders JS, bypasses most paywalls, and returns clean markdown
    with inline images as ![alt](url).
    """
    try:
        resp = requests.get(
            f"https://r.jina.ai/{url}",
            headers={
                "Accept": "text/markdown",
                "X-No-Cache": "true",
            },
            timeout=JINA_TIMEOUT,
        )
        if resp.status_code != 200:
            return "", ""

        body = resp.text.strip()

        # Strip Jina metadata headers (Title:, URL Source:, Warning:, Markdown Content:)
        body = re.sub(r"^Title:.*$", "", body, flags=re.MULTILINE)
        body = re.sub(r"^URL Source:.*$", "", body, flags=re.MULTILINE)
        body = re.sub(r"^Warning:.*$", "", body, flags=re.MULTILINE)
        body = re.sub(r"^Markdown Content:\s*$", "", body, flags=re.MULTILINE)
        body = body.strip()

        if len(body) < MIN_BODY_LEN:
            return "", ""

        # Extract first image URL from markdown ![alt](url)
        first_image = ""
        img_match = re.search(r"!\[.*?\]\((https?://[^\s)]+)\)", body)
        if img_match:
            first_image = img_match.group(1)

        return body, first_image
    except Exception as e:
        logger.debug("Jina Reader failed for %s: %s", url, e)
        return "", ""


def _extract_og_image(html: str) -> str:
    """Extract og:image or twitter:image from HTML meta tags."""
    soup = BeautifulSoup(html, "html.parser")
    for prop in ("og:image", "twitter:image", "twitter:image:src"):
        tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
        if tag:
            url = tag.get("content", "").strip()
            if url and not url.endswith(".svg"):
                return url
    return ""


def _try_trafilatura(html: str, favor_precision: bool = True) -> str:
    """Extract article text with trafilatura."""
    body = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=False,
        include_images=False,
        include_links=False,
        favor_precision=favor_precision,
        favor_recall=not favor_precision,
        output_format="markdown",
    )
    return (body or "").strip()


def _try_readability(html: str) -> str:
    """Extract article text with readability-lxml as fallback."""
    try:
        from lxml.html import document_fromstring
        from readability import Document

        doc = Document(html)
        summary_html = doc.summary()
        tree = document_fromstring(summary_html)
        text = tree.text_content().strip()
        # Convert to simple paragraphs
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n\n".join(lines)
    except Exception:
        return ""


def _fetch_wayback(url: str) -> str | None:
    """Try to fetch the article from the Wayback Machine."""
    try:
        wb_url = f"https://web.archive.org/web/2/{url}"
        resp = requests.get(wb_url, headers=HEADERS, timeout=FETCH_TIMEOUT,
                            allow_redirects=True)
        if resp.status_code == 200 and len(resp.text) > 500:
            return resp.text
    except requests.RequestException:
        pass
    return None


def _fetch_google_cache(url: str) -> str | None:
    """Try to fetch the article from Google's web cache."""
    try:
        cache_url = f"https://webcache.googleusercontent.com/search?q=cache:{url}"
        resp = requests.get(cache_url, headers=HEADERS, timeout=FETCH_TIMEOUT,
                            allow_redirects=True)
        if resp.status_code == 200 and len(resp.text) > 500:
            return resp.text
    except requests.RequestException:
        pass
    return None


def _set_body(article: Article, body: str, image_url: str = "") -> Article:
    """Set article body and update word count / status."""
    article.body = body
    article.word_count = len(body.split())
    article.fetch_status = "ok"
    if image_url and not article.image_url:
        article.image_url = image_url
    return article


def fetch_source_article(article: Article) -> Article:
    """Fetch full text from the article's source URL with fallback chain.

    Chain: cache → Jina Reader → trafilatura → readability-lxml →
           Wayback Machine → Google Cache
    """
    from cache import get_article, put_article

    # Check cache first
    cached = get_article(article.source_url)
    if cached and cached.body:
        article.body = cached.body
        article.word_count = cached.word_count
        article.fetch_status = cached.fetch_status
        article.image_url = cached.image_url or article.image_url
        article.is_paywalled = cached.is_paywalled
        logger.info("    (cached)")
        return article

    # Step 1: Jina Reader API (handles JS, paywalls, returns markdown + images)
    body, jina_image = _try_jina(article.source_url)
    if body and len(body) >= MIN_BODY_LEN:
        logger.debug("    Jina Reader succeeded")
        return _set_body(article, body, jina_image)

    # Step 2: Download HTML and try local extractors
    html = None
    try:
        downloaded = trafilatura.fetch_url(article.source_url)
        if downloaded is not None:
            html = downloaded
    except Exception:
        pass

    if html is None:
        try:
            resp = requests.get(article.source_url, headers=HEADERS,
                                timeout=FETCH_TIMEOUT, allow_redirects=True)
            if resp.status_code == 200:
                html = resp.text
        except requests.RequestException:
            pass

    if html:
        # Extract og:image
        if not article.image_url:
            article.image_url = _extract_og_image(html)

        # Step 3: trafilatura — precision
        body = _try_trafilatura(html, favor_precision=True)
        if body and len(body) >= MIN_BODY_LEN:
            return _set_body(article, body)

        # Step 4: trafilatura — recall
        body = _try_trafilatura(html, favor_precision=False)
        if body and len(body) >= MIN_BODY_LEN:
            return _set_body(article, body)

        # Step 5: readability-lxml
        body = _try_readability(html)
        if body and len(body) >= MIN_BODY_LEN:
            return _set_body(article, body)

    # Step 6: Wayback Machine
    wb_html = _fetch_wayback(article.source_url)
    if wb_html:
        body = _try_trafilatura(wb_html) or _try_readability(wb_html)
        if body and len(body) >= MIN_BODY_LEN:
            if not article.image_url:
                article.image_url = _extract_og_image(wb_html)
            return _set_body(article, body)

    # Step 7: Google Cache
    gc_html = _fetch_google_cache(article.source_url)
    if gc_html:
        body = _try_trafilatura(gc_html) or _try_readability(gc_html)
        if body and len(body) >= MIN_BODY_LEN:
            if not article.image_url:
                article.image_url = _extract_og_image(gc_html)
            return _set_body(article, body)

    logger.warning("All extraction methods failed for %s", article.source_url)
    article.fetch_status = "failed"
    return article


def fetch_all_sources(articles: list[Article]) -> list[Article]:
    """Fetch full text for all articles, respecting rate limits."""
    from cache import get_article, put_article

    total = len(articles)
    cached_count = 0
    for i, article in enumerate(articles, 1):
        was_cached = bool(get_article(article.source_url) and get_article(article.source_url).body)
        logger.info("  [%d/%d] Fetching %s", i, total, article.source_domain)
        fetch_source_article(article)

        # Save to cache (even failed ones, to avoid re-trying)
        put_article(article)

        if was_cached:
            cached_count += 1
        else:
            time.sleep(FETCH_DELAY)

    if cached_count:
        logger.info("  %d/%d articles loaded from cache", cached_count, total)
    return articles


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def harvest(
    target_date: date | None = None,
    categories: list[str] | None = None,
) -> list[Article]:
    """Full Stage 1: fetch TLDR pages, then fetch source articles.

    If target_date is None, fetches the latest issue for each category.
    """
    articles = fetch_tldr_pages(target_date, categories)
    if not articles:
        return []

    logger.info("Fetching full text for %d articles...", len(articles))
    fetch_all_sources(articles)
    return articles
