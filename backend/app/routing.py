"""Travel times: OSRM (free-flow road times) + a Belgrade time-of-day factor.

OSRM knows the road network (one-way streets, bridges, turn restrictions) but
not traffic. Traffic enters in two places:
  * `traffic_factor(t)` - a rush-hour profile for Belgrade. It is a hand-made
    estimate and is labelled as such everywhere in the UI.
  * the engine's observed-speed ratio - how much slower than planned the
    courier actually moves right now (see engine.py).
A live traffic provider (TomTom / HERE / Google) can be plugged in later by
implementing the same `table()` / `route_legs()` interface.
"""

import hashlib
import json
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx

from .geocode import haversine_m

USER_AGENT = "dostava-bg/0.1 (+https://github.com/Aleksandar1567)"

# minute-of-day -> slowdown versus OSRM free-flow, Belgrade working day.
# Hand-tuned guess: morning peak, midday, afternoon peak (bridges, Gazela,
# Autokomanda). Replace with measured data once trips are being logged.
WEEKDAY_PROFILE = [
    (0, 1.05), (6 * 60, 1.15), (7 * 60, 1.45), (9 * 60 + 30, 1.25),
    (14 * 60, 1.35), (15 * 60, 1.55), (18 * 60 + 30, 1.30), (20 * 60, 1.15),
    (22 * 60, 1.05),
]


def traffic_factor(t: datetime, enabled: bool = True) -> float:
    if not enabled:
        return 1.0
    m = t.hour * 60 + t.minute
    f = WEEKDAY_PROFILE[0][1]
    for start, val in WEEKDAY_PROFILE:
        if m >= start:
            f = val
    if t.weekday() == 5:      # Saturday: half the extra delay
        f = 1 + (f - 1) * 0.5
    elif t.weekday() == 6:    # Sunday
        f = 1 + (f - 1) * 0.25
    return round(f, 3)


def decode_polyline6(s: str):
    coords, lat, lon, i = [], 0, 0, 0
    while i < len(s):
        vals = []
        for _ in range(2):
            shift = result = 0
            while True:
                b = ord(s[i]) - 63
                i += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            vals.append(~(result >> 1) if result & 1 else result >> 1)
        lat += vals[0]
        lon += vals[1]
        coords.append((lat / 1e6, lon / 1e6))
    return coords


class OsrmRouter:
    """Small OSRM HTTP client with chunking, throttling and an on-disk cache.

    The public demo server (router.project-osrm.org) is fine for testing but
    allows ~1 request/second and at most 100x100 table cells per request.
    For real use run OSRM locally (see README: docker + serbia-latest.osm.pbf)
    and set OSRM_URL.
    """

    def __init__(self, base_url: str, cache_dir: Path, max_cells: int = 10000,
                 min_interval_s: float = 1.0):
        self.base = base_url.rstrip("/")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_cells = max_cells
        self.min_interval = min_interval_s
        self._last = 0.0
        self._lock = threading.Lock()
        self.client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30)
        self.last_error = ""

    # -- plumbing -------------------------------------------------------------
    def _get(self, url: str) -> dict:
        with self._lock:
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            try:
                r = self.client.get(url)
            finally:
                self._last = time.monotonic()
        data = r.json()
        if r.status_code != 200 or data.get("code") != "Ok":
            raise RuntimeError(f"OSRM {r.status_code}: {data.get('code')} {data.get('message', '')}")
        return data

    def _cache(self, kind: str, key_obj):
        key = hashlib.sha1(json.dumps(key_obj).encode()).hexdigest()[:20]
        return self.cache_dir / f"{kind}_{key}.json"

    @staticmethod
    def _fmt(points):
        return ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in points)

    # -- public ----------------------------------------------------------------
    def table(self, points, sources=None, use_cache=True):
        """Durations [s] and distances [m] for sources x all points.

        Returns (durations, distances, source_label). Falls back to a
        straight-line estimate if OSRM is unreachable, so the app keeps working.
        """
        pts = [(round(a, 6), round(b, 6)) for a, b in points]
        src = list(range(len(pts))) if sources is None else list(sources)
        cache = self._cache("table", [pts, src])
        if use_cache and cache.exists():
            d = json.loads(cache.read_text())
            return d["dur"], d["dist"], "osrm"
        try:
            dur, dist = [], []
            per_req = max(1, self.max_cells // max(1, len(pts)))
            for i in range(0, len(src), per_req):
                chunk = src[i:i + per_req]
                url = (f"{self.base}/table/v1/driving/{self._fmt(pts)}"
                       f"?annotations=duration,distance&sources={';'.join(map(str, chunk))}")
                data = self._get(url)
                dur += data["durations"]
                dist += data["distances"]
            # unreachable pairs come back as null
            dur = [[x if x is not None else 3 * 3600 for x in row] for row in dur]
            dist = [[x if x is not None else 100000 for x in row] for row in dist]
            if use_cache:
                cache.write_text(json.dumps({"dur": dur, "dist": dist}))
            self.last_error = ""
            return dur, dist, "osrm"
        except Exception as e:  # network, rate limit, server down
            self.last_error = str(e)
            dur, dist = self.estimate(pts, src)
            return dur, dist, "procena"

    @staticmethod
    def estimate(points, sources):
        """Straight line x 1.35 at 28 km/h: rough city driving without OSRM."""
        dur, dist = [], []
        for i in sources:
            drow, srow = [], []
            for j in range(len(points)):
                m = haversine_m(*points[i], *points[j]) * 1.35
                srow.append(m)
                drow.append(m / (28 / 3.6))
            dur.append(drow)
            dist.append(srow)
        return dur, dist

    def route_legs(self, points):
        """Road geometry for each consecutive pair: list of [(lat, lon), ...]."""
        pts = [(round(a, 6), round(b, 6)) for a, b in points]
        if len(pts) < 2:
            return []
        cache = self._cache("route", pts)
        if cache.exists():
            return [[tuple(p) for p in leg] for leg in json.loads(cache.read_text())]
        legs = []
        try:
            step = 80
            for i in range(0, len(pts) - 1, step):
                chunk = pts[i:i + step + 1]
                url = (f"{self.base}/route/v1/driving/{self._fmt(chunk)}"
                       "?overview=false&steps=true&geometries=polyline6")
                data = self._get(url)
                for leg in data["routes"][0]["legs"]:
                    line = []
                    for st in leg["steps"]:
                        seg = decode_polyline6(st["geometry"])
                        line += seg if not line else seg[1:]
                    legs.append(line)
            cache.write_text(json.dumps(legs))
            self.last_error = ""
            return legs
        except Exception as e:
            self.last_error = str(e)
            return [[pts[i], pts[i + 1]] for i in range(len(pts) - 1)]


def point_along(line, frac: float):
    """Point at `frac` (0..1) of the length of a polyline."""
    if not line:
        return None
    if len(line) == 1 or frac <= 0:
        return line[0]
    seg = [haversine_m(*line[i], *line[i + 1]) for i in range(len(line) - 1)]
    total = sum(seg)
    if total == 0 or frac >= 1:
        return line[-1]
    goal = total * frac
    for i, s in enumerate(seg):
        if goal <= s:
            f = goal / s if s else 0
            return (line[i][0] + (line[i + 1][0] - line[i][0]) * f,
                    line[i][1] + (line[i + 1][1] - line[i][1]) * f)
        goal -= s
    return line[-1]
