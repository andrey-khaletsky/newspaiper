# Newspaiper — Project Specification

AI & Tech newspaper generator. Fetches full-text articles from TLDR.tech, lays them out in a multi-column A4 portrait PDF ready for booklet printing.

## Goal

Run a single command and get a printable newspaper PDF:

```bash
python newspaiper.py                    # today's edition
python newspaiper.py --date 2026-04-13  # specific date
python newspaiper.py --pages 12         # target page count
```

Output: `newspaiper_YYYY-MM-DD.pdf` — A4 portrait, 3-column, paginated, booklet-ready (page count divisible by 4).

## Architecture

Three-stage pipeline:

```
[1. Harvest]  →  [2. Clean & Validate]  →  [3. Layout & Render]
TLDR.tech        trafilatura / BS4          reportlab
```

### Stage 1 — Harvest

1. Fetch the TLDR newsletter pages for the given date. Default categories: `ai`, `tech`, `dev`, `infosec`. Configurable via `--categories`.
   - URL pattern: `https://tldr.tech/{category}/{date}`
2. Parse each newsletter page to extract article entries: **title**, **source URL**, **read time**, **TLDR summary**, **category**.
3. For each article, fetch the **full source URL** to get the complete article text.
4. Use `trafilatura` (preferred) or `BeautifulSoup` + `readability-lxml` for article body extraction. `trafilatura` handles most sites well out of the box.
5. Respect rate limits: add a small delay between fetches (0.5-1s).

### Stage 2 — Clean & Validate

For each fetched article:

1. Strip navigation, ads, footers, sidebars, related articles, subscription CTAs.
2. Preserve structure: headings (h2/h3), paragraphs, bullet lists, numbered lists. Strip images, embedded tweets, and code blocks longer than 15 lines (truncate with `...`).
3. **Validate word count vs. read time**:
   - Expected: ~250 words per minute of read time (a "10 min read" ≈ 2,500 words).
   - If actual word count < 40% of expected → mark as `paywalled` or `extraction_failed`.
   - For paywalled articles: include the TLDR summary (from step 1) instead, with a note: `[Full article paywalled — summary from TLDR]`.
4. Deduplicate by title similarity (fuzzy match, >85% → drop the shorter one).
5. Sort articles by category, then by read time descending (meatiest articles first within each section).

### Stage 3 — Layout & Render

Generate a multi-column newspaper PDF using `reportlab` (Platypus framework with `BaseDocTemplate` and multi-frame column layout).

#### Page geometry — A4 Portrait (210 × 297 mm)

```
Margins:        12mm horizontal, 10mm top/bottom
Columns:        3
Column gap:     4mm
Column width:   (210 - 24 - 8) / 3 ≈ 59.3mm
```

#### Page templates

- **Page 1 (masthead)**: Large title "NEWSPAIPER", subtitle, date, edition info. Header height: ~24mm. Column separators drawn as thin vertical rules.
- **Pages 2+ (continuation)**: Compact header with "NEWSPAIPER" left-aligned, date + page number right-aligned. Header height: ~9mm.
- **All pages**: Footer with thin rule, centered "Newspaiper • {date} • p. {n}". Footer height: ~6mm.

#### Typography

All fonts are reportlab built-ins (no external fonts needed):

| Element | Font | Size | Leading | Alignment |
|---------|------|------|---------|-----------|
| Section header | Helvetica-Bold | 7pt | 8.5pt | Left, white on black bg |
| Lead headline (1st article) | Times-Bold | 13pt | 15pt | Left |
| Headline | Times-Bold | 9.5pt | 11.5pt | Left |
| Subtitle | Times-Italic | 7.5pt | 9.5pt | Left |
| Body text | Times-Roman | 7pt | 8.8pt | Justified |
| Sub-heading in body | Times-Bold | 7pt | 8.8pt | Left |
| Article meta (source, time) | Helvetica | 5pt | 6.5pt | Left, grey |

#### Article flow

Each article renders as:
1. Meta line (source • read time)
2. Headline
3. Subtitle (if present)
4. Body paragraphs (with sub-headings preserved)
5. Horizontal rule separator

