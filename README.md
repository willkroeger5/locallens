# LocalLens

Happy hours and food & drink deals near SMU, pulled from every restaurant's own website.

Live: https://willkroeger5.github.io/locallens/ (the `docs/` folder, served by GitHub Pages)

## How the data is built

```sh
uv run venues.py      # 1. every restaurant/bar/cafe in the area (Overture Maps + OpenStreetMap) -> data/venues.json
uv run fetch.py       # 2. fetch each venue's site: homepage + happy-hour/menu/specials pages + PDFs (cached in data/cache)
uv run extract.py     # 3. Claude extracts deals; each must quote the page verbatim or it's dropped -> docs/deals.json
node test_time.js     # open-now / past-midnight logic check
```

Re-running is cheap: fetches are cached on disk, and extraction only re-pays for sites whose deal text changed.
To refresh the data, run steps 2 and 3 with `--refresh` on fetch, then commit `docs/deals.json` and push.

- `venues.py --cached` rebuilds from the saved raw pulls.
- `fetch.py --only "Venue Name,Other Venue"` and `extract.py --only site.com` re-do single places.
- The area box and neighborhood centroids are at the top of `venues.py`.

## Page

`docs/index.html` + `docs/time.js` + `docs/deals.json`. No build step. "Happening now" uses Dallas time
regardless of the viewer's timezone. Share a view with `?when=today` or `?when=all`.
Set `REPORT_URL` in `index.html` (e.g. a Google Form) to show a "report a wrong deal" link.

Data: © Overture Maps Foundation, © OpenStreetMap contributors (ODbL).
