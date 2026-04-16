"""Visual article selection via a local HTML page.

Scrapes articles from TLDR, generates a self-contained selection page,
serves it locally. User clicks to pick articles, hits Done. No proxy needed.
"""

from __future__ import annotations

import json
import logging
import threading
import webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from typing import Any
from urllib.parse import urlparse

from models import Article

logger = logging.getLogger(__name__)

SERVER_TIMEOUT = 600  # 10 minutes


def _escape_html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _build_selection_html(articles: list[Article]) -> str:
    """Build a self-contained HTML selection page from article data."""

    # Build article cards HTML — flat list, JS handles grouping/sorting
    cards_html = ""
    for i, a in enumerate(articles):
        domain = _escape_html(a.source_domain)
        title = _escape_html(a.title)
        summary = _escape_html(a.tldr_summary[:200]) if a.tldr_summary else ""
        read_time = _escape_html(a.read_time) if a.read_time else ""
        cat = _escape_html(a.category)
        words = a.word_count
        img_url = _escape_html(a.image_url) if a.image_url and a.image_url.startswith("http") else ""

        thumb_html = f'<img class="article-thumb" src="{img_url}" loading="lazy" onerror="this.style.display=\'none\'">' if img_url else ""
        words_label = f"{words} words" if words else "summary"

        source_url = _escape_html(a.source_url)

        cards_html += f'''<div class="article" data-idx="{i}" data-state="full" data-words="{words}" data-cat="{cat}" data-cat-order="{i}">
  <div class="article-marker"></div>
  <div class="article-body">
    <div class="article-meta">{domain}{(' · ' + read_time) if read_time else ''} · {words_label}</div>
    <div class="article-title">{title}</div>
    <div class="article-summary">{summary}</div>
  </div>
  {thumb_html}
  <a class="article-link" href="{source_url}" target="_blank" title="Open source article">&#8599;</a>
</div>\n'''

    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>NEWSPAIPER — Select Articles</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
       background: #0f0f0f; color: #e0e0e0; padding-bottom: 80px; }}

