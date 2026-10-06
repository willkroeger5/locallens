# /// script
# requires-python = ">=3.11"
# dependencies = ["anthropic>=0.96", "pydantic>=2", "httpx>=0.27", "pypdf>=5"]
# ///
"""Step 3: pull structured deals out of fetched pages with Claude, then build docs/deals.json.

Only pages that mention happy hour / specials are sent. Every deal must carry a
verbatim quote that is found in the fetched page, or it is dropped — a made-up
deal is worse than a missing one. Results are cached per site keyed on the page
content, so re-runs only pay for sites whose pages changed.

    uv run extract.py [--limit N] [--only site.com,site2.com] [--build-only]
"""

import asyncio
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Literal

import anthropic
from pydantic import BaseModel

from fetch import DEAL_TEXT, site_key

MODEL = "claude-opus-5-5"
PRICE_IN, PRICE_OUT = 4 / 1e6, 20 / 1e6  # $/token, Opus 5.5
PAGES, OUT = Path("data/pages"), Path("data/extracted")
WINDOW, MAX_CONTEXT = 1500, 24_000
# ponytail: sites shared by *different* businesses (e.g. a shopping-center site listing a third venue's deals);
# deals there can't be attributed. Upgrade: have the extractor tag which listed location each deal is for.
SHARED_SITE_SKIP = {"thevillagedallas.com"}
Day = Literal["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class Deal(BaseModel):
    kind: Literal["happy_hour", "reverse_happy_hour", "daily_special", "other"]
    title: str
    summary: str
    days: list[Day]
    start: str | None
    end: str | None
    until_close: bool
    all_day: bool
    food: bool
    drink: bool
    where: str | None
    evidence: str
    source_url: str


class Extraction(BaseModel):
    deals: list[Deal]
    notes: str | None


SYSTEM = """You extract recurring food and drink deals (happy hours, reverse happy hours, \
daily specials like "$2 tacos Tuesday") from restaurant website text, for a student deals map in Dallas.

Rules:
- Only include deals that are actually stated in the text. Never infer or fill in typical deals.
- `evidence` must be copied verbatim from the text: one contiguous span (20-300 chars, may cross line breaks) \
that includes the deal's name or heading (e.g. "Happy Hour") AND its days/times or prices, so a reader can see \
the times belong to the deal and are not the restaurant's opening hours. It is machine-checked against the page; \
paraphrased or stitched-together evidence gets the deal thrown out.
- `source_url` is the SOURCE url of the chunk the evidence came from.
- `summary` is shown to students as-is: short and concrete, prices included, e.g. \
"$5 drafts, $7 house wine, half-price apps". Never mention "the text", "the page", or what is missing. \
If no prices are given, name what's discounted if stated ("discounted drinks and bites"), else just "Happy hour menu".
- Times are 24h "HH:MM" in local time. "3-6pm" -> start "15:00", end "18:00". \
"9pm-close" -> start "21:00", end null, until_close true. Whole-day specials -> all_day true, start/end null.
- `days`: expand ranges ("Mon-Fri" -> Mon..Fri). If no days are stated for a happy hour, use all seven and say so in notes.
- `food` / `drink`: whether the deal includes discounted food / drinks.
- `where`: restrictions like "bar & patio only", "dine-in only", or null.
- Skip one-off events, holiday specials, catering, gift cards, and private-event packages.
- If the site serves several locations and a deal is explicitly for a location outside Dallas or not \
among the listed locations, skip it.
- If the text says deals exist but the details are in an image or a missing menu, return no deals and explain in `notes`.
"""


def norm(s: str) -> str:
    s = s.lower().replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"[‐-―]", "-", s)
    return re.sub(r"\s+", " ", s).strip()


def context_for(pages: list[dict]) -> str:
    """Keyword windows from every page that mentions deals, labelled with their URL."""
    chunks = []
    for p in pages:
        text = p["text"]
        spans = [(max(0, m.start() - WINDOW), min(len(text), m.end() + WINDOW)) for m in DEAL_TEXT.finditer(text)]
        merged = []
        for a, b in spans:
            if merged and a <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        if merged:
            chunks.append(f"=== SOURCE: {p['url']} ===\n" + "\n...\n".join(text[a:b] for a, b in merged))
    return "\n\n".join(chunks)[:MAX_CONTEXT]  # ponytail: hard cap; a venue with >24k chars of deal text is rare


def verify(deal: Deal, pages: list[dict]) -> bool:
    ev = norm(deal.evidence)
    if len(ev) < 15:
        return False
    texts = {p["url"]: norm(p["text"]) for p in pages}
    if deal.source_url in texts and ev in texts[deal.source_url]:
        return True
    hit = next((u for u, t in texts.items() if ev in t), None)  # right quote, wrong url label
    if hit:
        deal.source_url = hit
        return True
    return False


def valid_time(t):
    return t is None or bool(re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t))