Text flows across all 3 columns on a page, then continues to the next page. `KeepTogether` is NOT used on full articles (they're too long) — only on the headline+subtitle+first-paragraph block to avoid orphaned headers.

#### Page count targeting

- User specifies `--pages N` (default: 12, must be divisible by 4 for booklet printing).
- If there's not enough content to fill N pages: reduce font size by 0.5pt steps, or include more categories.
- If there's too much content: truncate the article list (drop shortest articles first).
- Final page count adjustment: if result is not divisible by 4 and `--booklet` flag is set, add a blank page or "Index" page to pad.

## File structure

```
newspaiper/
├── CLAUDE.md              # This spec (for Claude Code context)
├── newspaiper.py          # Main entry point / CLI
├── harvester.py           # Stage 1: fetch TLDR + source articles
├── cleaner.py             # Stage 2: extract, clean, validate
├── renderer.py            # Stage 3: PDF layout engine
├── models.py              # Article dataclass / schema
├── config.py              # Constants, page geometry, fonts
├── requirements.txt       # Dependencies
└── output/                # Generated PDFs land here
```

## Data model

```python
@dataclass
class Article:
    title: str
    subtitle: str           # optional
    source_url: str
    source_domain: str      # e.g. "vinvashishta.substack.com"
    category: str           # AI, TECH, DEV, INFOSEC, DATA, etc.
    read_time: str          # e.g. "17 min"
    read_time_minutes: int  # parsed integer
    tldr_summary: str       # short summary from TLDR page
    body: str               # full article text (markdown-ish)
    word_count: int
    is_paywalled: bool
    fetch_status: str       # "ok", "paywalled", "failed", "timeout"
```

## Dependencies

```
requests
beautifulsoup4
trafilatura
reportlab
```

All pip-installable with no system dependencies.

## CLI interface

```
usage: newspaiper.py [-h] [--date DATE] [--categories CATS]
                     [--pages N] [--booklet] [--output PATH]
                     [--font-size SIZE] [--cols COLS]

options:
  --date DATE        Newsletter date (YYYY-MM-DD, default: today)
  --categories CATS  Comma-separated: ai,tech,dev,infosec,data
                     (default: ai,tech,dev,infosec)
  --pages N          Target page count (default: 12)
  --booklet          Ensure page count is divisible by 4
  --output PATH      Output PDF path (default: output/newspaiper_DATE.pdf)
  --font-size SIZE   Base body font size in pt (default: 7.0)
  --cols COLS        Number of columns (default: 3)
```

## Development priorities

1. **harvester.py** — Get this working first. Fetch a TLDR page, parse article links, fetch each source URL. Print a summary: title, source, word count, status.
2. **cleaner.py** — Focus on `trafilatura` extraction. Add the read-time validation check. Handle edge cases: empty body, HTML-only content, non-English articles.
3. **renderer.py** — Port the layout engine from the prototype (see below). The prototype already handles masthead, continuation headers, footers, column separators, section headers, multi-page text flow.
4. **CLI + integration** — Wire the three stages together. Add page count targeting.

## Prototype reference

A working prototype of the renderer exists (developed interactively). Key design decisions already validated:

- `BaseDocTemplate` with `Frame` per column + `PageTemplate` for page 1 vs continuation pages
- `onPage` callbacks for masthead/header/footer/column separators drawn on the canvas
- Body text converted from markdown-ish format to reportlab `Paragraph` flowables
- Horizontal `Rule` flowable between articles
- Section headers as white-on-black `Paragraph` with `backColor`

## Quality checklist

- [ ] 10-min-read articles have 1,500+ words (not 3 paragraphs)
- [ ] Paywalled articles clearly marked, TLDR summary used as fallback
- [ ] No orphaned section headers at bottom of columns
- [ ] Page count divisible by 4 when `--booklet` is set
- [ ] Column text is justified, no rivers wider than 3mm
- [ ] All articles have source attribution
- [ ] PDF file size reasonable (<2MB for 12 pages of text)

## Future enhancements (not in v1)

- RSS feed support (beyond TLDR)
- Cron job / scheduled generation
- Custom header image / logo
- Table of contents on page 1
- Two-page "front page" layout with larger lead article spanning 2 columns
- Image extraction and placement (article hero images)
- Booklet imposition (reorder pages for saddle-stitch printing)
