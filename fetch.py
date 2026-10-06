# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27", "pypdf>=5"]
# ///
"""Step 2: fetch each venue's website (homepage + likely happy-hour/menu pages + PDFs).

Writes data/pages/<site>.json = [{url, text}] and caches raw responses in
data/cache/ so re-runs are free. Respects robots.txt, one request at a time per
site, 16 sites in parallel.

    uv run fetch.py [--limit N] [--only "Name,Name"] [--refresh]
"""

import asyncio
import hashlib
import io
import json
import re
import sys
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import logging

import httpx

logging.getLogger("pypdf").setLevel(logging.ERROR)

UA = "Mozilla/5.0 (compatible; LocalLens/0.1; student deals map)"
CACHE, PAGES = Path("data/cache"), Path("data/pages")
MAX_SUBPAGES = 6
LINK_SCORE = [(re.compile(r"happy|hh\b", re.I), 5), (re.compile(r"special|deal|late.?night", re.I), 3),
              (re.compile(r"drink|cocktail|wine|beer|bar.?menu|brunch", re.I), 2), (re.compile(r"menu", re.I), 1)]
DEAL_TEXT = re.compile(r"happy\s*-?\s*hour|reverse\s+happy|\bspecials?\b|half[- ]?(off|price)", re.I)


class _Page(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "td", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article"}

    def __init__(self):
        super().__init__()
        self.out, self.links, self._skip, self._a = [], [], 0, None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self._skip += 1
        if tag in self.BLOCK:
            self.out.append("\n")
        if tag == "a":
            self._a = [dict(attrs).get("href") or "", ""]

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self._skip:
            self._skip -= 1
        if tag == "a" and self._a:
            self.links.append(tuple(self._a))
            self._a = None

    def handle_data(self, data):
        if not self._skip:
            self.out.append(data)
            if self._a:
                self._a[1] += data


def html_to_text(html: str) -> tuple[str, list[tuple[str, str]]]:
    p = _Page()
    p.feed(html)
    lines = (" ".join(l.split()) for l in "".join(p.out).splitlines())
    return "\n".join(l for l in lines if l), p.links


def pdf_to_text(raw: bytes) -> str:
    from pypdf import PdfReader
    try:
        return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(raw)).pages[:20])
    except Exception:
        return ""


def site_key(url: str) -> str:
    return re.sub(r"^www\.", "", urlparse(url).netloc.lower())


async def get_cached(client, url, refresh=False):
    """-> (status, content_type, bytes). Cached on disk by URL hash."""
    h = hashlib.sha1(url.encode()).hexdigest()
    body, meta = CACHE / h, CACHE / f"{h}.json"
    if meta.exists() and not refresh:
        m = json.loads(meta.read_text())
        return m["status"], m["ctype"], body.read_bytes() if body.exists() else b""
    try:
        r = await client.get(url)
        status, ctype, raw = r.status_code, r.headers.get("content-type", ""), r.content[:8_000_000]
    except (httpx.HTTPError, ValueError) as e:
        status, ctype, raw = 0, type(e).__name__, b""
    body.write_bytes(raw)
    meta.write_text(json.dumps({"url": url, "status": status, "ctype": ctype}))
    return status, ctype, raw


def to_text(ctype, raw, url):
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        return pdf_to_text(raw), []
    return html_to_text(raw.decode("utf-8", "replace"))


async def crawl_site(client, home, refresh, stats):
    key = site_key(home)
    status, ctype, raw = await get_cached(client, f"{urlparse(home).scheme}://{urlparse(home).netloc}/robots.txt", refresh)
    robots = RobotFileParser()
    robots.parse(raw.decode("utf-8", "replace").splitlines() if status == 200 else [])

    if not robots.can_fetch(UA, home):
        stats["robots_disallowed"] += 1
        return
    status, ctype, raw = await get_cached(client, home, refresh)
    if status != 200:
        stats[f"home_{status}" if status else f"home_{ctype}"] += 1  # ConnectError is mostly dead domains = closed
        return
    text, links = to_text(ctype, raw, home)
    pages = [{"url": home, "text": text}]

    scored = {}
    for href, anchor in links:
        url = urljoin(home, href.strip()).split("#")[0]
        if not url.startswith("http") or url == home:
            continue
        same_site = site_key(url).endswith(key) or key.endswith(site_key(url))
        is_pdf = url.lower().split("?")[0].endswith(".pdf")
        if not (same_site or is_pdf):
            continue
        score = sum(w for rx, w in LINK_SCORE if rx.search(href) or rx.search(anchor))
        if score:
            scored[url] = max(score, scored.get(url, 0))
    for url in sorted(scored, key=scored.get, reverse=True)[:MAX_SUBPAGES]:
        if not robots.can_fetch(UA, url):
            continue
        await asyncio.sleep(0.5)  # be polite to small restaurant sites
        status, ctype, raw = await get_cached(client, url, refresh)
        if status == 200:
            pages.append({"url": url, "text": to_text(ctype, raw, url)[0]})

    stats["fetched"] += 1
    if sum(len(p["text"]) for p in pages) < 300:
        stats["thin_js_or_image"] += 1
    if any(DEAL_TEXT.search(p["text"]) for p in pages):
        stats["has_deal_text"] += 1
    (PAGES / f"{key}.json").write_text(json.dumps(pages))


async def main():
    args = sys.argv[1:]
    venues = [v for v in json.load(open("data/venues.json")) if v["website"]]
    if "--only" in args:
        wanted = {n.strip().lower() for n in args[args.index("--only") + 1].split(",")}
        venues = [v for v in venues if v["name"].lower() in wanted]
    homes = list(dict.fromkeys(w if w.startswith("http") else f"https://{w}" for w in (v["website"] for v in venues)))  # chains share one site
    homes = list({site_key(h): h for h in homes}.values())
    if "--limit" in args:
        homes = homes[: int(args[args.index("--limit") + 1])]
    CACHE.mkdir(parents=True, exist_ok=True)
    PAGES.mkdir(parents=True, exist_ok=True)

    stats, sem = Counter(sites=len(homes)), asyncio.Semaphore(16)
    async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=20, follow_redirects=True) as client:
        async def one(h):
            async with sem:
                try:
                    await crawl_site(client, h, "--refresh" in args, stats)
                except Exception as e:  # one broken site must not kill the run
                    stats["crashed"] += 1
                    print(f"  crash {h}: {type(e).__name__}: {e}", file=sys.stderr)
        await asyncio.gather(*(one(h) for h in homes))
    print(dict(stats))


if __name__ == "__main__":
    asyncio.run(main())
