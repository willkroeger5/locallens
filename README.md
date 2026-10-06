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

`docs/index.html` + `docs/time.js` + `docs/deals.json`. No build step, no framework.

- **Areas:** the rail along the top filters to one area. "All areas" groups the list by area, closest to SMU first, and highlights the area you're scrolling through. The areas and their centres are defined in `venues.py` (`NEIGHBORHOODS`). Anything more than 1.4 km from every centre is listed under "More nearby".
- **When:** On now / Today / a specific day / Any day. Everything uses Dallas time, whatever the viewer's timezone.
- **Filters sheet:** deal type, food or drinks, time of day, place type, prices listed, all-day deals. Sort by best right now, A–Z, or nearest (asks for location).
- **Desktop** (1000px and up) shows the list and the map side by side. On phones, the Map button switches views.
- **Shareable URLs** (all optional, and combinable):
  `?area=knox-henderson` · `?when=today|all` · `?day=fri` · `?q=tacos` · `?type=hh,special,late` · `?inc=food,drink` ·
  `?tod=lunch,aft,eve,late` · `?cat=bar,rest,cafe` · `?flags=price,allday` · `?sort=az`

Set `REPORT_URL` in `index.html` (e.g. a Google Form) to show a "report a wrong deal" link.

Data: © Overture Maps Foundation, © OpenStreetMap contributors (ODbL).
