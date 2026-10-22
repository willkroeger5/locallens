# LocalLens

**Repo:** `~/Projects/Personal/LocalLens/` (this file is `STATUS.md` there, symlinked into the vault)
**Old version:** `~/Projects/Archive/LocalLens` (2025, React/Flask/Mapbox, 39 venues; left untouched, its CSVs are in `seed/`)
**Started (rebuild):** 2026-10-04

## Goal
A shareable link where SMU students (roommates, girlfriend's friends, and so on) can see which happy hours and food/drink deals are on right now near campus: Uptown, Knox-Henderson, Lower/Upper Greenville, Snider Plaza/UP, Mockingbird Station, Highland Park Village, and Oak Lawn. Deep Ellum and Bishop Arts are deliberately out of scope for now.

## Status
- **Pipeline built (2026-10-04):** `venues.py`, then `fetch.py`, then `extract.py`, which writes `docs/deals.json`. Commands are in the README.
- **Page v2 (2026-10-06):** area-first redesign. It has an area rail (filter to one area, or All, grouped with scroll highlighting), On now/Today/day picker/Any day, a filters sheet (type, food/drink, time of day, place, prices, sort including nearest), active-filter pills, the side-by-side map on desktop, and shareable URL params. See the README. Tested with Playwright on a frozen clock at 390px light/dark and 1280px, and in the New York timezone (counts match).
- **Areas redone (2026-10-06):** 13 areas plus "More nearby" (cutoff 1.4 km), ordered by distance from SMU. Added Preston Center, NorthPark, Victory Park, Downtown & Arts District, and Deep Ellum & East Dallas, which fixes mislabels such as Preston Center venues tagged as Snider Plaza.
- **Hosting:** LIVE since 2026-10-06 at https://willkroeger5.github.io/locallens/ (repo `willkroeger5/locallens`, public, Pages from `main` /docs). To update the data, run the pipeline, commit `docs/deals.json`, and push as willkroeger5.

## Coverage funnel (first full run, 2026-10-04)
- Venues in the area: 1,868 (1,789 from Overture, 79 from OSM only). 1,583 have a website.
- Unique sites: 1,312. Fetched: 912. Dead domains, probably closed: 231. 403/404: about 100. Blocked by robots.txt: 28.
- Sites whose pages mention deals: 381. Thin pages (JS-rendered or image menus): 133.
- Extraction: 381 deal-mentioning sites went to Claude. 124 had extractable deals, and all 263 extracted deals passed the verbatim-quote check. After joining to venues and removing duplicates: **136 venues, 289 deals** in `docs/deals.json` (143 happy hours, 171 daily specials, 7 reverse happy hours before dedupe).
- By area: Uptown 65, Lower Greenville 25, Oak Lawn 15, Upper Greenville 15, Knox-Henderson 14, Mockingbird 10, Snider/UP 8, HP Village 1 (before dedupe).
- Price check: 173 deal summaries list prices, and every price appears on that site's fetched page (0 failures).
- Chain sites: a deal that names a branch (in `where` or its source page path) is attached only to that branch, e.g. Bread Winners "Uptown only" and Taco Joint "not at Peak".
- Cost: about $15 for the first build, plus about $11 for the JS/image recovery and no-assumption re-extraction (2026-10-06) (two pilots, one run stopped for a prompt fix, then the full run at $9.55). Re-runs only pay for sites whose deal text changed.

## JS/image menu recovery (2026-10-06)
- `render.py` handled 272 target sites. These were thin JS pages, sites whose deals sat behind tabs, images or PDFs, and homepages that blocked plain fetching. It used headless Chrome and clicked happy-hour/specials tabs. It also ran OCR (Apple Vision) on deal-related menu images and scanned PDFs.
- Of those, about 125 rendered. Most failures are Cloudflare "Just a moment" challenges (deliberately not bypassed), dead or timed-out hosts, and robots.txt blocks.
- Recovered deals at Birdie's, The Saint, Velour, Jack & Harry's, Barley House, Lucky's, Eatzi's, Joey and KISS. I hand-checked the OCR deals against the source images (Velour, The Saint, Barley House) and found no misreads.
- Still missing:
  - Dakota's: Cloudflare.
  - Front Room: site down.
  - Las Palmas, Casa Brasa and Hero: their happy-hour menu is in a format the renderer didn't capture.
  - Sixty Vines and Fish City: their sites state no details.
- Keenable.ai (suggested) was tested on the misses. Its fetch doesn't click tabs or read images, so it wasn't used.
- **No more assumed days or times.** The prompt now forbids guessing. Deals whose menu gives hours but no days show "Days not listed" and never show as "on now". That re-extraction cost $10.23. The pilot and targeted re-runs added about $0.93.
- Venues whose site says they're closed are dropped (Quarter Bar: remodel).
- Fixed a negated chain restriction ("not available at the Peak location"). Also added a stale-branch list (`STALE_ADDRESSES`) and dropped unaddressed duplicate rows.
- Result: **144 venues, 315 deals** (was 136 / 289). The price check passes on all 194 deals with prices.

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
