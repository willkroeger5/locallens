# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright>=1.45", "httpx>=0.27", "pypdf>=5", "pypdfium2>=4", "ocrmac>=1.0"]
# ///
"""Step 2b: recover deals that plain fetching misses — JavaScript menus, menu tabs, and menus posted as images.

For a bounded set of sites (thin JS pages, sites whose deals live in a tab/image/PDF, homepages that
blocked the plain fetch) this opens the site in headless Chrome, clicks happy-hour / specials tabs, and
OCRs deal-related menu images and scanned PDFs with Apple's Vision framework (macOS only).

Writes data/rendered/<site>.json = [{url, text, via, flagged}], kept separate from data/pages so a
fetch --refresh never wipes it. `flagged` marks text that came from a deal-specific tab/page/image;
extract.py sends flagged text whole instead of keyword windows (OCR'd menus often omit "happy hour").

    uv run render.py                 # default target set (see targets())
    uv run render.py --only a.com,b.com
    uv run render.py --refresh       # re-render sites that already have output (default skips them)
"""

import asyncio
import hashlib
import io
import json
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path
from urllib.robotparser import RobotFileParser

import httpx

from fetch import CACHE, LINK_SCORE, UA, pdf_to_text, site_key

OUT = Path("data/rendered")
DEALISH = re.compile(r"happy|\bhh\b|special|deal|drink|cocktail|late.?night|bar.?menu|bites", re.I)
CHALLENGE = re.compile(r"just a moment|attention required|access denied|verify you are human|captcha", re.I)
MAX_LINKS, MAX_TABS, MAX_IMAGES, MIN_IMG_W, FLAG_CAP = 5, 4, 8, 400, 8000


# ---------- target selection ----------
def _meta(url):
    m = CACHE / f"{hashlib.sha1(url.encode()).hexdigest()}.json"
    return json.loads(m.read_text()) if m.exists() else None


def homes():
    vs = [v for v in json.load(open("data/venues.json")) if v["website"]]
    hs = [w if w.startswith("http") else f"https://{w}" for w in (v["website"] for v in vs)]
    return {site_key(h): h for h in dict.fromkeys(hs)}


def targets():
    """Thin JS pages + deals-behind-tab/image/PDF notes + homepages blocked for the plain fetcher."""
    out = set()
    for f in Path("data/pages").glob("*.json"):
        if sum(len(p["text"]) for p in json.loads(f.read_text())) < 300:
            out.add(f.stem)
    for f in Path("data/extracted").glob("*.json"):
        x = json.loads(f.read_text())
        if not x["deals"] and re.search(r"image|graphic|photo|tab\b|tabs|javascript|not (included|present|shown|provided)|"
                                         r"no details|separate|linked|download|pdf", (x["notes"] or "").lower()):
            out.add(f.stem)
    for key, home in homes().items():
        m = _meta(home)
        if m and (m["status"] in (403, 429, 500, 502, 522, 525) or m["ctype"] in ("ConnectTimeout", "RemoteProtocolError", "ReadError")):
            out.add(key)
    return sorted(out)


def allowed(home):
    m = _meta(f"{home.split('/')[0]}//{home.split('/')[2]}/robots.txt")
    rp = RobotFileParser()
    if m and m["status"] == 200:
        body = CACHE / hashlib.sha1(m["url"].encode()).hexdigest()
        rp.parse(body.read_bytes().decode("utf-8", "replace").splitlines())
    else:
        rp.parse([])
    return rp.can_fetch(UA, home)


# ---------- OCR ----------
def ocr_image(raw: bytes) -> str:
    """Apple Vision OCR, lines in reading order (top-to-bottom, then left-to-right)."""
    from ocrmac import ocrmac
    with tempfile.NamedTemporaryFile(suffix=".img") as f:
        f.write(raw)
        f.flush()
        try:
            res = ocrmac.OCR(f.name, recognition_level="accurate").recognize()
        except Exception:
            return ""
    # Vision boxes are (x, y, w, h) with origin bottom-left; bucket rows so a heading stays next to its times
    res.sort(key=lambda r: (-round(r[2][1] + r[2][3], 2), r[2][0]))
    return "\n".join(t for t, conf, box in res if conf > 0.3)


