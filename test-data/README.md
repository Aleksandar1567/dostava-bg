# Test podaci

Generisano sa `backend/tools/make_test_data.py` iz lokalnog OSM registra adresa.

- `*.csv`: učitaj u aplikaciju (Paketi → Uvezi CSV) ili preko `/api/demo/<ime>`
- `*-etikete.html`: QR etikete. Otvori na računaru i skeniraj telefonom,   mod *Jedan po jedan* prikazuje veliki QR.

Adrese su prave zgrade (© OpenStreetMap contributors, ODbL). Imena su izmišljena, a telefon `TEST-xxxx` se namerno ne može pozvati.

Kolona *slučaj* na etiketi pokazuje šta se testira: tačna adresa, ista zgrada, ćirilica, bez kvačica, stan posle broja, skraćenica, nepostojeća ulica/broj.
