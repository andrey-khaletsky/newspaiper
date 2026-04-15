"""Stage 3 — Layout & Render: multi-column A4 newspaper PDF via reportlab."""

from __future__ import annotations

import io
import logging
import math
import re
from pathlib import Path

import requests
from reportlab.lib.colors import Color, black, gray, white
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    FrameBreak,
    Image,
    KeepTogether,
    NextPageTemplate,
    PageTemplate,
    Paragraph,
    Spacer,
)
from reportlab.platypus.flowables import HRFlowable

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from config import (
    COL_GAP,
    COL_WIDTH,
    FONTS,
    FOOTER_HEIGHT,
    HEADER_HEIGHT,
    MARGIN_BOTTOM,
    MARGIN_H,
    MARGIN_TOP,
    MASTHEAD_HEIGHT,
    NUM_COLS,
    PAGE_HEIGHT,
    PAGE_WIDTH,
    PALATINO_PATHS,
)
from models import Article

logger = logging.getLogger(__name__)

# Register Palatino Linotype font family
pdfmetrics.registerFont(TTFont("Palatino", PALATINO_PATHS["regular"]))
pdfmetrics.registerFont(TTFont("Palatino-Bold", PALATINO_PATHS["bold"]))
pdfmetrics.registerFont(TTFont("Palatino-Italic", PALATINO_PATHS["italic"]))
pdfmetrics.registerFont(TTFont("Palatino-BoldItalic", PALATINO_PATHS["bold_italic"]))
pdfmetrics.registerFontFamily(
    "Palatino",
    normal="Palatino",
    bold="Palatino-Bold",
    italic="Palatino-Italic",
    boldItalic="Palatino-BoldItalic",
)

DARK_GRAY = Color(0.3, 0.3, 0.3)
RULE_COLOR = Color(0.6, 0.6, 0.6)
SECTION_BG = Color(0.1, 0.1, 0.1)

# Max image width = column width, max height ~40% of column height
IMG_MAX_WIDTH = COL_WIDTH
IMG_MAX_HEIGHT = 60 * mm

# Cache downloaded images in memory for the duration of the render
_image_cache: dict[str, Image | None] = {}


def _fetch_image(url: str) -> Image | None:
    """Download an image, compress it, and return a reportlab Image flowable."""
    if url in _image_cache:
        return _image_cache[url]

    # Skip relative URLs and tiny icons
    if not url.startswith("http"):
        _image_cache[url] = None
        return None

    try:
        from PIL import Image as PILImage

        resp = requests.get(url, timeout=10, headers={"User-Agent": "newspaiper/1.0"})
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if not any(t in content_type for t in ("image/", "octet-stream")):
            _image_cache[url] = None
            return None

        pil_img = PILImage.open(io.BytesIO(resp.content))
        pil_img = pil_img.convert("RGB")

        # Downscale to max 400px wide (plenty for a 60mm column)
        max_px = 400
        if pil_img.width > max_px:
            ratio = max_px / pil_img.width
            pil_img = pil_img.resize(
                (max_px, int(pil_img.height * ratio)),
                PILImage.LANCZOS,
            )

        # Save as compressed JPEG in memory
        buf = io.BytesIO()
        pil_img.save(buf, format="JPEG", quality=60, optimize=True)
        buf.seek(0)

        reader = ImageReader(buf)
        iw, ih = reader.getSize()

        # Scale to fit column width / max height
        scale = min(IMG_MAX_WIDTH / iw, IMG_MAX_HEIGHT / ih, 1.0)
        w = iw * scale
        h = ih * scale

        img = Image(buf, width=w, height=h)
        _image_cache[url] = img
        return img
    except Exception as e:
        logger.debug("Failed to fetch image %s: %s", url[:80], e)
        _image_cache[url] = None
        return None


# ---------------------------------------------------------------------------
# Paragraph styles
# ---------------------------------------------------------------------------

