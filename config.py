"""Constants: page geometry, fonts, and defaults for newspaiper."""

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm

# ---------------------------------------------------------------------------
# Page geometry — A4 Portrait (210 × 297 mm)
# ---------------------------------------------------------------------------
PAGE_WIDTH, PAGE_HEIGHT = A4  # 595.28 × 841.89 pt

MARGIN_H = 12 * mm        # horizontal (left + right)
MARGIN_TOP = 10 * mm
MARGIN_BOTTOM = 10 * mm

NUM_COLS = 3
COL_GAP = 4 * mm
COL_WIDTH = (PAGE_WIDTH - 2 * MARGIN_H - (NUM_COLS - 1) * COL_GAP) / NUM_COLS

# Header / footer heights
MASTHEAD_HEIGHT = 24 * mm   # page 1 only
HEADER_HEIGHT = 9 * mm      # pages 2+
FOOTER_HEIGHT = 6 * mm

# ---------------------------------------------------------------------------
# Typography — all reportlab built-in fonts
# ---------------------------------------------------------------------------
FONTS = {
    "section_header": {"face": "Helvetica-Bold", "size": 7, "leading": 8.5},
    "lead_headline":  {"face": "Times-Bold",     "size": 13, "leading": 15},
    "headline":       {"face": "Times-Bold",     "size": 9.5, "leading": 11.5},
    "subtitle":       {"face": "Times-Italic",   "size": 7.5, "leading": 9.5},
    "body":           {"face": "Times-Roman",    "size": 7, "leading": 8.8},
    "body_bold":      {"face": "Times-Bold",     "size": 7, "leading": 8.8},
    "meta":           {"face": "Helvetica",      "size": 5, "leading": 6.5},
}

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_CATEGORIES = ["ai", "tech", "dev", "infosec"]
DEFAULT_PAGES = 12
DEFAULT_FONT_SIZE = 7.0
TLDR_BASE_URL = "https://tldr.tech"

# Reading speed used for paywall detection
WORDS_PER_MINUTE = 250
PAYWALL_THRESHOLD = 0.40  # if word_count < 40% of expected → paywalled

# Fetching
FETCH_DELAY = 0.7          # seconds between requests
FETCH_TIMEOUT = 15         # seconds per request
USER_AGENT = "newspaiper/1.0 (personal newspaper generator)"
