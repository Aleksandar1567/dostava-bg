"""Building-level geocoder for Belgrade on top of the local OSM address index.

Result confidence:
  ok      street and house number found (an apartment after '/' is ignored)
  approx  something was guessed (partial street name, entrance letter dropped,
          nearest house number, several streets with the same name)
          -> the courier should glance at the pin
  none    not found -> package needs a manual pin on the map
"""

import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from rapidfuzz import fuzz, process

from .sqlite_compat import sqlite3
from .textnorm import base_number, fold, norm_housenumber, norm_street, parse_address

PLACE_PRIORITY = {"city": 0, "town": 1, "suburb": 2, "village": 3, "quarter": 4,
                  "neighbourhood": 5, "hamlet": 6, "locality": 7}
STREET_CUTOFF = 80      # rapidfuzz WRatio, 0..100
STREET_OK = 90          # below this the street match is reported as a guess
AMBIGUOUS_M = 800       # same street+number further apart than this = different streets
PLACE_RADIUS_M = 6000   # with a place hint, prefer anything within this radius
BELGRADE_CENTER = (44.8125, 20.4612)


@dataclass
class GeoResult:
    confidence: str                      # ok | approx | none
    lat: float | None = None
    lon: float | None = None
    label: str = ""                      # what we matched, e.g. "Bulevar Mihajla Pupina 10"
    note: str = ""                       # human readable, Serbian
    place: str = ""                      # place hint we used, if any
    osm: str = ""
    alternatives: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _num(hn_norm: str):
    m = re.match(r"\d+", hn_norm)
    return int(m.group(0)) if m else None