def ocr_pdf(raw: bytes) -> str:
    import pypdfium2 as pdfium
    try:
        pdf = pdfium.PdfDocument(raw)
    except Exception:
        return ""
    out = []
    for i in range(min(len(pdf), 4)):
        buf = io.BytesIO()
        pdf[i].render(scale=2).to_pil().save(buf, format="PNG")
        out.append(ocr_image(buf.getvalue()))
    return "\n".join(out)


# ---------- browser ----------
JS_TEXT = "() => document.body ? document.body.innerText : ''"
JS_LINKS = """() => [...document.querySelectorAll('a[href]')].flatMap(a => {  // getAttribute: SVG <a> has no string .href
  try { return [[new URL(a.getAttribute('href'), location.href).href, (a.innerText || a.textContent || '').trim()]]; } catch { return []; }
})"""
JS_IMAGES = """() => [...document.querySelectorAll('img')].map(i => {
  const srcset = (i.getAttribute('srcset') || i.getAttribute('data-srcset') || '').split(',').map(s => s.trim().split(' ')[0]).filter(Boolean);
  const link = i.closest('a');
  return { src: i.currentSrc || srcset.pop() || i.getAttribute('data-src') || i.src, w: i.naturalWidth || i.width,
           alt: (i.alt || '') + ' ' + (i.title || '') + ' ' + (link ? link.innerText + ' ' + link.href : '') };
})"""


async def page_text(page):
    texts = []
    for fr in page.frames:  # menu widgets (Toast, SinglePlatform, ...) often live in iframes
        try:
            texts.append(await fr.evaluate(JS_TEXT))
        except Exception:
            pass
    return "\n".join(t for t in texts if t.strip())


async def settle(page):
    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    try:  # scroll to trigger lazy-loaded images/sections
        await page.evaluate("async () => { for (let y = 0; y < document.body.scrollHeight; y += 700) { scrollTo(0, y); await new Promise(r => setTimeout(r, 120)); } scrollTo(0, 0); }")
    except Exception:
        pass