def _build_styles(font_size_offset: float = 0.0) -> dict[str, ParagraphStyle]:
    """Build paragraph styles, optionally adjusting body font size."""
    off = font_size_offset

    return {
        "section_header": ParagraphStyle(
            "section_header",
            fontName=FONTS["section_header"]["face"],
            fontSize=FONTS["section_header"]["size"] + off,
            leading=FONTS["section_header"]["leading"] + off,
            textColor=white,
            backColor=SECTION_BG,
            spaceBefore=6,
            spaceAfter=4,
            leftIndent=2,
            rightIndent=2,
            alignment=TA_LEFT,
        ),
        "lead_headline": ParagraphStyle(
            "lead_headline",
            fontName=FONTS["lead_headline"]["face"],
            fontSize=FONTS["lead_headline"]["size"],
            leading=FONTS["lead_headline"]["leading"],
            spaceBefore=2,
            spaceAfter=1,
            alignment=TA_LEFT,
        ),
        "headline": ParagraphStyle(
            "headline",
            fontName=FONTS["headline"]["face"],
            fontSize=FONTS["headline"]["size"],
            leading=FONTS["headline"]["leading"],
            spaceBefore=2,
            spaceAfter=1,
            alignment=TA_LEFT,
        ),
        "subtitle": ParagraphStyle(
            "subtitle",
            fontName=FONTS["subtitle"]["face"],
            fontSize=FONTS["subtitle"]["size"],
            leading=FONTS["subtitle"]["leading"],
            spaceAfter=2,
            alignment=TA_LEFT,
        ),
        "body": ParagraphStyle(
            "body",
            fontName=FONTS["body"]["face"],
            fontSize=FONTS["body"]["size"] + off,
            leading=FONTS["body"]["leading"] + off,
            alignment=TA_JUSTIFY,
            spaceAfter=2,
        ),
        "body_bold": ParagraphStyle(
            "body_bold",
            fontName=FONTS["body_bold"]["face"],
            fontSize=FONTS["body_bold"]["size"] + off,
            leading=FONTS["body_bold"]["leading"] + off,
            alignment=TA_LEFT,
            spaceBefore=3,
            spaceAfter=1,
        ),
        "meta": ParagraphStyle(
            "meta",
            fontName=FONTS["meta"]["face"],
            fontSize=FONTS["meta"]["size"],
            leading=FONTS["meta"]["leading"],
            textColor=DARK_GRAY,
            spaceAfter=1,
            alignment=TA_LEFT,
        ),
        "summary_note": ParagraphStyle(
            "summary_note",
            fontName="Helvetica-Oblique",
            fontSize=6,
            leading=7.5,
            textColor=DARK_GRAY,
            spaceAfter=2,
            alignment=TA_LEFT,
        ),
    }


# ---------------------------------------------------------------------------
# Page callbacks — draw masthead, headers, footers, column rules
# ---------------------------------------------------------------------------

def _draw_column_rules(canvas, doc):
    """Draw thin vertical rules between columns."""
    canvas.saveState()
    canvas.setStrokeColor(RULE_COLOR)
    canvas.setLineWidth(0.25)
    for i in range(1, NUM_COLS):
        x = MARGIN_H + i * (COL_WIDTH + COL_GAP) - COL_GAP / 2
        y_top = PAGE_HEIGHT - MARGIN_TOP - MASTHEAD_HEIGHT if doc.page == 1 else PAGE_HEIGHT - MARGIN_TOP - HEADER_HEIGHT
        y_bottom = MARGIN_BOTTOM + FOOTER_HEIGHT
        canvas.line(x, y_bottom, x, y_top)
    canvas.restoreState()


def _draw_masthead(canvas, doc, target_date: str):
    """Page 1 masthead: large title, date, edition info."""
    canvas.saveState()

    y_base = PAGE_HEIGHT - MARGIN_TOP

    # Title
    canvas.setFont("Helvetica-Bold", 22)
    canvas.drawString(MARGIN_H, y_base - 16, "NEWSPAIPER")

    # Subtitle line
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(DARK_GRAY)
    canvas.drawString(MARGIN_H, y_base - 23, f"AI & Tech Daily  |  {target_date}")

    # Thin rule under masthead
    canvas.setStrokeColor(black)
    canvas.setLineWidth(0.75)
    rule_y = y_base - MASTHEAD_HEIGHT
    canvas.line(MARGIN_H, rule_y, PAGE_WIDTH - MARGIN_H, rule_y)

    canvas.restoreState()
    _draw_column_rules(canvas, doc)


