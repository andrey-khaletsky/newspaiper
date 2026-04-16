#!/usr/bin/env python3
"""newspaiper — AI & Tech newspaper generator.

Fetches full-text articles from TLDR.tech, lays them out in a
multi-column A4 portrait PDF ready for booklet printing.

Usage:
    python newspaiper.py                        # today's edition
    python newspaiper.py --date 2026-04-13      # specific date
    python newspaiper.py --select               # choose articles interactively
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

from cleaner import clean
from config import DEFAULT_CATEGORIES, DEFAULT_FONT_SIZE, DEFAULT_PAGES
from harvester import harvest
from models import Article
from renderer import render

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _parse_date(s: str) -> date:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid date: {s}. Use YYYY-MM-DD.")


# ---------------------------------------------------------------------------
# Article selection file
# ---------------------------------------------------------------------------

def _write_selection_file(articles: list[Article], target_date: date, path: Path) -> None:
    """Write a human-editable selection file, sorted by word count descending."""
    lines: list[str] = []
    lines.append(f"# NEWSPAIPER — Article Selection for {target_date.isoformat()}")
    lines.append("#")
    lines.append("# + = full text,  $ = summary only,  - = exclude")
    lines.append("# Sorted longest-first. Flip the marker or delete the line.")
    lines.append("# Save and close to continue.")
    lines.append("#")

    # Sort indices by word count descending (longest first)
    ranked = sorted(range(len(articles)), key=lambda i: articles[i].word_count, reverse=True)

    for i in ranked:
        a = articles[i]

        marker = "$" if a.is_paywalled else "+"
        cat = a.category[:12].ljust(12)
        domain = a.source_domain[:20].ljust(20)
        title = a.title[:58]

        if a.is_paywalled:
            info = "PAYWALLED"
        elif a.word_count > 0:
            info = f"{a.word_count:>5} words"
        else:
            info = "summary only"

        # Format: + 01. [CATEGORY]  domain | Title              | words
        lines.append(f"{marker} {i:02d}. [{cat}] {domain} | {title:<58} | {info}")

    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _parse_selection_file(path: Path, articles: list[Article]) -> list[Article]:
    """Parse the selection file and return only included articles.

    Markers: + = full text, $ = summary only, - = exclude.
    """
    text = path.read_text(encoding="utf-8")
    include_full: set[int] = set()
    include_summary: set[int] = set()

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # Match: + 01. or $ 01. or - 01.
        m = re.match(r"^([+$\-])\s*(\d+)\.", line)
        if m:
            marker, idx_str = m.group(1), m.group(2)
            idx = int(idx_str)
            if 0 <= idx < len(articles):
                if marker == "+":
                    include_full.add(idx)
                elif marker == "$":
                    include_summary.add(idx)

    selected: list[Article] = []
    for i, a in enumerate(articles):
        if i in include_full:
            selected.append(a)
        elif i in include_summary:
            # Force summary-only: replace body with TLDR summary
            a.body = a.tldr_summary or a.body
            a.word_count = len(a.body.split())
            a.is_paywalled = True
            selected.append(a)

    return selected


def _open_in_editor(path: Path) -> None:
    """Open a file in the user's preferred editor and wait for close."""
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")

    if not editor:
        # Try common editors
        for candidate in ["code --wait", "nano", "vim", "vi"]:
            binary = candidate.split()[0]
            if subprocess.run(["which", binary], capture_output=True).returncode == 0:
                editor = candidate
                break

    if not editor:
        editor = "vi"

    cmd = editor.split() + [str(path)]
    logger.info("Opening selection file in: %s", editor)
    subprocess.run(cmd)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="newspaiper",
        description="AI & Tech newspaper generator from TLDR.tech",
    )
    parser.add_argument(
        "--date", type=_parse_date, default=None,
        help="Newsletter date (YYYY-MM-DD, default: today)",
    )
    parser.add_argument(
        "--categories", type=str, default=None,
        help=f"Comma-separated categories (default: {','.join(DEFAULT_CATEGORIES)})",
    )
    parser.add_argument(
        "--pages", type=int, default=0,
        help="Max page count (0 = use all content, default: 0)",
    )
    parser.add_argument(
        "--booklet", action="store_true", default=True,
        help="Ensure page count is divisible by 4 (default: true)",
    )
    parser.add_argument(
        "--no-booklet", dest="booklet", action="store_false",
        help="Don't round page count to multiple of 4",
    )
    parser.add_argument(
        "--select", action="store_true",
        help="Open an editor to choose which articles to include",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Output PDF path (default: output/newspaiper_DATE.pdf)",
    )
    parser.add_argument(
        "--font-size", type=float, default=DEFAULT_FONT_SIZE,
        help=f"Base body font size in pt (default: {DEFAULT_FONT_SIZE})",
    )
    parser.add_argument(
        "--cols", type=int, default=3,
        help="Number of columns (default: 3)",
    )

    args = parser.parse_args()

    target_date = args.date  # None means "latest"
    categories = args.categories.split(",") if args.categories else None
    date_label = target_date.isoformat() if target_date else date.today().isoformat()
    output_path = args.output or f"output/newspaiper_{date_label}.pdf"

    logger.info("=== NEWSPAIPER ===")
    logger.info("Date: %s", target_date or "latest")
    logger.info("Categories: %s", categories or DEFAULT_CATEGORIES)
    logger.info("Target pages: %s, booklet: %s", args.pages or "auto", args.booklet)

    # Stage 1 — Harvest
    logger.info("--- Stage 1: Harvesting articles ---")
    articles = harvest(target_date, categories)
    if not articles:
        logger.error("No articles found. Is it a weekend or future date?")
        sys.exit(1)
    logger.info("Harvested %d articles", len(articles))

    if args.select:
        from selector import visual_select

        # Show selection UI — user picks which articles to include
        articles = visual_select(articles)
        if not articles:
            logger.error("No articles selected. Exiting.")
            sys.exit(1)
        logger.info("Selected %d articles", len(articles))

    # Stage 2 — Clean & Validate
    logger.info("--- Stage 2: Cleaning & validating ---")
    articles = clean(articles)
    logger.info("After cleaning: %d articles ready for layout", len(articles))

    if not articles:
        logger.error("No articles survived cleaning. Cannot generate PDF.")
        sys.exit(1)

    # Stage 3 — Layout & Render
    logger.info("--- Stage 3: Rendering PDF ---")
    pdf_path = render(
        articles,
        target_date=date_label,
        output_path=output_path,
        target_pages=args.pages,
        booklet=args.booklet,
        font_size=args.font_size,
    )
    logger.info("=== Done: %s ===", pdf_path)


if __name__ == "__main__":
    main()
