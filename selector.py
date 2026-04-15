"""Visual article selection via TLDR.tech proxy.

Transparently proxies tldr.tech through a local server, injecting a selection
overlay on every HTML page. The user browses the real site, clicks articles
to select them, and hits Done. Selected article data is extracted from the
DOM and returned as Article objects.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any
from urllib.parse import urlparse

import requests

from models import Article

logger = logging.getLogger(__name__)

SERVER_TIMEOUT = 600  # 10 minutes
TLDR_BASE = "https://tldr.tech"

# ---------------------------------------------------------------------------
# Injected CSS + JS (appended before </body> on every HTML page)
# ---------------------------------------------------------------------------

INJECTION = r"""
<!-- NEWSPAIPER SELECTOR -->
<style id="np-sel-css">
#np-toolbar {
    position: fixed; top: 0; left: 0; right: 0; z-index: 999999;
    background: #1a1a2e; color: #eee;
    padding: 10px 20px; display: flex; align-items: center; gap: 12px;
    font-family: -apple-system, Helvetica, Arial, sans-serif; font-size: 14px;
    box-shadow: 0 2px 12px rgba(0,0,0,0.4);
}
#np-toolbar .np-title { font-weight: bold; font-size: 16px; letter-spacing: 0.5px; }
#np-toolbar .np-spacer { flex: 1; }
#np-toolbar .np-counter {
    background: #333; padding: 5px 12px; border-radius: 4px;
    font-size: 13px; font-variant-numeric: tabular-nums;
}
#np-toolbar .np-counter.full { border-left: 3px solid #22c55e; }
#np-toolbar .np-counter.summary { border-left: 3px solid #f59e0b; }
#np-toolbar .np-counter.excluded { border-left: 3px solid #666; }
#np-toolbar button {
    padding: 7px 18px; border: none; border-radius: 4px;
    cursor: pointer; font-size: 13px; font-weight: 600;
}
#np-done { background: #22c55e; color: #000; }
#np-done:hover { background: #16a34a; }
#np-select-all, #np-clear-all { background: #444; color: #eee; }
#np-select-all:hover, #np-clear-all:hover { background: #555; }

body { padding-top: 52px !important; }