def _draw_continuation_header(canvas, doc, target_date: str):
    """Pages 2+: compact header with title and page number."""
    canvas.saveState()

    y_base = PAGE_HEIGHT - MARGIN_TOP

    canvas.setFont("Helvetica-Bold", 7)
    canvas.drawString(MARGIN_H, y_base - 6, "NEWSPAIPER")

    canvas.setFont("Helvetica", 6)
    canvas.setFillColor(DARK_GRAY)
    page_info = f"{target_date}  |  p. {doc.page}"
    canvas.drawRightString(PAGE_WIDTH - MARGIN_H, y_base - 6, page_info)

    # Rule under header
    canvas.setStrokeColor(RULE_COLOR)
    canvas.setLineWidth(0.5)
    rule_y = y_base - HEADER_HEIGHT
    canvas.line(MARGIN_H, rule_y, PAGE_WIDTH - MARGIN_H, rule_y)

    canvas.restoreState()
    _draw_column_rules(canvas, doc)


def _draw_footer(canvas, doc, target_date: str):
    """Footer on every page: thin rule + centered text."""
    canvas.saveState()

    y_rule = MARGIN_BOTTOM + FOOTER_HEIGHT
    canvas.setStrokeColor(RULE_COLOR)
    canvas.setLineWidth(0.3)
    canvas.line(MARGIN_H, y_rule, PAGE_WIDTH - MARGIN_H, y_rule)

    canvas.setFont("Helvetica", 5)
    canvas.setFillColor(DARK_GRAY)
    footer_text = f"Newspaiper  \u2022  {target_date}  \u2022  p. {doc.page}"
    canvas.drawCentredString(PAGE_WIDTH / 2, MARGIN_BOTTOM + 1, footer_text)

    canvas.restoreState()


# ---------------------------------------------------------------------------
# Build frames (columns) for each page template
# ---------------------------------------------------------------------------

def _make_col_frames(top_offset: float) -> list[Frame]:
    """Create NUM_COLS column frames below the given top offset."""
    frames = []
    y_bottom = MARGIN_BOTTOM + FOOTER_HEIGHT + 2 * mm
    col_height = PAGE_HEIGHT - top_offset - y_bottom

    for i in range(NUM_COLS):
        x = MARGIN_H + i * (COL_WIDTH + COL_GAP)
        frames.append(Frame(
            x, y_bottom, COL_WIDTH, col_height,
            leftPadding=0, rightPadding=0,
            topPadding=0, bottomPadding=0,
            id=f"col{i}",
        ))

    return frames


# ---------------------------------------------------------------------------
# Convert article body text to reportlab flowables
# ---------------------------------------------------------------------------

def _escape_xml(text: str) -> str:
    """Escape text for use inside reportlab Paragraph XML."""
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _inline_markdown(text: str) -> str:
    """Convert inline markdown (**bold**, *italic*, `code`) to reportlab XML."""
    text = _escape_xml(text)
    # Bold: **text** or __text__
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)
    # Italic: *text* or _text_ (but not inside bold)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", text)
    # Inline code: `text`
    text = re.sub(r"`([^`]+)`", r'<font face="Courier" size="7">\1</font>', text)
    return text


def _body_to_flowables(
    body: str,
    styles: dict[str, ParagraphStyle],
) -> list:
    """Convert markdown article body into a list of Paragraph flowables."""
    flowables = []

    # Split into blocks by double-newline (markdown paragraph boundary)
    blocks = re.split(r"\n\n+", body)

    in_code_block = False
    code_lines: list[str] = []
    code_line_count = 0

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        lines = block.splitlines()

        # Handle fenced code blocks (may span multiple double-newline blocks)
        processed_lines: list[str] = []
        for line in lines:
            stripped = line.strip()

            if stripped.startswith("```"):
                if in_code_block:
                    # End of code block — flush
                    if code_lines:
                        code_text = "<br/>".join(
                            _escape_xml(cl) for cl in code_lines
                        )
                        flowables.append(Paragraph(
                            f'<font face="Courier" size="6.5">{code_text}</font>',
                            styles["body"],
                        ))
                        flowables.append(Spacer(1, 2))
                    code_lines = []
                    code_line_count = 0
                    in_code_block = False
                else:
                    # Start of code block
                    in_code_block = True
                continue

            if in_code_block:
                code_line_count += 1
                if code_line_count <= 15:
                    code_lines.append(line)
                elif code_line_count == 16:
                    code_lines.append("    ...")
                continue

            processed_lines.append(line)

        if in_code_block:
            # Block continues into next paragraph break
            continue

        if not processed_lines:
            continue

        joined = "\n".join(processed_lines)

        # Markdown heading: # Heading
        m = re.match(r"^(#{1,3})\s+(.+)$", joined.strip())
        if m:
            heading_text = m.group(2).strip()
            flowables.append(Paragraph(
                _inline_markdown(heading_text), styles["body_bold"],
            ))
            continue

        # Bullet list: lines starting with - or * or numbered
        all_lines = [l.strip() for l in processed_lines if l.strip()]
        if all_lines and all(
            re.match(r"^[-*\u2022]|\d+\.", l) for l in all_lines
        ):
            for li in all_lines:
                li = re.sub(r"^[-*\u2022]\s*", "", li)
                li = re.sub(r"^\d+\.\s*", "", li)
                if li:
                    flowables.append(Paragraph(
                        f"\u2022 {_inline_markdown(li)}", styles["body"],
                    ))
            continue

        # Regular paragraph — join lines, process inline markdown
        text = " ".join(l.strip() for l in processed_lines)
        flowables.append(Paragraph(_inline_markdown(text), styles["body"]))

    # Flush any unclosed code block
    if code_lines:
        code_text = "<br/>".join(_escape_xml(cl) for cl in code_lines)
        flowables.append(Paragraph(
            f'<font face="Courier" size="6.5">{code_text}</font>',
            styles["body"],
        ))

    return flowables


