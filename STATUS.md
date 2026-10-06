# LocalLens

**Repo:** `~/Projects/Personal/LocalLens/` (this file is `STATUS.md` there, symlinked into the vault)
**Old version:** `~/Projects/Archive/LocalLens` (2025, React/Flask/Mapbox, 39 venues; left untouched, its CSVs are in `seed/`)
**Started (rebuild):** 2026-10-04

## Goal
A shareable link where SMU students (roommates, girlfriend's friends, and so on) can see which happy hours and food/drink deals are on right now near campus: Uptown, Knox-Henderson, Lower/Upper Greenville, Snider Plaza/UP, Mockingbird Station, Highland Park Village, and Oak Lawn. Deep Ellum and Bishop Arts are deliberately out of scope for now.

## Status
- **Pipeline built (2026-10-04):** `venues.py`, then `fetch.py`, then `extract.py`, which writes `docs/deals.json`. Commands are in the README.
- **Page built:** `docs/index.html` (one static file plus `time.js`). It has Now/Today/All week views, food/drink and area filters, search, a Leaflet map, dark mode, and Dallas-time logic. The time logic is checked by `test_time.js`.
- **Hosting:** GitHub Pages on the personal account `willkroeger5`. **Not yet published.** Waiting on William's OK to create the public repo.

## Coverage funnel (first full run, 2026-10-04)
- Venues in the area: 1,868 (1,789 from Overture, 79 from OSM only). 1,583 have a website.
- Unique sites: 1,312. Fetched: 912. Dead domains, probably closed: 231. 403/404: about 100. Blocked by robots.txt: 28.
- Sites whose pages mention deals: 381. Thin pages (JS-rendered or image menus): 133.
- Extraction: 381 deal-mentioning sites went to Claude. 124 had extractable deals, and all 263 extracted deals passed the verbatim-quote check. After joining to venues and removing duplicates: **136 venues, 289 deals** in `docs/deals.json` (143 happy hours, 171 daily specials, 7 reverse happy hours before dedupe).
- By area: Uptown 65, Lower Greenville 25, Oak Lawn 15, Upper Greenville 15, Knox-Henderson 14, Mockingbird 10, Snider/UP 8, HP Village 1 (before dedupe).
- Price check: 173 deal summaries list prices, and every price appears on that site's fetched page (0 failures).
- Chain sites: a deal that names a branch (in `where` or its source page path) is attached only to that branch, e.g. Bread Winners "Uptown only" and Taco Joint "not at Peak".
- Cost: about $15 total for the first build (two pilots, one run stopped for a prompt fix, then the full run at $9.55). Re-runs only pay for sites whose deal text changed.

## Decisions
- **Overture Maps is the venue source, not OSM.** OSM found 3 of 18 known venues; Overture found 14 of 15. Google Places and Yelp are out because their terms of service forbid storing and redisplaying their data.
- **Every deal must carry a verbatim quote** (20+ chars covering the heading plus the times) that's found in the fetched page, or it's dropped. The goal is no made-up deals.
- **Model:** Claude Opus 5.5 at low effort through `messages.parse` structured output. The pilot cost about $1 per 60 sites. Results are cached per site, keyed on the page text plus the prompt.
- **I skipped server-side refusal fallbacks** on purpose. A refused venue is simply skipped and logged, and refusals on restaurant menus are very unlikely.

## Known gaps / next
- **Happy-hour menus behind JS tabs or images.** In the pilot this hit about 10 of 39 deal-mentioning sites (Birdie's, Dakota's, Las Palmas, Jack & Harry's, Velour, The Saint, ...). Fix options: headless rendering (Playwright) for those sites, and Claude vision on menu images.
- **Roundup articles** (D Magazine, Dallasites101, Dallas Observer) as a second source for those same venues. When an article and the venue's site conflict, the site wins.
- **"Report a wrong deal":** set `REPORT_URL` (a Google Form) in `docs/index.html`.
- **Freshness:** re-run fetch with `--refresh`, then extract, roughly monthly. Could be put on a schedule later.