class Geocoder:
    def __init__(self, db_path: Path):
        if not Path(db_path).exists():
            raise FileNotFoundError(
                f"{db_path} missing - run: .venv/bin/python tools/build_address_index.py")
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.streets = [r[0] for r in self.db.execute("SELECT DISTINCT street_norm FROM addr")]
        self.street_set = set(self.streets)
        self.alias: dict[str, set] = {}
        for a, t in self.db.execute("SELECT alias_norm, street_norm FROM alias"):
            if t in self.street_set:
                self.alias.setdefault(a, set()).add(t)
        self.fuzzy_keys = self.streets + [a for a in self.alias if a not in self.street_set]
        self.places = self.db.execute(
            "SELECT name, name_norm, type, lat, lon FROM place").fetchall()
        self.place_norms = [p[1] for p in self.places]
        self.count = self.db.execute("SELECT COUNT(*) FROM addr").fetchone()[0]

    # ---- places ---------------------------------------------------------
    def find_place(self, text: str):
        """('Novi Beograd', lat, lon) for a place name, or None."""
        n = fold(text or "").strip(" ,.")
        if not n:
            return None
        hits = [p for p in self.places if p[1] == n]
        if not hits:
            best = process.extract(n, self.place_norms, scorer=fuzz.ratio, limit=5,
                                   score_cutoff=88)
            hits = [self.places[i] for _, _, i in best]
        if not hits:
            return None
        hits.sort(key=lambda p: PLACE_PRIORITY.get(p[2], 9))
        name, _, _, lat, lon = hits[0]
        return name, lat, lon

    # ---- streets ----------------------------------------------------------
    def _street_candidates(self, s_norm: str):
        """[(street_norm, score)]: exact name, register name of an old/colloquial
        name, or the best fuzzy matches."""
        out = {}
        if s_norm in self.street_set:
            out[s_norm] = 100.0
        for t in self.alias.get(s_norm, ()):
            out.setdefault(t, 99.0)
        if out:
            return list(out.items())
        found = process.extract(s_norm, self.fuzzy_keys, scorer=fuzz.WRatio, limit=6,
                                score_cutoff=STREET_CUTOFF)
        if not found:
            return []
        top = found[0][1]
        for key, sc, _ in found:
            if sc < top - 2:           # keep only those tied with the best
                continue
            targets = ({key} if key in self.street_set else set()) | self.alias.get(key, set())
            for t in targets:
                out[t] = max(out.get(t, 0), sc)
        return list(out.items())

    def _rows(self, street_norm: str):
        return self.db.execute(
            "SELECT street, hn, hn_norm, lat, lon, osm, building FROM addr WHERE street_norm=?",
            (street_norm,)).fetchall()

    def _match_number(self, sn, score, hn, hn_norm, tried):
        """Candidates on one street: (tier, score, row, how, street_norm)."""
        rows = self._rows(sn)
        out = []
        by_hn = {}
        for r in rows:
            by_hn.setdefault(r[2], []).append(r)
        if hn_norm:
            for t_i, key in enumerate(tried):
                if key in by_hn:
                    # '10/25' -> '10' is still exact: the part after '/' is the apartment
                    tier = 0 if t_i == 0 or (t_i == 1 and "/" in hn_norm) else 1
                    how = "exact" if tier == 0 else f"broj {by_hn[key][0][1]} umesto {hn}"
                    return [(tier, score, r, how, sn) for r in by_hn[key]]
            want = _num(hn_norm)
            nums = [(abs(_num(r[2]) - want), r) for r in rows
                    if want is not None and _num(r[2]) is not None
                    and _num(r[2]) % 2 == want % 2]
            if nums:
                best_d = min(d for d, _ in nums)
                if best_d == 0:
                    # same number, other letter ('10' -> '10a'): take the first letter
                    first = min(r[2] for d, r in nums if d == 0)
                    return [(1, score, r, f"broj {r[1]} umesto {hn}", sn)
                            for d, r in nums if r[2] == first]
                return [(2, score, r, f"broj {hn} ne postoji, najbliži je {r[1]}", sn)
                        for d, r in nums if d == best_d]
        if rows:
            # no number / 'bb': street centroid, usable only for a short street
            lat = sum(r[3] for r in rows) / len(rows)
            lon = sum(r[4] for r in rows) / len(rows)
            span = max(haversine_m(lat, lon, r[3], r[4]) for r in rows)
            tier = 3 if span < 400 else 4
            how = "bez broja" if hn == "bb" else "broj nije naveden"
            out.append((tier, score, (rows[0][0], "", "", lat, lon, "", 0), how, sn))
        return out

    # ---- main entry ---------------------------------------------------------
    def geocode(self, address: str, place_hint: str = "", ref=None) -> GeoResult:
        """`ref` (lat, lon) breaks ties when no place is given - e.g. the depot."""
        street, hn, rest = parse_address(address or "")
        place = self.find_place(place_hint) or (self.find_place(rest) if rest else None)
        place_name = place[0] if place else ""
        s_norm = norm_street(street)
        if not s_norm:
            return GeoResult("none", note="Adresa je prazna", place=place_name)

        streets = self._street_candidates(s_norm)
        if not streets:
            return GeoResult("none", note=f"Ulica '{street}' nije pronađena", place=place_name)

        hn_norm = norm_housenumber(hn) if hn and hn != "bb" else ""
        tried = [hn_norm]
        no_sub = re.sub(r"/.*$", "", hn_norm)          # '7a/3' -> '7a' (apartment)
        if no_sub and no_sub not in tried:
            tried.append(no_sub)
        base = base_number(no_sub)                      # '7a' -> '7' (entrance letter)
        if base and base not in tried:
            tried.append(base)

        cands = []
        for sn, score in streets:
            cands += self._match_number(sn, score, hn, hn_norm, tried)
        if not cands:
            return GeoResult("none", note=f"Nema kućnih brojeva za '{street}'", place=place_name)

        refp = (place[1], place[2]) if place else (ref or BELGRADE_CENTER)

        def dist(c):
            return haversine_m(refp[0], refp[1], c[2][3], c[2][4])

        def strength(c):
            # exact name (100) and register name of an alias (99) count the same
            return 0 if c[1] >= 97 else -c[1]

        if place:
            # the right district beats a better house-number match elsewhere
            cands.sort(key=lambda c: (dist(c) > PLACE_RADIUS_M, c[0], strength(c), dist(c),
                                      -c[2][6]))
        else:
            cands.sort(key=lambda c: (c[0], strength(c), dist(c), -c[2][6]))
        tier, score, row, how, sn = cands[0]
        label = f"{row[0]} {row[1]}".strip()

        # other plausible answers: same quality, but somewhere else
        rivals = [c for c in cands[1:] if c[0] <= max(tier, 1) and
                  haversine_m(row[3], row[4], c[2][3], c[2][4]) > AMBIGUOUS_M]
        # a different street that matched about as well -> the name is ambiguous
        other_streets = {c[4] for c in cands if c[0] <= tier and c[1] >= score - 2} - {sn}
        partial = sn != s_norm and sn not in self.alias.get(s_norm, ()) and score < 100

        notes = []
        if score < STREET_OK or (partial and other_streets):
            notes.append(f"ulica pogođena kao '{row[0]}'")
        if how != "exact":
            notes.append(how)
        # a unique street+number is fine even far from the place centroid
        # (Zemun polje is "Zemun"); with rivals the place has to decide
        unclear = bool(rivals) and (not place or dist(cands[0]) > PLACE_RADIUS_M)
        if rivals and not place:
            notes.append("isti naziv postoji na više mesta, dodaj mesto (opštinu)")
        elif rivals and unclear:
            notes.append(f"isti naziv postoji na više mesta, nijedno blizu mesta {place_name}")
        elif rivals:
            notes.append(f"izabrana najbliža mestu {place_name}")

        if tier == 4:
            conf = "none"
        elif tier == 0 and score >= STREET_OK and not (partial and other_streets) and not unclear:
            conf = "ok"
        else:
            conf = "approx"

        alts, seen = [], {(round(row[3], 4), round(row[4], 4))}
        for c in cands[1:]:
            k = (round(c[2][3], 4), round(c[2][4], 4))
            if k in seen or c[0] > 2:
                continue
            seen.add(k)
            alts.append({"label": f"{c[2][0]} {c[2][1]}".strip(), "lat": c[2][3], "lon": c[2][4]})
            if len(alts) == 3:
                break

        if conf == "none":
            # never plan a package on a street centroid; offer it as a hint for the pin
            return GeoResult("none", None, None, label, "; ".join(notes) + " - postavi pin ručno",
                             place_name, "", [{"label": label, "lat": row[3], "lon": row[4],
                                                "hint": True}] + alts)
        return GeoResult(conf, row[3], row[4], label, "; ".join(notes), place_name,
                         row[5], alts)