def _article_flowables(
    article: Article,
    styles: dict[str, ParagraphStyle],
    is_lead: bool = False,
) -> list:
    """Build the full list of flowables for one article."""
    items = []

    # Meta line
    meta_parts = []
    if article.source_domain:
        meta_parts.append(article.source_domain)
    if article.read_time:
        meta_parts.append(article.read_time)
    if meta_parts:
        items.append(Paragraph(_escape_xml(" \u2022 ".join(meta_parts)), styles["meta"]))

    # Headline
    hl_style = styles["lead_headline"] if is_lead else styles["headline"]
    items.append(Paragraph(_escape_xml(article.title), hl_style))

    # Subtitle
    if article.subtitle:
        items.append(Paragraph(_escape_xml(article.subtitle), styles["subtitle"]))

    # Paywall note
    if article.is_paywalled:
        items.append(Paragraph(
            "[Full article paywalled \u2014 summary from TLDR]",
            styles["summary_note"],
        ))

    # Image (if available)
    img_flowable = None
    if article.image_url:
        img_flowable = _fetch_image(article.image_url)

    # Body
    body_flowables = _body_to_flowables(article.body, styles)

    # KeepTogether for headline + image + first paragraph to avoid orphans
    header_block = list(items)
    if img_flowable is not None:
        header_block.append(Spacer(1, 2))
        header_block.append(img_flowable)
        header_block.append(Spacer(1, 2))
    if body_flowables:
        header_block.append(body_flowables[0])
        remaining_body = body_flowables[1:]
    else:
        remaining_body = []

    result = [KeepTogether(header_block)]
    result.extend(remaining_body)

    # Separator rule
    result.append(Spacer(1, 3))
    result.append(HRFlowable(
        width="100%", thickness=0.3,
        color=RULE_COLOR, spaceAfter=4, spaceBefore=2,
    ))

    return result


# ---------------------------------------------------------------------------
# Build the full story (list of flowables for the entire document)
# ---------------------------------------------------------------------------

def _build_story(
    articles: list[Article],
    styles: dict[str, ParagraphStyle],
) -> list:
    """Build the complete document story from articles."""
    story = []
    current_category = None

    for i, article in enumerate(articles):
        # Section header when category changes
        if article.category != current_category:
            current_category = article.category
            story.append(Paragraph(
                _escape_xml(current_category),
                styles["section_header"],
            ))

        is_lead = (i == 0)
        story.extend(_article_flowables(article, styles, is_lead=is_lead))

    return story


# ---------------------------------------------------------------------------
# Page count targeting
# ---------------------------------------------------------------------------

def _trial_build(target_date: str, font_offset: float, story: list) -> int:
    """Build the document to an in-memory buffer and return the page count."""
    import io
    buf = io.BytesIO()
    doc = _build_doc(buf, target_date, font_offset)
    doc.build(story)
    return doc.page


def _round_up_to_4(n: int) -> int:
    """Round up to the nearest multiple of 4."""
    return math.ceil(n / 4) * 4


