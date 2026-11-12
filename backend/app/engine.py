"""Day state for one courier: packages -> stops -> plan -> live ETAs.

Lifecycle
  1. packages come in (QR scan, CSV, manual) and are geocoded to a building
  2. packages for the same building with compatible windows become one stop
     (the courier calls, the customer comes out: `service_min` per stop)
  3. plan(): OR-Tools picks the order, customers get a promised window
  4. while driving: refresh() recomputes ETAs from where the courier is now,
     using the rush-hour profile plus the speed actually observed on the
     current leg; if a customer window is about to be missed, a stop fails
     or a package is added, the rest of the route is re-optimized
  5. customers are notified when their window moves and ~5 min before arrival

The clock is either real (field use, GPS from the phone) or simulated (sim_*),
which moves a virtual courier along the road geometry with random traffic and
customers that sometimes do not come out.
"""

import csv
import hashlib
import io
import json
import math
import random
import secrets
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .geocode import Geocoder, haversine_m
from .optimizer import Problem, solve
from .routing import OsrmRouter, point_along, traffic_factor

TZ = ZoneInfo("Europe/Belgrade")

DEFAULT_SETTINGS = {
    "depot": {"lat": 44.8682, "lon": 20.3579, "label": "Magacin (test), Batajnički drum"},
    "shift_start": "08:00",
    "shift_end": "20:00",
    "service_min": 5,            # courier calls, customer comes out in front of the building
    "return_to_depot": False,
    "traffic_profile": True,     # Belgrade rush-hour estimate on top of OSRM
    "notify_before_min": 5,      # "izađite ispred zgrade" message
    "promise_width_min": 60,     # width of the window promised to the customer
    "late_threshold_min": 10,    # predicted lateness that triggers re-optimization
    "retry_after_min": 60,       # customer did not come out -> try again later
    "solver_seconds": 5,
}
REPLAN_COOLDOWN_S = 10 * 60
GPS_ROW_INTERVAL_S = 60
ARRIVE_RADIUS_M = 80


def iso(dt: datetime | None):
    return dt.isoformat(timespec="seconds") if dt else None


def parse_dt(s):
    return datetime.fromisoformat(s) if s else None


def hhmm(dt: datetime | None):
    return dt.strftime("%H:%M") if dt else ""


def _clean_hhmm(v):
    v = (v or "").strip().replace(".", ":").replace("h", "")
    if not v:
        return None
    if v.isdigit():
        v = f"{int(v):02d}:00"
    h, _, m = v.partition(":")
    return f"{int(h):02d}:{int(m or 0):02d}"


def _round15(dt: datetime, up: bool):
    q = 15 * 60
    ts = dt.timestamp()
    ts = math.ceil(ts / q) * q if up else math.floor(ts / q) * q
    return datetime.fromtimestamp(ts, TZ)