async def extract_site(client, key, venues, stats, sem):
    pages = json.loads((PAGES / f"{key}.json").read_text())
    ctx = context_for(pages)
    if not ctx:
        stats["no_deal_text"] += 1
        return
    h = hashlib.sha1((MODEL + SYSTEM + ctx).encode()).hexdigest()[:16]  # prompt change = re-extract
    out = OUT / f"{key}.json"
    if out.exists() and json.loads(out.read_text()).get("hash") == h:
        stats["cached"] += 1
        return

    locations = "\n".join(f"- {v['name']} ({v.get('address') or v['neighborhood']})" for v in venues)
    async with sem:
        try:
            resp = await client.messages.parse(
                model=MODEL,
                max_tokens=16000,
                system=SYSTEM,
                output_config={"effort": "low"},
                messages=[{"role": "user", "content": f"Locations we care about:\n{locations}\n\nWebsite text:\n\n{ctx}"}],
                output_format=Extraction,
            )
        except anthropic.APIStatusError as e:
            stats["api_error"] += 1
            print(f"  api error {key}: {e.status_code}", file=sys.stderr)
            return
    stats["in_tokens"] += resp.usage.input_tokens
    stats["out_tokens"] += resp.usage.output_tokens
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        stats["refused_or_unparsed"] += 1
        return

    kept, dropped = [], []
    for d in resp.parsed_output.deals:
        ok = verify(d, pages) and d.days and valid_time(d.start) and valid_time(d.end)
        (kept if ok else dropped).append(d.model_dump())
    stats["deals_extracted"] += len(kept) + len(dropped)
    stats["deals_verified"] += len(kept)
    stats["sites_with_deals"] += bool(kept)
    out.write_text(json.dumps({"hash": h, "checked": date.today().isoformat(), "deals": kept,
                               "dropped": dropped, "notes": resp.parsed_output.notes}, indent=1))


GENERIC = {"avenue", "street", "dallas", "suite", "drive", "plaza", "village", "parkway", "boulevard", "north",
           "south", "texas", "central", "expressway"}


def addr_key(v):
    m = re.match(r"(\d{3,})\s+(?:[nsew]\.?\s+)?(\w+)", (v.get("address") or "").lower())
    return m and m.groups()


def branch_tokens(v):
    hood = re.sub(r"[^a-z]", "", v["neighborhood"].lower())
    words = re.findall(r"[a-z]{5,}|\d{3,}", (v.get("address") or "").lower())
    return {hood} | {w for w in words if w not in GENERIC}


def for_branch(deal, v, group):
    """Chain sites: a deal that names a branch (in `where` or its source page path) goes only to that branch."""
    if len(group) < 2:
        return True
    where = re.sub(r"\b(not|except|excluding)\b[^;,)]*", "", (deal.get("where") or "").lower())
    loc = re.sub(r"[^a-z0-9 ]", "", where) + " " + re.sub(r"[^a-z0-9/]", "", deal["source_url"].lower().split("//")[-1].partition("/")[2])
    named = [g for g in group if any(t in loc for t in branch_tokens(g))]
    return not named or v in named


def build():
    """Join venues + verified deals into the one file the web page loads."""
    by_site = defaultdict(list)
    for v in json.load(open("data/venues.json")):
        if v["website"]:
            by_site[site_key(v["website"] if v["website"].startswith("http") else f"https://{v['website']}")].append(v)
    rows, seen = [], []
    for f in OUT.glob("*.json"):
        x = json.loads(f.read_text())
        if not x["deals"] or f.stem in SHARED_SITE_SKIP:
            continue
        group = []
        for v in by_site.get(f.stem, []):
            # same place listed twice: same site and ~200m apart, or same name and ~700m apart.
            # Chain branches (km apart) stay separate.
            name = re.sub(r"[^a-z0-9]", "", v["name"].lower())
            near = lambda lat, lon, deg: abs(lat - v["lat"]) + abs(lon - v["lon"]) < deg
            if any(site == f.stem and (near(lat, lon, 0.002) or (n == name and near(lat, lon, 0.007)))
                   for site, n, lat, lon in seen) or any(addr_key(g) and addr_key(g) == addr_key(v) for g in group):
                continue
            seen.append((f.stem, name, v["lat"], v["lon"]))
            group.append(v)
        for v in group:
            deals = [d for d in x["deals"] if for_branch(d, v, group)]
            if deals:
                rows.append({k: v[k] for k in ("name", "lat", "lon", "address", "website", "neighborhood", "category")}
                            | {"checked": x["checked"], "deals": deals})
    rows.sort(key=lambda r: r["name"].lower())
    Path("docs").mkdir(exist_ok=True)
    Path("docs/deals.json").write_text(json.dumps({"updated": date.today().isoformat(), "venues": rows}))
    print(f"docs/deals.json: {len(rows)} venues, {sum(len(r['deals']) for r in rows)} deals")


async def main():
    args = sys.argv[1:]
    if "--build-only" not in args:
        by_site = defaultdict(list)
        for v in json.load(open("data/venues.json")):
            if v["website"]:
                by_site[site_key(v["website"] if v["website"].startswith("http") else f"https://{v['website']}")].append(v)
        keys = sorted(p.stem for p in PAGES.glob("*.json"))
        if "--only" in args:
            keys = [k for k in keys if k in args[args.index("--only") + 1].split(",")]
        if "--limit" in args:
            keys = keys[: int(args[args.index("--limit") + 1])]
        OUT.mkdir(parents=True, exist_ok=True)
        stats, sem = Counter(sites=len(keys)), asyncio.Semaphore(8)
        client = anthropic.AsyncAnthropic()
        await asyncio.gather(*(extract_site(client, k, by_site.get(k, []), stats, sem) for k in keys))
        cost = stats["in_tokens"] * PRICE_IN + stats["out_tokens"] * PRICE_OUT
        print(dict(stats), f"cost ~${cost:.2f}")
    build()


if __name__ == "__main__":
    asyncio.run(main())