def _fit_articles(
    articles: list[Article],
    target_date: str,
    font_offset: float,
    styles: dict[str, ParagraphStyle],
    target_pages: int,
    booklet: bool,
) -> tuple[list[Article], int]:
    """Fit articles to page budget. Returns (articles, actual_page_count).

    Strategy:
    - Build with all articles to find natural page count.
    - If booklet: round natural page count up to next multiple of 4.
      That becomes the budget. If it exceeds target_pages, trim to target_pages.
    - If not booklet: budget = target_pages.
    - Only trim if natural pages > budget. Drop shortest articles first using
      binary search (fast — at most ~log2(N) trial builds).
    """
    # Trial build with all articles
    story = _build_story(articles, styles)
    story.insert(0, NextPageTemplate("later"))
    natural_pages = _trial_build(target_date, font_offset, list(story))

    if target_pages <= 0:
        if booklet:
            # Round UP to nearest multiple of 4 — keep all content
            budget = _round_up_to_4(natural_pages)
            logger.info("Natural page count: %d, rounded up to %d for booklet", natural_pages, budget)
            return articles, natural_pages
        else:
            logger.info("Natural page count: %d pages (no limit)", natural_pages)
            return articles, natural_pages
    elif booklet:
        budget = min(_round_up_to_4(natural_pages), target_pages)
    else:
        budget = target_pages

    logger.info("Natural page count: %d, budget: %d pages", natural_pages, budget)

    if natural_pages <= budget:
        return articles, natural_pages

    # Need to trim. Sort article indices by word count (shortest first to drop).
    ranked = sorted(range(len(articles)), key=lambda i: articles[i].word_count)

    # Binary search for how many to drop
    lo, hi = 0, len(ranked)
    best_drop = len(ranked)
    while lo <= hi:
        mid = (lo + hi) // 2
        drop_set = set(ranked[:mid])
        remaining = [a for i, a in enumerate(articles) if i not in drop_set]
        if not remaining:
            lo = mid + 1
            continue
        story = _build_story(remaining, styles)
        story.insert(0, NextPageTemplate("later"))
        pages = _trial_build(target_date, font_offset, list(story))
        if pages <= budget:
            best_drop = mid
            hi = mid - 1
        else:
            lo = mid + 1

    drop_set = set(ranked[:best_drop])
    kept = [a for i, a in enumerate(articles) if i not in drop_set]

    # Final page count
    story = _build_story(kept, styles)
    story.insert(0, NextPageTemplate("later"))
    final_pages = _trial_build(target_date, font_offset, list(story))

    logger.info(
        "Trimmed to %d articles (%d dropped) → %d pages (budget %d)",
        len(kept), best_drop, final_pages, budget,
    )
    return kept, final_pages


def _build_doc(
    output,
    target_date: str,
    font_size_offset: float = 0.0,
) -> BaseDocTemplate:
    """Create the BaseDocTemplate with page templates."""
    doc = BaseDocTemplate(
        output,
        pagesize=A4,
        leftMargin=0, rightMargin=0,
        topMargin=0, bottomMargin=0,
    )
    # Stash for use in callbacks
    doc._target_date = target_date
    doc._font_offset = font_size_offset

    # Page 1 template (with masthead)
    page1_frames = _make_col_frames(MARGIN_TOP + MASTHEAD_HEIGHT)
    page1 = PageTemplate(
        id="first",
        frames=page1_frames,
        onPage=lambda c, d: (_draw_masthead(c, d, target_date), _draw_footer(c, d, target_date)),
    )

    # Continuation pages
    cont_frames = _make_col_frames(MARGIN_TOP + HEADER_HEIGHT)
    cont = PageTemplate(
        id="later",
        frames=cont_frames,
        onPage=lambda c, d: (_draw_continuation_header(c, d, target_date), _draw_footer(c, d, target_date)),
    )

    doc.addPageTemplates([page1, cont])
    return doc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render(
    articles: list[Article],
    target_date: str,
    output_path: str,
    target_pages: int = 12,
    booklet: bool = False,
    font_size: float = 7.0,
) -> Path:
    """Render articles into a multi-column A4 newspaper PDF.

    Returns the Path to the generated PDF.
    """
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    font_offset = font_size - 8.5
    styles = _build_styles(font_offset)

    # Page count targeting
    articles, estimated_pages = _fit_articles(
        articles, target_date, font_offset, styles, target_pages, booklet,
    )

    story = _build_story(articles, styles)
    story.insert(0, NextPageTemplate("later"))

    doc = _build_doc(str(output), target_date, font_offset)

    logger.info("Building PDF with %d articles: %s", len(articles), output)
    doc.build(story)

    actual_pages = doc.page
    logger.info("Generated %d pages (%d articles)", actual_pages, len(articles))

    return output