class Engine:
    def __init__(self, data_dir: Path, geocoder: Geocoder, router: OsrmRouter,
                 demo_dir: Path):
        self.lock = threading.RLock()
        self.data_dir = Path(data_dir)
        self.state_path = self.data_dir / "state.json"
        self.geo = geocoder
        self.router = router
        self.demo_dir = Path(demo_dir)
        self.leg_geom: dict = {}      # (from_key, to_key) -> [(lat, lon), ...]
        self._gps_row = None          # last OSRM row from a GPS position
        self._geom_retry_at = 0.0     # back-off after OSRM route errors
        self.load()

    # ===================================================================
    # persistence / clock
    # ===================================================================
    def _empty(self):
        return {
            "day": datetime.now(TZ).date().isoformat(),
            "settings": json.loads(json.dumps(DEFAULT_SETTINGS)),
            "packages": {}, "stops": {}, "plan": None, "matrix": None,
            "courier": {"state": "depot", "at": "depot", "lat": None, "lon": None},
            "sim": {"active": False, "now": None, "seed": 7},
            "notifications": [], "log": [], "next_stop": 1,
        }

    def load(self):
        if self.state_path.exists():
            self.s = json.loads(self.state_path.read_text())
            for k, v in self._empty().items():
                self.s.setdefault(k, v)
            for k, v in DEFAULT_SETTINGS.items():
                self.s["settings"].setdefault(k, v)
        else:
            self.s = self._empty()
        c = self.s["courier"]
        if c.get("lat") is None:
            d = self.s["settings"]["depot"]
            c["lat"], c["lon"] = d["lat"], d["lon"]

    def save(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.s, ensure_ascii=False))
        tmp.replace(self.state_path)

    def now(self) -> datetime:
        sim = self.s["sim"]
        if sim["active"] and sim["now"]:
            return parse_dt(sim["now"])
        return datetime.now(TZ)

    def _day_time(self, hm: str | None):
        if not hm:
            return None
        day = datetime.fromisoformat(self.s["day"]).date()
        h, m = map(int, hm.split(":"))
        return datetime(day.year, day.month, day.day, h, m, tzinfo=TZ)

    def _log(self, text):
        self.s["log"].insert(0, {"t": iso(self.now()), "text": text})
        del self.s["log"][200:]

    def _notify(self, pkg_ids, kind, text):
        t = iso(self.now())
        for pid in pkg_ids:
            self.s["notifications"].insert(0, {"t": t, "pkg": pid, "kind": kind, "text": text})
        del self.s["notifications"][500:]

    # ===================================================================
    # packages
    # ===================================================================
    def add_package(self, data: dict, source: str):
        pid = str(data.get("id") or "").strip()
        if not pid:
            pid = f"P{len(self.s['packages']) + 1:04d}"
        if pid in self.s["packages"]:
            return self.s["packages"][pid], False
        address = (data.get("adresa") or data.get("address") or "").strip()
        place = (data.get("mesto") or data.get("place") or "").strip()
        if not address:
            raise ValueError("Paket nema adresu")
        w_from = _clean_hhmm(data.get("od") or data.get("from"))
        w_to = _clean_hhmm(data.get("do") or data.get("to"))
        d = self.s["settings"]["depot"]
        g = self.geo.geocode(address, place, ref=(d["lat"], d["lon"]))
        pkg = {
            "id": pid,
            "name": (data.get("ime") or data.get("name") or "").strip(),
            "phone": (data.get("tel") or data.get("phone") or "").strip(),
            "address": address, "place": place,
            "window": {"from": w_from, "to": w_to} if (w_from or w_to) else None,
            "note": (data.get("napomena") or data.get("note") or "").strip(),
            "geo": g.to_dict(),
            "lat": g.lat, "lon": g.lon, "confidence": g.confidence,
            "status": "pending", "stop": None, "source": source,
            "scanned_at": iso(self.now()), "track": secrets.token_hex(3),
            "promise": None, "delivered_at": None,
        }
        self.s["packages"][pid] = pkg
        self._log(f"Paket {pid} dodat ({source}): {g.label or address} [{g.confidence}]")
        if pkg["lat"] is not None:
            self._attach_if_running(pkg)
        return pkg, True

    def import_csv(self, text: str, source="csv"):
        rows = csv.DictReader(io.StringIO(text.lstrip("﻿")))
        added, errors = 0, []
        for i, row in enumerate(rows, 2):
            row = {(k or "").strip().lower(): (v or "") for k, v in row.items()}
            try:
                _, new = self.add_package(row, source)
                added += new
            except ValueError as e:
                errors.append(f"red {i}: {e}")
        return added, errors

    def load_demo(self, name: str):
        path = self.demo_dir / f"{name}.csv"
        if not path.exists():
            raise ValueError(f"Nema test skupa {name}")
        return self.import_csv(path.read_text(encoding="utf-8"), source="demo")

    def set_location(self, pid, lat, lon):
        pkg = self.s["packages"][pid]
        pkg.update(lat=lat, lon=lon, confidence="manual")
        pkg["geo"]["note"] = "lokacija ručno postavljena"
        old = pkg["stop"]
        if old and old in self.s["stops"]:
            st = self.s["stops"][old]
            if st["status"] == "pending":
                st["packages"].remove(pid)
                if not st["packages"]:
                    del self.s["stops"][old]
                else:
                    self._recompute_stop(st)
        pkg["stop"] = None
        self._attach_if_running(pkg)
        self._log(f"Paket {pid}: ručna lokacija {lat:.5f}, {lon:.5f}")

    def delete_package(self, pid):
        pkg = self.s["packages"].pop(pid)
        st = self.s["stops"].get(pkg["stop"] or "")
        if st:
            st["packages"] = [x for x in st["packages"] if x != pid]
            if not st["packages"]:
                del self.s["stops"][st["id"]]
                if self.s["plan"]:
                    self.s["plan"]["order"] = [x for x in self.s["plan"]["order"] if x != st["id"]]
            else:
                self._recompute_stop(st)

    def reset(self, keep_settings=True):
        settings = self.s["settings"]
        self.s = self._empty()
        if keep_settings:
            self.s["settings"] = settings
        d = self.s["settings"]["depot"]
        self.s["courier"].update(lat=d["lat"], lon=d["lon"])
        self.leg_geom.clear()

    def update_settings(self, patch: dict):
        st = self.s["settings"]
        for k, v in patch.items():
            if k not in DEFAULT_SETTINGS:
                continue
            if k in ("shift_start", "shift_end"):
                v = _clean_hhmm(v)
            st[k] = v
        if self.s["courier"]["state"] == "depot":
            d = st["depot"]
            self.s["courier"].update(lat=d["lat"], lon=d["lon"], at="depot")

    # ===================================================================
    # stops
    # ===================================================================
    def _running(self):
        return self.s["courier"]["state"] != "depot" or any(
            st["status"] != "pending" for st in self.s["stops"].values())

    @staticmethod
    def _bkey(pkg):
        osm = pkg["geo"].get("osm") if pkg["confidence"] != "manual" else ""
        return osm or f"{round(pkg['lat'], 4)},{round(pkg['lon'], 4)}"

    def _win_bounds(self, w):
        if not w:
            return None, None
        return self._day_time(w.get("from")), self._day_time(w.get("to"))

    def _compatible(self, stop, pkg):
        """Package fits into an existing stop if the windows overlap."""
        a1, b1 = self._win_bounds(stop.get("window"))
        a2, b2 = self._win_bounds(pkg.get("window"))
        lo = max([x for x in (a1, a2) if x] or [None]) if (a1 or a2) else None
        hi = min([x for x in (b1, b2) if x] or [None]) if (b1 or b2) else None
        return not (lo and hi and lo >= hi)

    def _recompute_stop(self, st):
        pk = [self.s["packages"][p] for p in st["packages"]]
        froms = [self._day_time(p["window"]["from"]) for p in pk if p["window"] and p["window"]["from"]]
        tos = [self._day_time(p["window"]["to"]) for p in pk if p["window"] and p["window"]["to"]]
        f = max(froms) if froms else None
        t = min(tos) if tos else None
        st["window"] = {"from": hhmm(f) or None, "to": hhmm(t) or None} if (f or t) else None
        st["lat"] = pk[0]["lat"]
        st["lon"] = pk[0]["lon"]
        st["label"] = pk[0]["geo"].get("label") or pk[0]["address"]

    def _new_stop(self, pkg):
        sid = f"S{self.s['next_stop']:03d}"
        self.s["next_stop"] += 1
        st = {"id": sid, "packages": [pkg["id"]], "status": "pending", "bkey": self._bkey(pkg),
              "attempts": 0, "not_before": None, "eta": None, "late_min": 0,
              "arrival_notified": False, "done_at": None}
        self.s["stops"][sid] = st
        pkg["stop"] = sid
        self._recompute_stop(st)
        return st

    def _place_package(self, pkg):
        key = self._bkey(pkg)
        for st in self.s["stops"].values():
            if st["status"] == "pending" and st["bkey"] == key and self._compatible(st, pkg):
                st["packages"].append(pkg["id"])
                pkg["stop"] = st["id"]
                self._recompute_stop(st)
                return st, False
        return self._new_stop(pkg), True

    def _attach_if_running(self, pkg):
        """During the day a new package joins the plan straight away."""
        if self.s["plan"] and self._running():
            st, new = self._place_package(pkg)
            if new:
                self.s["plan"]["order"].append(st["id"])
            self.plan(reason=f"novi paket {pkg['id']}")

    def rebuild_stops(self):
        self.s["stops"] = {}
        self.s["next_stop"] = 1
        pk = sorted((p for p in self.s["packages"].values()
                     if p["status"] == "pending" and p["lat"] is not None),
                    key=lambda p: (p["window"] is None, p["id"]))
        for p in pk:
            p["stop"] = None
            self._place_package(p)

    # ===================================================================
    # travel times
    # ===================================================================
    def _point(self, key):
        if key == "depot":
            d = self.s["settings"]["depot"]
            return d["lat"], d["lon"]
        st = self.s["stops"][key]
        return st["lat"], st["lon"]

    def ensure_matrix(self):
        keys = ["depot"] + sorted(self.s["stops"])
        m = self.s["matrix"]
        pts = [self._point(k) for k in keys]
        if m and m["keys"] == keys and m["points"] == [list(p) for p in pts]:
            return
        dur, dist, src = self.router.table(pts)
        self.s["matrix"] = {"keys": keys, "points": [list(p) for p in pts],
                            "dur": [[round(x, 1) for x in r] for r in dur],
                            "dist": [[round(x) for x in r] for r in dist], "source": src}
        self._midx = None

    def _mi(self):
        m = self.s["matrix"]
        if getattr(self, "_midx", None) is None or self._midx[0] is not m:
            self._midx = (m, {k: i for i, k in enumerate(m["keys"])})
        return self._midx[1]

    def dur(self, a, b):
        if a == b:
            return 0.0
        if a == "gps":
            return self._gps_row["dur"].get(b, 0.0)
        mi = self._mi()
        return self.s["matrix"]["dur"][mi[a]][mi[b]]

    def dist(self, a, b):
        if a == b or a == "gps":
            return 0
        mi = self._mi()
        return self.s["matrix"]["dist"][mi[a]][mi[b]]

    def factor_fn(self, t0: datetime):
        """Seconds after t0 -> slowdown: rush-hour profile x live ratio (decays)."""
        prof = self.s["settings"]["traffic_profile"]
        ratio = self.s["courier"].get("ratio", 1.0) or 1.0
        lead = (t0 - self.now()).total_seconds()

        def f(rel):
            t = t0 + timedelta(seconds=rel)
            live = 1 + (ratio - 1) * math.exp(-max(0.0, rel + lead) / 1200)
            return traffic_factor(t, prof) * live
        return f

    # ===================================================================
    # where is the courier, and from when/where does the rest start
    # ===================================================================
    def _anchor(self):
        """(start_key, leave_time, head) where head is the stop being driven
        to / served, already fixed, with its own ETA row."""
        c = self.s["courier"]
        now = self.now()
        if c["state"] == "depot":
            start = max(now, self._day_time(self.s["settings"]["shift_start"]))
            return "depot", start, None
        if c["state"] in ("driving", "serving") and c.get("target") in self.s["stops"]:
            tgt = c["target"]
            st = self.s["stops"][tgt]
            svc = self.s["settings"]["service_min"] * 60
            if c["state"] == "driving":
                arrival = self._eta_current_leg()
                w_from = self._earliest(st)
                begin = max(arrival, w_from) if w_from else arrival
                depart = begin + timedelta(seconds=svc)
            else:
                arrival = parse_dt(c["arrived_at"])
                begin = max(arrival, self._earliest(st) or arrival)
                depart = max(now, begin + timedelta(seconds=svc))
            head = {"stop": tgt, "arrival": arrival, "begin": begin, "depart": depart,
                    "travel": None}
            return tgt, depart, head
        at = c.get("at")
        if at and (at == "depot" or at in self.s["stops"]):
            return at, now, None
        return "gps", now, None

    def _eta_current_leg(self) -> datetime:
        c = self.s["courier"]
        now = self.now()
        start = parse_dt(c["leg_start"])
        expected = c["leg_expected"]
        if self.s["sim"]["active"] or c.get("progress") is not None:
            p = c.get("progress") or 0.0
            ratio = c.get("ratio", 1.0) or 1.0
            remaining = max(0.0, (1 - p) * expected * ratio)
            return max(now, now + timedelta(seconds=remaining))
        # real GPS: ask OSRM for the remaining time from the current position
        row = self._gps_row
        tgt = c["target"]
        if row and tgt in row["dur"]:
            age = (now - parse_dt(row["t"])).total_seconds()
            remaining = max(0.0, row["dur"][tgt] * traffic_factor(now, self.s["settings"]["traffic_profile"]) - age)
            return now + timedelta(seconds=remaining)
        return max(now, start + timedelta(seconds=expected))

    def _earliest(self, st):
        e = None
        if st.get("window") and st["window"].get("from"):
            e = self._day_time(st["window"]["from"])
        if st.get("not_before"):
            nb = parse_dt(st["not_before"])
            e = max(e, nb) if e else nb
        return e

    def _latest(self, st):
        if st.get("window") and st["window"].get("to"):
            return self._day_time(st["window"]["to"])
        return None

    def _promise_bounds(self, st):
        """Strictest promise across the packages of a stop: (from, to) or (None, None)."""
        prs = [self.s["packages"][p].get("promise") for p in st["packages"]
               if p in self.s["packages"]]
        prs = [p for p in prs if p]
        if not prs:
            return None, None
        return (max(parse_dt(p["from"]) for p in prs), min(parse_dt(p["to"]) for p in prs))

    def _pending_ids(self, exclude=None):
        return [sid for sid, st in self.s["stops"].items()
                if st["status"] == "pending" and sid != exclude]

    # ===================================================================
    # planning
    # ===================================================================
    def plan(self, reason="ručno"):
        if not self._running():
            self.rebuild_stops()
        if not self.s["stops"]:
            self.s["plan"] = None
            return
        self.ensure_matrix()
        set_ = self.s["settings"]
        start_key, t0, head = self._anchor()
        if start_key == "gps":
            self._fetch_gps_row(force=True)
        pending = self._pending_ids(exclude=head["stop"] if head else None)
        nodes = [start_key] + pending
        end_key = "depot" if set_["return_to_depot"] else None
        if end_key:
            nodes.append("depot_end")
        n = len(nodes)

        def key(i):
            return "depot" if nodes[i] == "depot_end" else nodes[i]

        dur = [[self.dur(key(i), key(j)) if key(j) != "gps" else 0 for j in range(n)]
               for i in range(n)]
        svc = set_["service_min"] * 60
        service = [0] + [svc] * len(pending) + ([0] if end_key else [])
        earliest, latest = [None] * n, [None] * n
        for i, sid in enumerate(pending, 1):
            st = self.s["stops"][sid]
            e, l = self._earliest(st), self._latest(st)
            # keep what we already promised ("by 15:15") unless it really can't be;
            # arriving earlier than promised is fine, the customer gets an update
            _, pt = self._promise_bounds(st)
            if pt:
                l = min(l, pt) if l else pt
            earliest[i] = (e - t0).total_seconds() if e else None
            latest[i] = (l - t0).total_seconds() if l else None
        shift_end = (self._day_time(set_["shift_end"]) - t0).total_seconds()
        horizon = int(max(shift_end, 0) + 12 * 3600)   # overtime is soft, see optimizer
        limit = set_["solver_seconds"] if not self._running() else min(2, set_["solver_seconds"])
        prob = Problem(dur, service, earliest, latest, self.factor_fn(t0), int(shift_end),
                       horizon, end_node=n - 1 if end_key else None, time_limit_s=limit)
        sol = solve(prob)
        order = [nodes[i] for i in sol.order]
        if head:
            order = [head["stop"]] + order
        dropped = [nodes[i] for i in sol.dropped]
        self.s["plan"] = {"order": order, "dropped": dropped, "made_at": iso(self.now()),
                          "reason": reason, "solve_ms": sol.solve_ms,
                          "matrix_source": self.s["matrix"]["source"]}
        self.s["last_replan"] = iso(self.now())
        self._log(f"Ruta optimizovana ({reason}): {len(order)} stanica, "
                  f"{len(dropped)} ne staje, {sol.solve_ms} ms")
        self.refresh(allow_replan=False)

    # ===================================================================
    # live ETAs
    # ===================================================================
    def refresh(self, allow_replan=True):
        plan = self.s["plan"]
        if not plan:
            return
        set_ = self.s["settings"]
        svc = set_["service_min"] * 60
        start_key, t0, head = self._anchor()
        if start_key == "gps":
            self._fetch_gps_row()
        f = self.factor_fn(t0)
        order = [sid for sid in plan["order"]
                 if sid in self.s["stops"] and self.s["stops"][sid]["status"] == "pending"]
        plan["order"] = order
        rows = []
        if head:
            rows.append(head)
            order_rest = order[1:] if order and order[0] == head["stop"] else order
        else:
            order_rest = order
        t, prev = t0, start_key
        for sid in order_rest:
            st = self.s["stops"][sid]
            rel = (t - t0).total_seconds()
            travel = self.dur(prev, sid) * f(rel)
            arrival = t + timedelta(seconds=travel)
            e = self._earliest(st)
            begin = max(arrival, e) if e else arrival
            depart = begin + timedelta(seconds=svc)
            rows.append({"stop": sid, "arrival": arrival, "begin": begin, "depart": depart,
                         "travel": travel, "from": prev})
            t, prev = depart, sid

        worst_late = 0
        for i, r in enumerate(rows):
            st = self.s["stops"][r["stop"]]
            l = self._latest(st)
            late = max(0, (r["begin"] - l).total_seconds() / 60) if l else 0
            st.update(seq=i + 1, eta=iso(r["begin"]), arrival=iso(r["arrival"]),
                      depart=iso(r["depart"]), late_min=round(late),
                      wait_min=round((r["begin"] - r["arrival"]).total_seconds() / 60),
                      travel_min=round(r["travel"] / 60, 1) if r["travel"] is not None else None)
            if l:
                worst_late = max(worst_late, late)
            self._update_promise(st, r["begin"])

        drive = sum(r["travel"] or 0 for r in rows)
        plan["summary"] = {
            "stops": len(rows), "packages": sum(len(self.s["stops"][r["stop"]]["packages"]) for r in rows),
            "drive_min": round(drive / 60), "service_min": len(rows) * set_["service_min"],
            "wait_min": round(sum((r["begin"] - r["arrival"]).total_seconds() for r in rows) / 60),
            "end": iso(rows[-1]["depart"]) if rows else None,
            "late_stops": sum(1 for r in rows if self.s["stops"][r["stop"]]["late_min"] > 0),
            "km": round(sum(self.dist(r.get("from", start_key), r["stop"]) for r in rows if r.get("from")) / 1000, 1),
        }
        self._arrival_notices(rows)

        if not allow_replan:
            plan["late_after"] = worst_late        # what the fresh plan could not avoid
        elif (worst_late >= set_["late_threshold_min"]
              and worst_late > plan.get("late_after", 0) + set_["late_threshold_min"] / 2):
            last = parse_dt(self.s.get("last_replan"))
            if not last or (self.now() - last).total_seconds() >= REPLAN_COOLDOWN_S:
                self.plan(reason=f"kašnjenje {round(worst_late)} min")

    def _promise_width(self, eta):
        """Uncertainty grows with lead time: 60 min for the next hour or so,
        up to 3 h for stops late in the day."""
        base = self.s["settings"]["promise_width_min"]
        lead_h = max(0.0, (eta - self.now()).total_seconds() / 3600)
        return min(180, base + max(0.0, lead_h - 1.5) * 20)

    def _update_promise(self, st, eta):
        for pid in st["packages"]:
            pkg = self.s["packages"][pid]
            pr = pkg.get("promise")
            width = self._promise_width(eta)
            narrow = (pr and not pr.get("narrow") and width <= self.s["settings"]["promise_width_min"]
                      and (parse_dt(pr["to"]) - parse_dt(pr["from"])).total_seconds() / 60 > width + 1)
            if pr and parse_dt(pr["from"]) <= eta <= parse_dt(pr["to"]) and not narrow:
                continue
            # delays are likelier than early arrivals: 40 % before, 60 % after
            lo = _round15(eta - timedelta(minutes=width * 0.4), up=False)
            hi = _round15(eta + timedelta(minutes=width * 0.6), up=True)
            w_from, w_to = self._win_bounds(pkg.get("window"))
            if w_from and lo < w_from <= eta:
                lo = w_from
            if w_to and eta <= w_to < hi:
                hi = w_to
            later = pr is not None and eta > parse_dt(pr["to"])
            pkg["promise"] = {"from": iso(lo), "to": iso(hi),
                              "narrow": width <= self.s["settings"]["promise_width_min"]}
            if pr is None:
                self._notify([pid], "plan", f"Vaša dostava je planirana između {hhmm(lo)} i {hhmm(hi)}. "
                             "Kurir će vas pozvati da izađete ispred zgrade.")
            elif narrow and parse_dt(pr["from"]) <= eta <= parse_dt(pr["to"]):
                self._notify([pid], "narrow", f"Preciznije vreme dostave: {hhmm(lo)}–{hhmm(hi)}.")
            else:
                why = ("Kurir kasni zbog gužve ili prethodnih dostava." if later
                       else "Kurir stiže ranije nego što je planirano.")
                self._notify([pid], "update", f"Novo vreme dostave: {hhmm(lo)}–{hhmm(hi)}. {why}")

    def _arrival_notices(self, rows):
        """'Izađite ispred zgrade' ~notify_before_min before arrival. Also checked
        for the stop after the current one, so a short hop still gets a heads-up
        while the courier is finishing the previous delivery."""
        if self.s["courier"]["state"] in ("depot", "done"):
            return
        now = self.now()
        for r in rows[:2]:
            st = self.s["stops"][r["stop"]]
            if st["arrival_notified"]:
                continue
            mins = (r["arrival"] - now).total_seconds() / 60
            if mins <= self.s["settings"]["notify_before_min"]:
                st["arrival_notified"] = True
                self._notify(st["packages"], "arriving",
                             f"Kurir stiže za ~{max(1, round(mins))} min na adresu {st['label']}. "
                             "Molimo izađite ispred zgrade.")

    # ===================================================================
    # courier actions (phone or simulator)
    # ===================================================================
    def _start_leg(self, target):
        c = self.s["courier"]
        frm = c.get("at") or "gps"
        if frm == "gps":
            self._fetch_gps_row(force=True)
        now = self.now()
        expected = self.dur(frm, target) * traffic_factor(now, self.s["settings"]["traffic_profile"])
        # a new street, new conditions: forget half of the slowdown seen so far
        c["ratio"] = round(1 + ((c.get("ratio") or 1.0) - 1) * 0.5, 3)
        c.update(state="driving", target=target, leg_from=frm, leg_start=iso(now),
                 leg_expected=expected, progress=0.0 if self.s["sim"]["active"] else None,
                 from_lat=c["lat"], from_lon=c["lon"])

    def start_day(self):
        if not self.s["plan"]:
            self.plan(reason="početak dana")
        order = self.s["plan"]["order"] if self.s["plan"] else []
        if order and self.s["courier"]["state"] == "depot":
            self._start_leg(order[0])
            self._log(f"Kurir krenuo ka {order[0]}")
        self.refresh()

    def _next_leg(self):
        order = [sid for sid in (self.s["plan"] or {}).get("order", [])
                 if self.s["stops"].get(sid, {}).get("status") == "pending"]
        c = self.s["courier"]
        if order:
            self._start_leg(order[0])
        else:
            c.update(state="done", target=None)
            self._log("Sve stanice obiđene")

    def arrive(self, sid):
        c = self.s["courier"]
        st = self.s["stops"][sid]
        c.update(state="serving", target=sid, at=None, lat=st["lat"], lon=st["lon"],
                 arrived_at=iso(self.now()), progress=None)
        if not st["arrival_notified"]:
            st["arrival_notified"] = True
            self._notify(st["packages"], "arriving",
                         f"Kurir je stigao na adresu {st['label']}. Molimo izađite ispred zgrade.")

    def stop_action(self, sid, action):
        st = self.s["stops"][sid]
        now = self.now()
        c = self.s["courier"]
        if c.get("state") == "driving" and c.get("target") == sid:
            self._learn_leg(sid)
        c.update(lat=st["lat"], lon=st["lon"], at=sid, state="idle", target=None, progress=None)
        if action == "delivered":
            st.update(status="done", done_at=iso(now))
            for pid in st["packages"]:
                p = self.s["packages"][pid]
                p.update(status="delivered", delivered_at=iso(now))
            self._notify(st["packages"], "delivered", f"Paket je isporučen u {hhmm(now)}. Hvala!")
            self._log(f"{sid} isporučeno ({len(st['packages'])} pak.)")
            self._next_leg()
            self.refresh()
        elif action == "failed":
            st["attempts"] += 1
            if st["attempts"] >= 2:
                st.update(status="failed", done_at=iso(now))
                for pid in st["packages"]:
                    self.s["packages"][pid]["status"] = "failed"
                self._notify(st["packages"], "failed",
                             "Nismo uspeli da vas dobijemo ni iz drugog pokušaja. Javićemo se za novi termin.")
                self._log(f"{sid} neuspešno (2. pokušaj)")
            else:
                nb = now + timedelta(minutes=self.s["settings"]["retry_after_min"])
                st.update(not_before=iso(nb), arrival_notified=False)
                self._notify(st["packages"], "retry",
                             f"Kurir vas nije dobio. Pokušaće ponovo posle {hhmm(nb)}.")
                self._log(f"{sid} nije se javio, novi pokušaj posle {hhmm(nb)}")
            self.plan(reason=f"{sid} nije se javio")
            self._next_leg()
            self.refresh(allow_replan=False)
        else:
            raise ValueError(action)

    def _learn_leg(self, sid):
        """After a leg: how much slower than planned was it? Feeds the live ratio."""
        c = self.s["courier"]
        start = parse_dt(c.get("leg_start"))
        if not start or not c.get("leg_expected"):
            return
        actual = (self.now() - start).total_seconds()
        if c.get("state") == "serving":
            actual = (parse_dt(c["arrived_at"]) - start).total_seconds()
        ratio = max(0.6, min(3.0, actual / max(60.0, c["leg_expected"])))
        c["ratio"] = round(0.5 * (c.get("ratio") or 1.0) + 0.5 * ratio, 3)

    def courier_position(self, lat, lon):
        """GPS from the phone (real clock)."""
        if self.s["sim"]["active"]:
            return
        c = self.s["courier"]
        c.update(lat=lat, lon=lon, gps_at=iso(self.now()))
        if c["state"] == "depot":
            d = self.s["settings"]["depot"]
            if self.s["plan"] and haversine_m(lat, lon, d["lat"], d["lon"]) > 150:
                c["at"] = "gps"
                self.start_day()
            return
        if c["state"] in ("idle", "done"):
            c["at"] = "gps"
        if c["state"] == "driving" and c.get("target") in self.s["stops"]:
            st = self.s["stops"][c["target"]]
            if haversine_m(lat, lon, st["lat"], st["lon"]) <= ARRIVE_RADIUS_M:
                self._learn_leg(c["target"])
                self.arrive(c["target"])
            else:
                self._fetch_gps_row()
                row = self._gps_row
                if row and c["target"] in row["dur"]:
                    elapsed = (self.now() - parse_dt(c["leg_start"])).total_seconds()
                    rem = row["dur"][c["target"]] * traffic_factor(self.now(), self.s["settings"]["traffic_profile"])
                    if elapsed > 120:
                        c["ratio"] = round(max(0.6, min(3.0, (elapsed + rem) / max(60.0, c["leg_expected"]))), 3)
        self.refresh()

    def _fetch_gps_row(self, force=False):
        c = self.s["courier"]
        now = self.now()
        row = self._gps_row
        if row and not force and (now - parse_dt(row["t"])).total_seconds() < GPS_ROW_INTERVAL_S:
            return
        keys = self._pending_ids() + ["depot"]
        pts = [(c["lat"], c["lon"])] + [self._point(k) for k in keys]
        dur, _, _ = self.router.table(pts, sources=[0], use_cache=False)
        self._gps_row = {"t": iso(now), "dur": dict(zip(keys, dur[0][1:]))}

    # ===================================================================
    # simulator
    # ===================================================================
    def sim_start(self, seed=None):
        start = self._day_time(self.s["settings"]["shift_start"]) - timedelta(minutes=10)
        self.s["sim"] = {"active": True, "now": iso(start), "seed": seed or 7}
        d = self.s["settings"]["depot"]
        self.s["courier"] = {"state": "depot", "at": "depot", "lat": d["lat"], "lon": d["lon"],
                             "ratio": 1.0}
        for st in self.s["stops"].values():
            st.update(status="pending", attempts=0, not_before=None, arrival_notified=False,
                      done_at=None)
        for p in self.s["packages"].values():
            if p["status"] != "pending":
                p.update(status="pending", delivered_at=None)
            p["promise"] = None
        self.s["notifications"] = []
        self.s["plan"] = None
        self._rng = random.Random(self.s["sim"]["seed"])
        self._log(f"Simulacija počinje u {hhmm(start)}")
        self.plan(reason="simulacija: plan za ceo dan")

    def sim_stop(self):
        self.s["sim"]["active"] = False
        self._log("Simulacija zaustavljena, sat je ponovo stvaran")

    def _rng_get(self):
        if not hasattr(self, "_rng"):
            self._rng = random.Random(self.s["sim"].get("seed", 7))
        return self._rng

    def _sim_leg_actual(self, expected):
        r = self._rng_get().random()
        if r < 0.82:
            k = self._rng_get().uniform(0.8, 1.05)      # as planned
        elif r < 0.96:
            k = self._rng_get().uniform(1.3, 1.8)       # gužva
        else:
            k = self._rng_get().uniform(2.0, 2.8)       # zastoj / udes
        return expected * k, k

    def sim_step(self, minutes: float):
        if not self.s["sim"]["active"]:
            raise ValueError("Simulacija nije pokrenuta")
        target = self.now() + timedelta(minutes=minutes)
        guard = 0
        while self.now() < target and guard < 1000:
            guard += 1
            c = self.s["courier"]
            now = self.now()
            if c["state"] == "depot":
                t_start = self._day_time(self.s["settings"]["shift_start"])
                if now < t_start:
                    self._set_sim_now(min(t_start, target))
                    continue
                if not self.s["plan"] or not self.s["plan"]["order"]:
                    break
                self.start_day()
                self._sim_init_leg()
                continue
            if c["state"] == "driving":
                actual = c["sim_actual"]
                start = parse_dt(c["leg_start"])
                arrive_at = start + timedelta(seconds=actual)
                nxt = min(arrive_at, target, now + timedelta(minutes=1))
                self._set_sim_now(nxt)
                el = (nxt - start).total_seconds()
                c["progress"] = min(1.0, el / actual) if actual > 0 else 1.0
                if el >= 120 or c["progress"] >= 0.2:
                    c["ratio"] = round(c["sim_k"], 3)     # observed from GPS speed
                self._sim_move()
                if nxt >= arrive_at:
                    self._learn_leg(c["target"])
                    self.arrive(c["target"])
                    self._sim_init_service()
                self.refresh()
                continue
            if c["state"] == "serving":
                end = parse_dt(c["sim_service_end"])
                if end > target:
                    self._set_sim_now(target)
                    break
                self._set_sim_now(end)
                outcome = c["sim_outcome"]
                self.stop_action(c["target"], outcome)
                if self.s["courier"]["state"] == "driving":
                    self._sim_init_leg()
                continue
            if c["state"] in ("idle",):
                self._next_leg()
                if self.s["courier"]["state"] == "driving":
                    self._sim_init_leg()
                continue
            self._set_sim_now(target)       # done
        self.refresh()

    def _set_sim_now(self, dt):
        self.s["sim"]["now"] = iso(dt)

    def _sim_init_leg(self):
        c = self.s["courier"]
        actual, k = self._sim_leg_actual(c["leg_expected"])
        c.update(sim_actual=max(30.0, actual), sim_k=k, progress=0.0)
        if k > 1.35:
            self._log(f"Simulacija: gužva na putu ka {c['target']} ({k:.1f}x sporije)")

    def _sim_init_service(self):
        c = self.s["courier"]
        st = self.s["stops"][c["target"]]
        svc = self.s["settings"]["service_min"] * 60
        r = self._rng_get().random()
        begin = max(self.now(), self._earliest(st) or self.now())
        if r < 0.86:
            dur, outcome = svc * self._rng_get().uniform(0.5, 1.0), "delivered"
        elif r < 0.95:
            dur, outcome = svc + self._rng_get().uniform(2, 6) * 60, "delivered"
            self._log(f"Simulacija: kupac na {st['id']} sporo izlazi")
        else:
            dur, outcome = svc, "failed"
            self._log(f"Simulacija: kupac na {st['id']} se ne javlja")
        c.update(sim_service_end=iso(begin + timedelta(seconds=dur)), sim_outcome=outcome)

    def _sim_move(self):
        c = self.s["courier"]
        line = self._leg_line(c["leg_from"], c["target"], (c["from_lat"], c["from_lon"]))
        p = point_along(line, c["progress"] or 0)
        if p:
            c["lat"], c["lon"] = p

    # ===================================================================
    # geometry for the map
    # ===================================================================
    def _leg_line(self, a, b, a_point=None):
        pa = tuple(a_point) if a_point else self._point(a)
        k = (a if a != "gps" else f"gps:{pa[0]:.5f},{pa[1]:.5f}", b)
        if k in self.leg_geom:
            return self.leg_geom[k]
        if time.monotonic() < self._geom_retry_at:
            return [pa, self._point(b)]
        line = self.router.route_legs([pa, self._point(b)])[0]
        if self.router.last_error:
            self._geom_retry_at = time.monotonic() + 60
        else:
            self.leg_geom[k] = line
        return line

    def geometry(self):
        """Road geometry: courier -> target, then the remaining order."""
        plan = self.s["plan"]
        if not plan or not plan["order"]:
            return {"version": self.geometry_version(), "current": [], "rest": []}
        c = self.s["courier"]
        order = plan["order"]
        cur = []
        if c["state"] == "driving" and c.get("target") == order[0]:
            cur = self._leg_line(c["leg_from"], order[0], (c["from_lat"], c["from_lon"]))
            rest_keys = order
        else:
            start = c.get("at") if c.get("at") in self.s["stops"] or c.get("at") == "depot" else None
            rest_keys = ([start] if start else []) + order
            if not start:
                cur = [(c["lat"], c["lon"]), self._point(order[0])]
        pairs = list(zip(rest_keys, rest_keys[1:]))
        missing = [p for p in pairs if p not in self.leg_geom]
        if missing and time.monotonic() >= self._geom_retry_at:
            legs = self.router.route_legs([self._point(k) for k in rest_keys])
            if self.router.last_error:
                self._geom_retry_at = time.monotonic() + 60
            else:
                for p, line in zip(pairs, legs):
                    self.leg_geom[p] = line
        rest = []
        for p in pairs:
            rest.append(self.leg_geom.get(p, [self._point(p[0]), self._point(p[1])]))

        def r5(line):
            return [[round(a, 5), round(b, 5)] for a, b in line]
        return {"version": self.geometry_version(), "current": r5(cur),
                "rest": [r5(x) for x in rest]}

    def geometry_version(self):
        """Changes whenever the drawn route changes; clients refetch on change."""
        plan = self.s["plan"] or {}
        c = self.s["courier"]
        key = json.dumps([plan.get("order"), c.get("state") == "driving", c.get("target"),
                          c.get("leg_start"), c.get("at")])
        return hashlib.sha1(key.encode()).hexdigest()[:12]

    # ===================================================================
    # API view
    # ===================================================================
    def snapshot(self):
        self.refresh()
        now = self.now()
        s = self.s
        plan = s["plan"]
        f_now = traffic_factor(now, s["settings"]["traffic_profile"])
        stops_out = []
        order = plan["order"] if plan else []
        for sid in order:
            stops_out.append(self._stop_view(s["stops"][sid]))
        done = sorted((st for st in s["stops"].values() if st["status"] != "pending"),
                      key=lambda st: st.get("done_at") or "")
        unplanned = [self._stop_view(s["stops"][sid]) for sid in s["stops"]
                     if s["stops"][sid]["status"] == "pending" and sid not in order]
        return {
            "now": iso(now), "clock": "sim" if s["sim"]["active"] else "real", "day": s["day"],
            "settings": s["settings"],
            "traffic": {"profile": s["settings"]["traffic_profile"], "factor_now": f_now,
                        "live_ratio": s["courier"].get("ratio", 1.0),
                        "source": (plan or {}).get("matrix_source") or (s["matrix"] or {}).get("source"),
                        "router_error": self.router.last_error},
            "courier": {k: s["courier"].get(k) for k in
                        ("state", "target", "lat", "lon", "progress", "ratio", "at")},
            "packages": sorted(s["packages"].values(), key=lambda p: p["id"]),
            "route": stops_out,
            "done": [self._stop_view(st) for st in done],
            "unplanned": unplanned,
            "unlocated": [p["id"] for p in s["packages"].values() if p["lat"] is None],
            "plan": {k: v for k, v in (plan or {}).items() if k != "order"} if plan else None,
            "geom_version": self.geometry_version(),
            "notifications": s["notifications"][:150],
            "log": s["log"][:60],
        }

    def _stop_view(self, st):
        pk = [self.s["packages"][p] for p in st["packages"] if p in self.s["packages"]]
        return {
            "id": st["id"], "seq": st.get("seq"), "status": st["status"], "label": st["label"],
            "lat": st["lat"], "lon": st["lon"], "window": st.get("window"),
            "eta": st.get("eta"), "arrival": st.get("arrival"), "late_min": st.get("late_min", 0),
            "wait_min": st.get("wait_min", 0), "travel_min": st.get("travel_min"),
            "attempts": st.get("attempts", 0), "not_before": st.get("not_before"),
            "done_at": st.get("done_at"),
            "packages": [{"id": p["id"], "name": p["name"], "phone": p["phone"],
                          "address": p["address"], "track": p["track"],
                          "confidence": p["confidence"], "status": p["status"],
                          "promise": p.get("promise")} for p in pk],
        }

    def customer_view(self, track):
        for p in self.s["packages"].values():
            if p["track"] == track:
                self.refresh()
                st = self.s["stops"].get(p["stop"] or "")
                ahead = None
                if st and st["status"] == "pending" and self.s["plan"]:
                    order = self.s["plan"]["order"]
                    if st["id"] in order:
                        ahead = order.index(st["id"])
                return {"package": p, "stop": self._stop_view(st) if st else None,
                        "stops_before": ahead, "now": iso(self.now()),
                        "notifications": [n for n in self.s["notifications"] if n["pkg"] == p["id"]]}
        return None

