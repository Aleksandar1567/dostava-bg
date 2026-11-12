"""Build the local Belgrade address index from an OpenStreetMap extract.

Serbian OSM data carries the RGZ (Republički geodetski zavod) house-number
import (`ref:RS:kucni_broj`), so almost every building in Belgrade has a
street + house number. We read them once from the Geofabrik Serbia extract
into SQLite and geocode daily package lists locally: fast, free, offline,
building-level.

Usage (from backend/):
    .venv/bin/python tools/build_address_index.py               # downloads the extract if missing
    .venv/bin/python tools/build_address_index.py --pbf my.osm.pbf

Data © OpenStreetMap contributors, ODbL 1.0.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import httpx
import osmium

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.sqlite_compat import sqlite3  # noqa: E402
from app.textnorm import (MANUAL_ALIASES, fold, norm_housenumber, norm_street,  # noqa: E402
                          translit)

GEOFABRIK = "https://download.geofabrik.de/europe/serbia-latest.osm.pbf"
USER_AGENT = "dostava-bg/0.1 (+https://github.com/Aleksandar1567)"
# Belgrade + surroundings: Obrenovac, Batajnica, Surčin, Borča, Pančevo, Grocka, Ripanj.
DEFAULT_BBOX = (44.62, 20.15, 44.98, 20.75)  # south, west, north, east
BACKEND = Path(__file__).resolve().parents[1]
DB_PATH = BACKEND / "data" / "addresses.sqlite"
PBF_PATH = BACKEND / "data" / "osm" / "serbia-latest.osm.pbf"
ALIAS_KEYS = ("alt_name", "old_name", "loc_name", "short_name", "official_name",
              "alt_name:sr-Latn", "old_name:sr-Latn", "loc_name:sr-Latn")
PLACE_TYPES = {"city", "town", "village", "suburb", "quarter", "neighbourhood", "hamlet",
               "locality"}


def download(dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_suffix(".part")
    print(f"downloading {GEOFABRIK} ...", flush=True)
    with httpx.stream("GET", GEOFABRIK, headers={"User-Agent": USER_AGENT},
                      follow_redirects=True, timeout=120) as r:
        r.raise_for_status()
        with open(part, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    part.replace(dst)


def housenumbers(hn: str):
    """'12;14' -> 12, 14; '12-14' -> '12-14', 12, 14."""
    parts = [p.strip() for p in hn.replace(",", ";").split(";") if p.strip()]
    for p in list(parts):
        if "-" in p and all(x.strip().isdigit() for x in p.split("-", 1)):
            parts += [x.strip() for x in p.split("-", 1)]
    return parts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pbf", type=Path, default=PBF_PATH)
    ap.add_argument("--bbox", type=float, nargs=4, default=DEFAULT_BBOX,
                    metavar=("S", "W", "N", "E"))
    ap.add_argument("--db", type=Path, default=DB_PATH)
    args = ap.parse_args()
    s, w, n, e = args.bbox

    if not args.pbf.exists():
        download(args.pbf)

    t0 = time.time()
    rows: dict = {}
    places = []
    aliases = set()
    fp = (osmium.FileProcessor(str(args.pbf))
          .with_locations()
          .with_filter(osmium.filter.KeyFilter("addr:housenumber", "place", "highway")))
    for obj in fp:
        tags = obj.tags
        if obj.is_way() and "highway" in tags and "addr:housenumber" not in tags:
            # old / alternative / colloquial street names -> register name
            if "name" not in tags or not obj.nodes or not obj.nodes[0].location.valid():
                continue
            loc = obj.nodes[0].location
            if not (s <= loc.lat <= n and w <= loc.lon <= e):
                continue
            target = norm_street(tags["name"])
            for k in ALIAS_KEYS:
                for a in (tags.get(k) or "").split(";"):
                    an = norm_street(a) if a.strip() else ""
                    if an and an != target:
                        aliases.add((an, target, k))
            continue
        if obj.is_node():
            if not obj.location.valid():
                continue
            lat, lon = obj.location.lat, obj.location.lon
            key = f"n{obj.id}"
        elif obj.is_way():
            if "addr:housenumber" not in tags:
                continue
            pts = [(nd.location.lat, nd.location.lon) for nd in obj.nodes if nd.location.valid()]
            if not pts:
                continue
            if len(pts) > 1 and pts[0] == pts[-1]:
                pts = pts[:-1]
            lat = sum(p[0] for p in pts) / len(pts)
            lon = sum(p[1] for p in pts) / len(pts)
            key = f"w{obj.id}"
        else:
            continue
        if not (s <= lat <= n and w <= lon <= e):
            continue

        if obj.is_node() and tags.get("place") in PLACE_TYPES and "name" in tags:
            latn = tags.get("name:sr-Latn") or translit(tags["name"])
            names = {fold(latn), fold(translit(tags["name"]))}
            for alt_key in ("alt_name", "alt_name:sr-Latn", "old_name"):
                for a in (tags.get(alt_key) or "").split(";"):
                    if a.strip():
                        names.add(fold(translit(a)))
            for nm in names:
                places.append((latn, nm, tags["place"], lat, lon))

        hn = tags.get("addr:housenumber", "")
        street = tags.get("addr:street") or tags.get("addr:place") or ""
        if not hn or not street:
            continue
        for one in housenumbers(hn):
            k = key if one == hn else f"{key}:{one}"
            rows[k] = (key, translit(street), norm_street(street), translit(one),
                       norm_housenumber(one), tags.get("addr:postcode", ""),
                       translit(tags.get("addr:city", "")), round(lat, 7), round(lon, 7),
                       tags.get("ref:RS:kucni_broj", ""), 1 if "building" in tags else 0)
    print(f"read {args.pbf.name} in {time.time() - t0:.0f}s: {len(rows)} addresses, "
          f"{len(places)} place names, {len(aliases)} street aliases", flush=True)
    aliases |= {(a, t, "manual") for a, t in MANUAL_ALIASES.items()}

    args.db.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.db.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.executescript("""
        CREATE TABLE addr(osm TEXT, street TEXT, street_norm TEXT, hn TEXT, hn_norm TEXT,
                          postcode TEXT, city TEXT, lat REAL, lon REAL, rgz TEXT,
                          building INTEGER);
        CREATE TABLE place(name TEXT, name_norm TEXT, type TEXT, lat REAL, lon REAL);
        CREATE TABLE alias(alias_norm TEXT, street_norm TEXT, kind TEXT);
        CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
    """)
    db.executemany("INSERT INTO addr VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows.values())
    db.executemany("INSERT INTO place VALUES (?,?,?,?,?)", places)
    db.executemany("INSERT INTO alias VALUES (?,?,?)", sorted(aliases))
    db.executescript("""
        CREATE INDEX addr_street ON addr(street_norm, hn_norm);
        CREATE INDEX place_norm ON place(name_norm);
        CREATE INDEX alias_norm ON alias(alias_norm);
    """)
    db.executemany("INSERT INTO meta VALUES (?,?)", [
        ("bbox", json.dumps(args.bbox)),
        ("built", time.strftime("%Y-%m-%dT%H:%M:%S")),
        ("source", f"OpenStreetMap ({args.pbf.name}), ODbL 1.0"),
    ])
    db.commit()
    db.close()
    tmp.replace(args.db)
    n_streets = len({v[2] for v in rows.values()})
    print(f"done: {len(rows)} addresses, {n_streets} streets -> {args.db}")


if __name__ == "__main__":
    main()