async def render_site(browser, key, home, stats):
    if not allowed(home):
        stats["robots_disallowed"] += 1
        return
    ctx = await browser.new_context(user_agent=f"{UA} HeadlessChrome", viewport={"width": 1280, "height": 1600}, accept_downloads=True)
    page = await ctx.new_page()
    entries, images, seen_img, tab_links = [], [], set(), []

    async def visit(url, flagged):
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        except Exception:
            return False
        if resp and resp.status >= 400:
            return False
        await settle(page)
        if CHALLENGE.search(await page.title() or ""):
            stats["bot_wall_skipped"] += 1
            return False
        entries.append({"url": page.url, "text": await page_text(page), "via": "render", "flagged": flagged})
        # deal-ish tabs/buttons on this page
        loc = page.locator("a, button, [role=tab]").filter(has_text=re.compile(r"happy|special|drink|late night|hh\b", re.I))
        n = min(await loc.count(), MAX_TABS)
        for i in range(n):
            el = loc.nth(i)
            try:
                label = (await el.inner_text()).strip()[:40]
                href = await el.get_attribute("href")
                if href and not href.startswith("#") and not href.startswith("javascript"):
                    tab_links.append(await el.evaluate("e => e.href"))  # a tab that is really a link: visit it below
                    continue
                before = page.url
                await el.click(timeout=3000)
                await page.wait_for_timeout(1500)
                entries.append({"url": f"{page.url}#tab={label}", "text": await page_text(page), "via": "render-tab", "flagged": True})
                stats["tabs_clicked"] += 1
                await collect_images(True)  # the tab may reveal a menu image
                if page.url.split("#")[0] != before.split("#")[0]:
                    await page.go_back(timeout=10000)
            except Exception:
                pass
        await collect_images(flagged)
        return True

    async def collect_images(flagged):
        try:
            for im in await page.evaluate(JS_IMAGES):
                src = im["src"] or ""
                if not src.startswith("http") or src in seen_img or (im["w"] or 0) < MIN_IMG_W:
                    continue
                if flagged or DEALISH.search(src + " " + im["alt"]):
                    seen_img.add(src)
                    images.append(src)
        except Exception:
            pass

    try:
        if not await visit(home, False):
            stats["home_failed"] += 1
            return
        links = {}
        for href, text in await page.evaluate(JS_LINKS):
            href = href.split("#")[0]
            if not href.startswith("http") or href.rstrip("/") == home.rstrip("/"):
                continue
            same = site_key(href).endswith(key) or key.endswith(site_key(href))
            if not (same or href.lower().split("?")[0].endswith(".pdf")):
                continue
            score = sum(w for rx, w in LINK_SCORE if rx.search(href) or rx.search(text))
            if score:
                links[href] = max(score, links.get(href, 0))
        for href in sorted(links, key=links.get, reverse=True)[:MAX_LINKS]:
            flagged = bool(DEALISH.search(href))
            if href.lower().split("?")[0].endswith(".pdf"):
                async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=30, follow_redirects=True) as c:
                    try:
                        raw = (await c.get(href)).content
                    except httpx.HTTPError:
                        continue
                text = pdf_to_text(raw)
                via = "pdf"
                if len(text.strip()) < 100:  # scanned PDF
                    text, via = await asyncio.to_thread(ocr_pdf, raw), "pdf-ocr"
                    stats["pdfs_ocrd"] += 1
                entries.append({"url": href, "text": text, "via": via, "flagged": flagged})
            elif allowed(href):
                await visit(href, flagged)
        visited = {e["url"].split("#")[0] for e in entries}
        for href in list(dict.fromkeys(tab_links))[:3]:
            if href.split("#")[0] not in visited and href.startswith("http") and allowed(href):
                await visit(href, True)
    finally:
        await ctx.close()

    async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=30, follow_redirects=True) as c:
        for src in images[:MAX_IMAGES]:
            url = src + ("&" if "?" in src else "?") + "format=1500w" if "squarespace-cdn" in src else src
            try:
                r = await c.get(url)
                if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image"):
                    continue
            except httpx.HTTPError:
                continue
            text = await asyncio.to_thread(ocr_image, r.content)
            stats["images_ocrd"] += 1
            if len(text) > 40:
                entries.append({"url": src, "text": text, "via": "ocr", "flagged": True})

    stats["rendered"] += 1
    from fetch import DEAL_TEXT
    if any(e["flagged"] and len(e["text"]) > 40 or DEAL_TEXT.search(e["text"]) for e in entries):
        stats["has_deal_text"] += 1
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{key}.json").write_text(json.dumps(entries))


async def main():
    from playwright.async_api import async_playwright
    args = sys.argv[1:]
    keys = args[args.index("--only") + 1].split(",") if "--only" in args else targets()
    hs = homes()
    keys = [k for k in keys if k in hs and ("--refresh" in args or "--only" in args or not (OUT / f"{k}.json").exists())]
    stats, sem = Counter(targets=len(keys)), asyncio.Semaphore(4)
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True)

        async def one(k):
            async with sem:
                try:
                    await asyncio.wait_for(render_site(browser, k, hs[k], stats), timeout=180)
                except Exception as e:  # one broken site must not kill the run
                    stats["crashed"] += 1
                    print(f"  crash {k}: {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
        await asyncio.gather(*(one(k) for k in keys))
        await browser.close()
    print(dict(stats))


if __name__ == "__main__":
    asyncio.run(main())
