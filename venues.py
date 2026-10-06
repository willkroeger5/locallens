# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb>=1.1"]
# ///
"""Step 1: build data/venues.json — every restaurant/bar/cafe in the SMU area.

Sources (in merge priority): Overture Maps Places (open, CDLA/ODbL), then
OpenStreetMap via Overpass for anything Overture lacks. Seed CSVs from the old
LocalLens are only used to measure recall (their coordinates aren't trustworthy).

    uv run venues.py            # re-pull both sources
    uv run venues.py --cached   # rebuild from data/*_raw.json
"""

import csv
import glob
import json
import math
import re
import subprocess
import sys
import unicodedata

OVERTURE_RELEASE = "2026-09-23.1"  # bump from https://docs.overturemaps.org/release-calendar/
S, W, N, E = 32.785, -96.815, 32.870, -96.755
CATEGORIES = {"restaurant", "bar", "casual_eatery", "coffee_shop", "cafe", "lounge",
              "brewery", "winery", "nightlife_venue"}
NEIGHBORHOODS = {  # ponytail: nearest-centroid tagging; swap for polygons if borders get argued about
    "Uptown": (32.800, -96.801),
    "Oak Lawn": (32.811, -96.810),
    "Knox-Henderson": (32.820, -96.786),
    "Lower Greenville": (32.813, -96.770),
    "Upper Greenville": (32.862, -96.769),
    "Mockingbird Station": (32.837, -96.774),
    "Snider Plaza / UP": (32.849, -96.785),
    "Highland Park Village": (32.835, -96.806),
}
SKIP_SITES = re.compile(r"facebook\.com|instagram\.com|yelp\.com|tripadvisor|doordash|ubereats|grubhub|linktr\.ee")


def norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"^the ", "", s.replace("&", "and"))
    return re.sub(r"[^a-z0-9]", "", s)


def meters(a, b):
    dy = (a[0] - b[0]) * 111_000
    dx = (a[1] - b[1]) * 111_000 * math.cos(math.radians(a[0]))
    return math.hypot(dx, dy)


def neighborhood(lat, lon):
    return min(NEIGHBORHOODS, key=lambda k: meters((lat, lon), NEIGHBORHOODS[k]))


def pull_overture():
    import duckdb
    c = duckdb.connect()
    c.sql("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial; SET s3_region='us-west-2';")
    path = f"s3://overturemaps-us-west-2/release/{OVERTURE_RELEASE}/theme=places/type=place/*"
    c.sql(f"""COPY (SELECT id, names.primary AS name, ST_Y(geometry) AS lat, ST_X(geometry) AS lon,
        websites, addresses[1].freeform AS address, basic_category, confidence, operating_status
        FROM read_parquet('{path}', hive_partitioning=1)
        WHERE bbox.xmin BETWEEN {W} AND {E} AND bbox.ymin BETWEEN {S} AND {N})
        TO 'data/overture_raw.json' (FORMAT JSON, ARRAY true)""")


def pull_osm():
    q = (f'[out:json][timeout:90];(nwr["amenity"~"^(restaurant|bar|pub|cafe|nightclub|biergarten)$"]'
         f"({S},{W},{N},{E}););out center tags;")
    for url in ("https://overpass.private.coffee/api/interpreter", "https://overpass-api.de/api/interpreter"):
        r = subprocess.run(["curl", "-s", "--max-time", "120", url, "--data-urlencode", f"data={q}",
                            "-A", "LocalLens/0.1 (student project)", "-o", "data/osm_raw.json"])
        if r.returncode == 0 and open("data/osm_raw.json").read(1) == "{":
            return
    print("warning: Overpass unavailable, using cached data/osm_raw.json", file=sys.stderr)


def build():
    venues = []
    for x in json.load(open("data/overture_raw.json")):
        if x["basic_category"] not in CATEGORIES or x["operating_status"] == "permanently_closed":
            continue
        if (x["confidence"] or 0) < 0.5 or not x["name"]:
            continue
        site = next((w for w in x["websites"] or [] if not SKIP_SITES.search(w)), None)
        venues.append({"id": x["id"], "name": x["name"], "lat": x["lat"], "lon": x["lon"],
                       "address": x["address"], "website": site, "category": x["basic_category"],
                       "source": "overture"})

    # dedupe Overture against itself (same name within 150m), then fold in OSM
    def find(name, lat, lon):
        n = norm(name)
        return next((v for v in venues if norm(v["name"]) == n and meters((lat, lon), (v["lat"], v["lon"])) < 150), None)

    deduped = []
    for v in venues:
        if not any(norm(d["name"]) == norm(v["name"]) and meters((v["lat"], v["lon"]), (d["lat"], d["lon"])) < 150
                   for d in deduped):
            deduped.append(v)
    venues = deduped

    for e in json.load(open("data/osm_raw.json"))["elements"]:
        t = e.get("tags", {})
        if not t.get("name"):
            continue
        lat, lon = e.get("lat") or e["center"]["lat"], e.get("lon") or e["center"]["lon"]
        site = t.get("website") or t.get("contact:website")
        if v := find(t["name"], lat, lon):
            v["website"] = v["website"] or site
        else:
            venues.append({"id": f"osm/{e['type']}/{e['id']}", "name": t["name"], "lat": lat, "lon": lon,
                           "address": " ".join(filter(None, [t.get("addr:housenumber"), t.get("addr:street")])) or None,
                           "website": site, "category": t["amenity"], "source": "osm"})

    for v in venues:
        v["neighborhood"] = neighborhood(v["lat"], v["lon"])
    json.dump(venues, open("data/venues.json", "w"), indent=1)

    # funnel + recall against the old hand-built list
    print(f"venues: {len(venues)}  with website: {sum(1 for v in venues if v['website'])}  "
          f"(overture {sum(v['source'] == 'overture' for v in venues)}, osm-only {sum(v['source'] == 'osm' for v in venues)})")
    names = [norm(v["name"]) for v in venues]
    seed = {r["venue_name"] for f in glob.glob("seed/*.csv") for r in csv.DictReader(open(f, encoding="utf-8-sig"))
            if S <= float(r["venue_lat"] or 0) <= N and W <= float(r["venue_lon"] or 0) <= E}
    missing = [s for s in seed if not any(norm(s)[:8] in n for n in names)]
    print(f"seed recall: {len(seed) - len(missing)}/{len(seed)}  missing: {missing}")


if __name__ == "__main__":
    if "--cached" not in sys.argv:
        pull_overture()
        pull_osm()
    build()
