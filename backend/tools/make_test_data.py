"""Generate test package lists (CSV) and printable QR labels (HTML).

Addresses are real buildings from the OSM address index; names are made up
("Jelena M.") and phone numbers are the non-dialable placeholder TEST-xxxx,
so the "call customer" button can never reach a real person.

Usage (from backend/):
    .venv/bin/python tools/make_test_data.py
Writes ../test-data/<set>.csv and ../test-data/<set>-etikete.html
"""

import csv
import html
import json
import math
import random
import sys
from pathlib import Path

import segno

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.sqlite_compat import sqlite3  # noqa: E402
from app.textnorm import fold  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
OUT = BACKEND.parent / "test-data"
DB = BACKEND / "data" / "addresses.sqlite"

FIRST = ["Jelena", "Marko", "Milica", "Nikola", "Ana", "Stefan", "Jovana", "Luka", "Ivana",
         "Petar", "Marija", "Aleksa", "Teodora", "Nemanja", "Katarina", "Vuk", "Sara", "Filip",
         "Tamara", "Dušan", "Mina", "Uroš", "Nevena", "Lazar", "Dragana", "Miloš", "Sanja",
         "Đorđe", "Tijana", "Bojan"]
LAST_INITIAL = list("AĐBVGDJKLMNOPRSTUZŽČĆŠ")

WINDOWS = [("09:00", "11:00"), ("10:00", "12:00"), ("12:00", "14:00"), ("14:00", "16:00"),
           ("16:00", "18:00"), ("17:00", "20:00"), ("15:00", ""), ("", "12:00")]

# (place shown on the label, centroid, radius m, number of buildings)
REJON = [   # one courier's area: compact, big residential blocks -> several packages per building
    ("Novi Beograd", (44.8133, 20.4183), 2000, 32),
    ("Zemun", (44.8444, 20.4124), 1200, 15),
    ("Novi Beograd", (44.8173, 20.3819), 800, 7),     # Bežanijska kosa
    ("Beograd", (44.8021, 20.3485), 600, 3),          # Ledine, generic city on the form
    ("Surčin", (44.7910, 20.2702), 800, 3),           # okolina
]
GRAD = [
    ("Vračar", (44.7979, 20.4722), 900, 3), ("Stari grad", (44.8197, 20.4602), 700, 3),
    ("Zvezdara", (44.7998, 20.5033), 1000, 3), ("Voždovac", (44.7779, 20.4735), 1000, 2),
    ("Čukarica", (44.7791, 20.4131), 900, 2), ("Rakovica", (44.7463, 20.4403), 900, 2),
    ("Palilula", (44.8131, 20.5126), 800, 2), ("Borča", (44.8773, 20.4570), 1000, 2),
    ("Kaluđerica", (44.7558, 20.5579), 900, 2), ("Pančevo", (44.8706, 20.6400), 1200, 2),
    ("Novi Beograd", (44.8133, 20.4183), 2000, 3), ("Zemun", (44.8444, 20.4124), 1200, 2),
    ("Surčin", (44.7910, 20.2702), 900, 1), ("Dobanovci", (44.8276, 20.2229), 900, 1),
]

_LAT2CYR = [("lj", "љ"), ("nj", "њ"), ("dž", "џ"), ("Lj", "Љ"), ("Nj", "Њ"), ("Dž", "Џ")] + \
    list(zip("abvgdđežzijklmnoprstćufhcčšABVGDĐEŽZIJKLMNOPRSTĆUFHCČŠ",
             "абвгдђежзијклмнопрстћуфхцчшАБВГДЂЕЖЗИЈКЛМНОПРСТЋУФХЦЧШ"))


def to_cyrillic(text):
    for lat, cyr in _LAT2CYR:
        text = text.replace(lat, cyr)
    return text


def lazy(text):
    """How people type without a Serbian keyboard: Đ->D, č/ć->c, š->s, ž->z."""
    return (text.replace("Đ", "D").replace("đ", "d").replace("č", "c").replace("ć", "c")
            .replace("š", "s").replace("ž", "z").replace("Č", "C").replace("Ć", "C")
            .replace("Š", "S").replace("Ž", "Z"))


MESSY = [
    ("bez kvačica", lambda st, hn: f"{lazy(st)} {hn}"),
    ("ćirilica", lambda st, hn: f"{to_cyrillic(st)} {hn}"),
    ("ul. i mala slova", lambda st, hn: f"ul. {st.lower()} {hn}"),
    ("stan posle broja", lambda st, hn: f"{st} {hn}/{random.randint(2, 40)}"),
    ("stan i sprat", lambda st, hn: f"{st} {hn}, stan {random.randint(1, 60)}, sprat {random.randint(1, 9)}"),
    ("skraćenica", lambda st, hn: f"{st.replace('Bulevar', 'Bul.').replace('Jurija ', 'J. ')} {hn}"),
]