#toolbar {{
    position: fixed; top: 0; left: 0; right: 0; z-index: 100;
    background: #1a1a2e; padding: 12px 24px; display: flex; align-items: center; gap: 14px;
    box-shadow: 0 2px 12px rgba(0,0,0,0.5); font-size: 14px;
}}
#np-title {{ font-weight: 700; font-size: 17px; letter-spacing: 0.5px; }}
#toolbar .spacer {{ flex: 1; }}
.counter {{
    background: #2a2a3e; padding: 5px 14px; border-radius: 4px;
    font-size: 13px; font-variant-numeric: tabular-nums;
}}
.counter.full {{ border-left: 3px solid #22c55e; }}
.counter.summary {{ border-left: 3px solid #f59e0b; }}
.counter.excluded {{ border-left: 3px solid #555; }}
#toolbar button {{
    padding: 8px 20px; border: none; border-radius: 5px;
    cursor: pointer; font-size: 13px; font-weight: 600;
}}
#btn-done {{ background: #22c55e; color: #000; }}
#btn-done:hover {{ background: #16a34a; }}
#btn-all, #btn-summary-all, #btn-clear {{ background: #3a3a4e; color: #ddd; }}
#btn-all:hover, #btn-summary-all:hover, #btn-clear:hover {{ background: #4a4a5e; }}
#btn-summary-all {{ border-left: 3px solid #f59e0b; }}
#btn-sort {{ background: #2a2a3e; color: #aaa; border: 1px solid #444; }}
#btn-sort:hover {{ background: #3a3a4e; color: #ddd; }}

.container {{ max-width: 700px; margin: 0 auto; padding: 70px 16px 20px; }}

.cat-header {{
    font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 1.5px;
    color: #888; margin: 24px 0 8px; padding: 6px 0; border-bottom: 1px solid #2a2a2a;
}}

.article {{
    display: flex; gap: 12px; padding: 12px 14px; margin: 4px 0;
    border-radius: 6px; cursor: pointer; transition: all 0.12s;
    border-left: 4px solid transparent;
}}
.article:hover {{ background: #1a1a2a; }}
.article[data-state="full"] {{ border-left-color: #22c55e; opacity: 1; }}
.article[data-state="summary"] {{ border-left-color: #f59e0b; opacity: 0.7; }}
.article[data-state="excluded"] {{ border-left-color: transparent; opacity: 0.3; }}
.article[data-state="excluded"] .article-title {{ text-decoration: line-through; }}

.article-marker {{
    width: 18px; height: 18px; border-radius: 50%; margin-top: 2px; flex-shrink: 0;
    border: 2px solid #555; transition: all 0.12s;
}}
.article[data-state="full"] .article-marker {{ background: #22c55e; border-color: #22c55e; }}
.article[data-state="summary"] .article-marker {{ background: #f59e0b; border-color: #f59e0b; }}

.article-link {{
    flex-shrink: 0; align-self: center; text-decoration: none;
    font-size: 18px; color: #555; padding: 4px 8px; border-radius: 4px;
    transition: color 0.12s, background 0.12s;
}}
.article-link:hover {{ color: #ddd; background: #2a2a3e; }}
.article-thumb {{
    width: 100px; height: 64px; border-radius: 4px; object-fit: cover; flex-shrink: 0;
    background: #1a1a2a; align-self: center;
}}
.article-body {{ flex: 1; min-width: 0; }}
.article-meta {{ font-size: 11px; color: #666; margin-bottom: 3px; }}
.article-title {{ font-size: 14px; font-weight: 600; line-height: 1.3; }}
.article-summary {{ font-size: 12px; color: #777; margin-top: 4px; line-height: 1.4;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }}

.legend {{
    text-align: center; padding: 16px; font-size: 12px; color: #555; margin-top: 8px;
}}
.legend span {{ margin: 0 10px; }}
.legend .dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%;
    vertical-align: middle; margin-right: 4px; }}
.dot-full {{ background: #22c55e; }}
.dot-summary {{ background: #f59e0b; }}
.dot-excluded {{ background: #555; }}
</style>
</head><body>

<div id="toolbar">
    <span id="np-title">NEWSPAIPER</span>
    <span class="spacer"></span>
    <span class="counter full" id="c-full">0 full</span>
    <span class="counter summary" id="c-summary">0 summary</span>
    <span class="counter excluded" id="c-excluded">0 excluded</span>
    <button id="btn-sort">Sort: by topic</button>
    <button id="btn-all">All Full</button>
    <button id="btn-summary-all">All Summary</button>
    <button id="btn-clear">Clear All</button>
    <button id="btn-done">Done &#10003;</button>
</div>

<div class="container">
<div id="article-list">
{cards_html}
</div>
<div class="legend">
    <span><span class="dot dot-full"></span> Click once = full text</span>
    <span><span class="dot dot-summary"></span> Click twice = summary only</span>
    <span><span class="dot dot-excluded"></span> Click three = excluded</span>
</div>
</div>

<script>
const states = ['full', 'summary', 'excluded'];
const sortModes = ['topic', 'longest', 'shortest'];
let currentSort = 0;

const container = document.getElementById('article-list');

function update() {{
    let full = 0, summary = 0, excluded = 0;
    document.querySelectorAll('.article').forEach(el => {{
        const s = el.dataset.state;
        if (s === 'full') full++;
        else if (s === 'summary') summary++;
        else excluded++;
    }});
    document.getElementById('c-full').textContent = full + ' full';
    document.getElementById('c-summary').textContent = summary + ' summary';
    document.getElementById('c-excluded').textContent = excluded + ' excluded';
}}

function reorder() {{
    const mode = sortModes[currentSort];
    const articles = [...document.querySelectorAll('.article')];
    const headers = [...document.querySelectorAll('.cat-header')];

    // Remove all from DOM
    headers.forEach(h => h.remove());
    articles.forEach(a => a.remove());

    if (mode === 'topic') {{
        // Group by category, original order within each
        articles.sort((a, b) => parseInt(a.dataset.catOrder) - parseInt(b.dataset.catOrder));
        let lastCat = '';
        articles.forEach(el => {{
            if (el.dataset.cat !== lastCat) {{
                lastCat = el.dataset.cat;
                const h = document.createElement('div');
                h.className = 'cat-header';
                h.textContent = lastCat;
                container.appendChild(h);
            }}
            container.appendChild(el);
        }});
    }} else {{
        // Sort by word count
        const dir = mode === 'longest' ? -1 : 1;
        articles.sort((a, b) => dir * (parseInt(a.dataset.words || 0) - parseInt(b.dataset.words || 0)));
        articles.forEach(el => container.appendChild(el));
    }}

    document.getElementById('btn-sort').textContent = 'Sort: ' + mode;
}}

// Click to cycle state
document.querySelectorAll('.article').forEach(el => {{
    el.addEventListener('click', (e) => {{
        // Don't cycle state when clicking the source link
        if (e.target.closest('.article-link')) return;
        const cur = states.indexOf(el.dataset.state);
        el.dataset.state = states[(cur + 1) % 3];
        update();
    }});
}});

document.getElementById('btn-sort').addEventListener('click', () => {{
    currentSort = (currentSort + 1) % sortModes.length;
    reorder();
}});

document.getElementById('btn-all').addEventListener('click', () => {{
    document.querySelectorAll('.article').forEach(el => el.dataset.state = 'full');
    update();
}});

document.getElementById('btn-summary-all').addEventListener('click', () => {{
    document.querySelectorAll('.article').forEach(el => el.dataset.state = 'summary');
    update();
}});

document.getElementById('btn-clear').addEventListener('click', () => {{
    document.querySelectorAll('.article').forEach(el => el.dataset.state = 'excluded');
    update();
}});

document.getElementById('btn-done').addEventListener('click', async () => {{
    const result = [];
    document.querySelectorAll('.article').forEach(el => {{
        if (el.dataset.state !== 'excluded') {{
            result.push({{ idx: parseInt(el.dataset.idx), mode: el.dataset.state }});
        }}
    }});
    await fetch('/np-done', {{
        method: 'POST', body: JSON.stringify(result),
        headers: {{'Content-Type': 'application/json'}}
    }});
    document.body.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100vh;font-size:22px;color:#666;">Selection saved. You can close this tab.</div>';
}});

// Initial render — group by topic
reorder();
update();
</script>
</body></html>"""


# ---------------------------------------------------------------------------
# Minimal HTTP server — serves the selection page and receives the result
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    page_html: str = ""
    selection_result: list[dict] = []
    done_event: threading.Event

    def do_GET(self):
        out = self.__class__.page_html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_POST(self):
        if self.path == "/np-done":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                self.__class__.selection_result = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                self.__class__.selection_result = []
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            self.__class__.done_event.set()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        pass  # silence request logs


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def visual_select(articles: list[Article]) -> list[Article]:
    """Show a local selection page in the browser, return selected articles."""
    handler = _Handler
    handler.page_html = _build_selection_html(articles)
    handler.selection_result = []
    handler.done_event = threading.Event()

    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    url = f"http://localhost:{port}/"

    logger.info("Selection page at %s", url)
    print(f"\n  Newspaiper selector: {url}\n")

    threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
    threading.Thread(target=server.serve_forever, daemon=True).start()

    done = handler.done_event.wait(timeout=SERVER_TIMEOUT)
    server.shutdown()

    if not done:
        logger.warning("Selection timed out")
        return []

    # Map selections back to articles
    selected: list[Article] = []
    for item in handler.selection_result:
        if not isinstance(item, dict):
            continue
        idx = item.get("idx")
        mode = item.get("mode", "full")
        if idx is None or idx < 0 or idx >= len(articles):
            continue

        a = articles[idx]
        if mode == "summary":
            a.body = a.tldr_summary or a.body
            a.word_count = len(a.body.split())
            a.is_paywalled = True
        selected.append(a)

    return selected
