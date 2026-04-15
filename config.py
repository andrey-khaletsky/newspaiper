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
# Typography — Palatino Linotype (registered in renderer.py)
# ---------------------------------------------------------------------------
PALATINO_PATHS = {
    "regular": "C:/Windows/Fonts/pala.ttf",
    "bold":    "C:/Windows/Fonts/palab.ttf",
    "italic":  "C:/Windows/Fonts/palai.ttf",
    "bold_italic": "C:/Windows/Fonts/palabi.ttf",
}

FONTS = {
    "section_header": {"face": "Helvetica-Bold", "size": 8.5, "leading": 10},
    "lead_headline":  {"face": "Palatino-Bold",  "size": 15, "leading": 17.5},
    "headline":       {"face": "Palatino-Bold",  "size": 11.5, "leading": 13.5},
    "subtitle":       {"face": "Palatino-Italic","size": 9, "leading": 11},
    "body":           {"face": "Palatino",       "size": 8.5, "leading": 11},
    "body_bold":      {"face": "Palatino-Bold",  "size": 8.5, "leading": 11},
    "meta":           {"face": "Helvetica",      "size": 6, "leading": 7.5},
}

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_CATEGORIES = ["ai", "tech", "dev", "infosec"]
DEFAULT_PAGES = 12
DEFAULT_FONT_SIZE = 8.5
TLDR_BASE_URL = "https://tldr.tech"

# Reading speed used for paywall detection
WORDS_PER_MINUTE = 250
PAYWALL_THRESHOLD = 0.40  # if word_count < 40% of expected → paywalled

# Fetching
FETCH_DELAY = 0.7          # seconds between requests
FETCH_TIMEOUT = 15         # seconds per request
USER_AGENT = "newspaiper/1.0 (personal newspaper generator)"