def sample(db, areas, rng, used):
    out = []
    for place, (lat, lon), radius, count in areas:
        dlat = radius / 111000
        dlon = radius / (111000 * math.cos(math.radians(lat)))
        rows = db.execute(
            "SELECT street, hn, lat, lon, osm FROM addr WHERE building=1 AND lat BETWEEN ? AND ? "
            "AND lon BETWEEN ? AND ? AND hn GLOB '[0-9]*' AND street NOT GLOB '*[0-9]*'",
            (lat - dlat, lat + dlat, lon - dlon, lon + dlon)).fetchall()
        rng.shuffle(rows)
        got = 0
        for street, hn, la, lo, osm in rows:
            if osm in used or len(street) > 32:
                continue
            used.add(osm)
            out.append({"street": street, "hn": hn, "mesto": place, "lat": la, "lon": lo})
            got += 1
            if got == count:
                break
    return out


def person(rng):
    return f"{rng.choice(FIRST)} {rng.choice(LAST_INITIAL)}."


def build(name, prefix, areas, extra_same_building, n_windows, n_messy, bad, rng, db, used):
    buildings = sample(db, areas, rng, used)
    pk = []
    for b in buildings:
        pk.append(dict(b, adresa=f"{b['street']} {b['hn']}", how="tačno"))
    # several packages for the same building (big residential blocks)
    for b in rng.sample(buildings, min(extra_same_building, len(buildings))):
        pk.append(dict(b, adresa=f"{b['street']} {b['hn']}", how="ista zgrada"))
    rng.shuffle(pk)
    for p in rng.sample(pk, n_windows):
        p["od"], p["do"] = rng.choice(WINDOWS)
    for k, p in enumerate(rng.sample([p for p in pk if p["how"] == "tačno"], n_messy)):
        label, fn = MESSY[k % len(MESSY)]       # every kind of mess at least once
        p["adresa"], p["how"] = fn(p["street"], p["hn"]), label
    for kind in bad:
        p = rng.choice([p for p in pk if p["how"] == "tačno"])
        if kind == "nepostojeća ulica":
            p["adresa"] = f"Izmišljena {rng.randint(3, 40)}"
        elif kind == "nepostojeći broj":
            p["adresa"] = f"{p['street']} {int(''.join(c for c in p['hn'] if c.isdigit())) + 900}"
        p["how"] = kind
    rows = []
    for i, p in enumerate(pk, 1):
        rows.append({"id": f"{prefix}-{i:03d}", "ime": person(rng), "tel": f"TEST-{i:04d}",
                     "adresa": p["adresa"], "mesto": p["mesto"], "od": p.get("od", ""),
                     "do": p.get("do", ""), "napomena": "", "_how": p["how"]})
    return rows


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["id", "ime", "tel", "adresa", "mesto", "od", "do", "napomena"])
        w.writeheader()
        for r in rows:
            w.writerow({k: v for k, v in r.items() if not k.startswith("_")})


def qr_payload(r):
    d = {"v": 1, "id": r["id"], "ime": r["ime"], "tel": r["tel"], "adresa": r["adresa"],
         "mesto": r["mesto"]}
    if r["od"]:
        d["od"] = r["od"]
    if r["do"]:
        d["do"] = r["do"]
    # ASCII only ("\u0161" instead of "š"): QR byte mode has no reliable charset
    # signalling, and some decoders (ZXing-js in the web scanner) guess wrong for UTF-8
    return json.dumps(d, ensure_ascii=True, separators=(",", ":"))