article.mt-3 {
    cursor: pointer !important;
    transition: opacity 0.15s ease, border-color 0.15s ease;
    border-left: 5px solid transparent !important;
    padding-left: 10px !important;
    margin-left: -15px !important;
}
article.mt-3[data-np-state="excluded"] { opacity: 0.25 !important; }
article.mt-3[data-np-state="excluded"] h3 { text-decoration: line-through !important; }
article.mt-3[data-np-state="full"] { border-left-color: #22c55e !important; opacity: 1 !important; }
article.mt-3[data-np-state="summary"] { border-left-color: #f59e0b !important; opacity: 0.65 !important; }
article.mt-3[data-np-sponsor="true"] { opacity: 0.10 !important; pointer-events: none !important; }
</style>

<div id="np-toolbar">
    <span class="np-title">NEWSPAIPER</span>
    <span class="np-spacer"></span>
    <span class="np-counter full" id="np-count-full">0 full</span>
    <span class="np-counter summary" id="np-count-summary">0 summary</span>
    <span class="np-counter excluded" id="np-count-excluded">0 excl</span>
    <button id="np-select-all">Select All</button>
    <button id="np-clear-all">Clear All</button>
    <button id="np-done">Done &#10003;</button>
</div>

<script id="np-sel-js">
(function() {
    // Prevent re-init if already injected (e.g. SPA navigation)
    if (window.__np_init) return;
    window.__np_init = true;

    const STORAGE_KEY = 'np_selection';
    // selection: { "url": { mode: "full"|"summary", title, readTime, summary, category } }
    function loadSel() {
        try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}'); } catch(e) { return {}; }
    }
    function saveSel(s) { localStorage.setItem(STORAGE_KEY, JSON.stringify(s)); }

    const states = ['excluded', 'full', 'summary'];

    function initArticles() {
        const sel = loadSel();

        document.querySelectorAll('article.mt-3').forEach(el => {
            // Skip already initialized
            if (el.dataset.npInit) return;
            el.dataset.npInit = 'true';

            const link = el.querySelector('a.font-bold');
            if (!link) return;
            const h3 = link.querySelector('h3');
            if (!h3) return;

            const rawTitle = h3.textContent.trim();

            // Skip sponsors
            if (rawTitle.includes('(Sponsor)') || rawTitle.includes('(sponsor)')) {
                el.dataset.npSponsor = 'true';
                return;
            }

            const url = link.getAttribute('href') || '';
            if (!url) return;

            // Clean URL (strip tracking params)
            let cleanUrl = url;
            try {
                const u = new URL(url);
                for (const key of [...u.searchParams.keys()]) {
                    if (key.startsWith('utm_') || key === 'ref' || key === 'source') {
                        u.searchParams.delete(key);
                    }
                }
                cleanUrl = u.toString();
            } catch(e) {}

            el.dataset.npUrl = cleanUrl;

            // Restore state
            if (sel[cleanUrl]) {
                el.dataset.npState = sel[cleanUrl].mode;
            } else {
                el.dataset.npState = 'excluded';
            }

            // Extract metadata
            const readTimeMatch = rawTitle.match(/\((\d+)\s*min(?:ute)?\s*read\)/);
            const readTime = readTimeMatch ? readTimeMatch[1] + ' min' : '';
            const readTimeMin = readTimeMatch ? parseInt(readTimeMatch[1]) : 0;
            const title = rawTitle.replace(/\s*\(\d+\s*min(?:ute)?\s*read\)/, '').trim();

            const summaryDiv = el.querySelector('div.newsletter-html');
            const summary = summaryDiv ? summaryDiv.textContent.trim() : '';

            // Find category from nearest section header
            let category = '';
            let section = el.closest('section');
            if (section) {
                const header = section.querySelector('header h3');
                if (header) category = header.textContent.trim();
            }

            // Store metadata on element for extraction on Done
            el.dataset.npTitle = title;
            el.dataset.npReadTime = readTime;
            el.dataset.npReadTimeMin = readTimeMin;
            el.dataset.npSummary = summary;
            el.dataset.npCategory = category;

            el.addEventListener('click', function(e) {
                e.preventDefault();
                e.stopPropagation();
                const cur = states.indexOf(this.dataset.npState);
                this.dataset.npState = states[(cur + 1) % 3];

                const s = loadSel();
                if (this.dataset.npState === 'excluded') {
                    delete s[this.dataset.npUrl];
                } else {
                    s[this.dataset.npUrl] = {
                        mode: this.dataset.npState,
                        url: this.dataset.npUrl,
                        title: this.dataset.npTitle,
                        readTime: this.dataset.npReadTime,
                        readTimeMin: parseInt(this.dataset.npReadTimeMin) || 0,
                        summary: this.dataset.npSummary,
                        category: this.dataset.npCategory
                    };
                }
                saveSel(s);
                updateCounters();
            });
        });
    }

    function updateCounters() {
        const s = loadSel();
        let full = 0, summary = 0;
        for (const v of Object.values(s)) {
            if (v.mode === 'full') full++;
            else if (v.mode === 'summary') summary++;
        }
        document.getElementById('np-count-full').textContent = full + ' full';
        document.getElementById('np-count-summary').textContent = summary + ' summary';

        let excl = 0;
        document.querySelectorAll('article.mt-3[data-np-url]').forEach(el => {
            if (el.dataset.npState === 'excluded') excl++;
        });
        document.getElementById('np-count-excluded').textContent = excl + ' excl';
    }

    document.getElementById('np-select-all').addEventListener('click', function() {
        const s = loadSel();
        document.querySelectorAll('article.mt-3[data-np-url]').forEach(el => {
            el.dataset.npState = 'full';
            s[el.dataset.npUrl] = {
                mode: 'full',
                url: el.dataset.npUrl,
                title: el.dataset.npTitle,
                readTime: el.dataset.npReadTime,
                readTimeMin: parseInt(el.dataset.npReadTimeMin) || 0,
                summary: el.dataset.npSummary,
                category: el.dataset.npCategory
            };
        });
        saveSel(s); updateCounters();
    });

    document.getElementById('np-clear-all').addEventListener('click', function() {
        const s = loadSel();
        document.querySelectorAll('article.mt-3[data-np-url]').forEach(el => {
            el.dataset.npState = 'excluded';
            delete s[el.dataset.npUrl];
        });
        saveSel(s); updateCounters();
    });

    document.getElementById('np-done').addEventListener('click', async function() {
        const s = loadSel();
        const selected = Object.values(s);
        try {
            await fetch('/np-done', {
                method: 'POST', body: JSON.stringify(selected),
                headers: {'Content-Type': 'application/json'}
            });
        } catch(e) {}
        localStorage.removeItem(STORAGE_KEY);
        document.body.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100vh;font-family:system-ui,sans-serif;font-size:24px;color:#666;">Selection saved. You can close this tab.</div>';
    });

    // Init now and watch for dynamically loaded content
    initArticles();
    updateCounters();

    // Re-init periodically to catch dynamically loaded articles
    const observer = new MutationObserver(() => { initArticles(); updateCounters(); });
    observer.observe(document.body, { childList: true, subtree: true });
})();
</script>
"""


# ---------------------------------------------------------------------------
# Proxy HTTP server
# ---------------------------------------------------------------------------

class _ProxyHandler(BaseHTTPRequestHandler):
    """Transparently proxies tldr.tech, injecting selector on HTML pages."""

    selection_result: list[dict] = []
    done_event: threading.Event
    _session: requests.Session

    def _proxy(self):
        """Forward request to tldr.tech and return the response."""
        upstream = TLDR_BASE + self.path
        logger.info("PROXY %s -> %s", self.path, upstream)
        try:
            resp = self.__class__._session.get(upstream, timeout=15, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": self.headers.get("Accept", "*/*"),
                "Accept-Encoding": "identity",
                "Accept-Language": self.headers.get("Accept-Language", "en-US,en;q=0.9"),
            }, allow_redirects=True)
            logger.info("  -> %d %s (%d bytes)", resp.status_code, resp.headers.get("Content-Type", "?"), len(resp.content))
            return resp
        except Exception as e:
            logger.error("Proxy error for %s: %s", self.path, e)
            return None

    def do_GET(self):
        resp = self._proxy()
        if resp is None:
            self.send_response(502)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        ct = resp.headers.get("Content-Type", "")
        content = resp.content

        # Inject selector into HTML pages
        if "text/html" in ct:
            html = content.decode("utf-8", errors="replace")
            # Rewrite absolute links to tldr.tech to go through proxy
            html = html.replace('href="https://tldr.tech/', 'href="/')
            html = html.replace("href='https://tldr.tech/", "href='/")
            if "</body>" in html:
                html = html.replace("</body>", INJECTION + "\n</body>", 1)
            else:
                html += INJECTION
            out = html.encode("utf-8")
        else:
            out = content

        self.send_response(resp.status_code)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(out)))
        if "Cache-Control" in resp.headers:
            self.send_header("Cache-Control", resp.headers["Cache-Control"])
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
        logger.debug(format, *args)


def _extract_domain(url: str) -> str:
    """Extract domain from URL, stripping www. prefix."""
    try:
        host = urlparse(url).netloc
        return host.removeprefix("www.")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def visual_select() -> list[Article]:
    """Open browser with TLDR.tech proxy, return selected articles."""
    handler = _ProxyHandler
    handler.selection_result = []
    handler.done_event = threading.Event()
    handler._session = requests.Session()

    server = HTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    url = f"http://localhost:{port}/"

    logger.info("Selection server at %s", url)
    print(f"\n  Newspaiper selector: {url}\n")

    threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    done = handler.done_event.wait(timeout=SERVER_TIMEOUT)
    server.shutdown()
    handler._session.close()

    if not done:
        logger.warning("Selection timed out — no articles selected")
        return []

    # Convert JS selection data to Article objects
    articles: list[Article] = []
    for item in handler.selection_result:
        if not isinstance(item, dict):
            continue
        source_url = item.get("url", "")
        a = Article(
            title=item.get("title", ""),
            source_url=source_url,
            source_domain=_extract_domain(source_url),
            category=item.get("category", ""),
            read_time=item.get("readTime", ""),
            read_time_minutes=int(item.get("readTimeMin", 0)),
            tldr_summary=item.get("summary", ""),
            fetch_status="pending",
        )
        if item.get("mode") == "summary":
            a.body = a.tldr_summary
            a.word_count = len(a.body.split())
            a.is_paywalled = True

        articles.append(a)

    return articles