def write_labels(path, title, rows):
    e = html.escape
    cards = []
    for r in rows:
        # error level L keeps the code sparse enough to scan off a monitor
        svg = segno.make(qr_payload(r), error="l").svg_inline(scale=4, border=2, dark="#000")
        win = ""
        if r["od"] or r["do"]:
            win = f"<div class='win'>⏱ {e(r['od'] or '…')}–{e(r['do'] or '…')}</div>"
        cards.append(
            f"<article class='label' data-id='{e(r['id'])}'><div class='qr'>{svg}</div>"
            f"<div class='txt'><div class='id'>{e(r['id'])}</div><div class='name'>{e(r['ime'])}</div>"
            f"<div class='addr'>{e(r['adresa'])}</div><div class='place'>{e(r['mesto'])}</div>"
            f"{win}<div class='case'>{e(r['_how'])}</div></div></article>")
    path.write_text(f"""<!doctype html><html lang="sr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)}</title>
<style>
:root{{--bg:#eef0f3;--card:#fff;--ink:#121418;--muted:#5b6370;--acc:#0b6bcb}}
*{{box-sizing:border-box}}
body{{margin:0;font:14px/1.35 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--ink)}}
header{{padding:16px;display:flex;gap:12px;flex-wrap:wrap;align-items:center;justify-content:space-between}}
h1{{font-size:18px;margin:0}} .muted{{color:var(--muted)}}
button{{font:inherit;padding:8px 14px;border-radius:8px;border:1px solid #c9ced6;background:#fff;cursor:pointer}}
button.on{{background:var(--acc);color:#fff;border-color:var(--acc)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(370px,1fr));gap:10px;padding:0 16px 24px}}
.label{{background:var(--card);border:1.5px dashed #9aa3ae;border-radius:10px;padding:10px;display:flex;gap:10px;align-items:center;break-inside:avoid}}
.qr svg{{display:block;width:190px;height:190px}}
.id{{font-weight:800;font-size:18px;letter-spacing:.02em}} .name{{font-weight:600}}
.addr{{font-size:15px}} .place{{color:var(--muted)}}
.win{{margin-top:4px;font-weight:700;color:#a14b00}}
.case{{margin-top:6px;font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}}
.one .grid{{display:block;max-width:560px;margin:0 auto}}
.one .label{{display:none;flex-direction:column;text-align:center;padding:24px}}
.one .label.cur{{display:flex}} .one .qr svg{{width:min(78vw,420px);height:min(78vw,420px)}}
.one .id{{font-size:28px}} .one .addr{{font-size:20px}}
#nav{{display:none;gap:8px;justify-content:center;padding:0 16px 16px}} .one #nav{{display:flex}}
@media print{{header,#nav{{display:none}} body{{background:#fff}} .grid{{grid-template-columns:repeat(2,1fr);padding:0}} .label{{border-color:#000}}}}
</style></head><body>
<header><div><h1>{e(title)}</h1><div class="muted">TEST etikete · izmišljena imena · telefon TEST-xxxx se ne može pozvati · adrese: © OpenStreetMap</div></div>
<div><button id="bGrid" class="on">Mreža</button> <button id="bOne">Jedan po jedan (za skeniranje sa ekrana)</button> <button onclick="print()">Štampaj</button></div></header>
<div id="nav"><button id="prev">◀ Prethodni</button><span id="pos" class="muted" style="align-self:center"></span><button id="next">Sledeći ▶</button></div>
<main class="grid">{''.join(cards)}</main>
<script>
const L=[...document.querySelectorAll('.label')];let i=0;
function show(){{L.forEach((x,k)=>x.classList.toggle('cur',k===i));document.getElementById('pos').textContent=(i+1)+' / '+L.length;}}
bOne.onclick=()=>{{document.body.classList.add('one');bOne.classList.add('on');bGrid.classList.remove('on');show();}};
bGrid.onclick=()=>{{document.body.classList.remove('one');bGrid.classList.add('on');bOne.classList.remove('on');}};
prev.onclick=()=>{{i=(i-1+L.length)%L.length;show();}};next.onclick=()=>{{i=(i+1)%L.length;show();}};
document.addEventListener('keydown',ev=>{{if(!document.body.classList.contains('one'))return;if(ev.key==='ArrowRight'||ev.key===' ')next.onclick();if(ev.key==='ArrowLeft')prev.onclick();}});
</script></body></html>""", encoding="utf-8")


def main():
    OUT.mkdir(exist_ok=True)
    db = sqlite3.connect(DB)
    rng = random.Random(2611)
    random.seed(2611)
    used = set()
    sets = [
        ("rejon-nbg-zemun-100", "PK", REJON, 40, 18, 12, ["nepostojeća ulica", "nepostojeći broj"],
         "Rejon Novi Beograd + Zemun + okolina: 100 paketa"),
        ("ceo-grad-30", "GR", GRAD, 0, 6, 3, [], "Ceo Beograd + okolina: 30 paketa"),
    ]
    for name, prefix, areas, same, n_win, n_messy, bad, title in sets:
        rows = build(name, prefix, areas, same, n_win, n_messy, bad, rng, db, used)
        if name.endswith("-100"):
            rows = rows[:100]
        write_csv(OUT / f"{name}.csv", rows)
        write_labels(OUT / f"{name}-etikete.html", title, rows)
        cases = {}
        for r in rows:
            cases[r["_how"]] = cases.get(r["_how"], 0) + 1
        print(f"{name}: {len(rows)} paketa, slučajevi {cases}, "
              f"sa prozorom {sum(1 for r in rows if r['od'] or r['do'])}")
    (OUT / "README.md").write_text(
        "# Test podaci\n\n"
        "Generisano sa `backend/tools/make_test_data.py` iz lokalnog OSM registra adresa.\n\n"
        "- `*.csv`: učitaj u aplikaciju (Paketi → Uvezi CSV) ili preko `/api/demo/<ime>`\n"
        "- `*-etikete.html`: QR etikete. Otvori na računaru i skeniraj telefonom, "
        "  mod *Jedan po jedan* prikazuje veliki QR.\n\n"
        "Adrese su prave zgrade (© OpenStreetMap contributors, ODbL). Imena su izmišljena, a "
        "telefon `TEST-xxxx` se namerno ne može pozvati.\n\n"
        "Kolona *slučaj* na etiketi pokazuje šta se testira: tačna adresa, ista zgrada, "
        "ćirilica, bez kvačica, stan posle broja, skraćenica, nepostojeća ulica/broj.\n",
        encoding="utf-8")


if __name__ == "__main__":
    main()
