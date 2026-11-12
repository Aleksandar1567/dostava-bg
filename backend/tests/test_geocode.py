"""Runs against the real OSM index (skipped if it has not been built)."""

from pathlib import Path

import pytest

DB = Path(__file__).resolve().parents[1] / "data" / "addresses.sqlite"
pytestmark = pytest.mark.skipif(not DB.exists(), reason="run tools/build_address_index.py")


@pytest.fixture(scope="module")
def g():
    from app.geocode import Geocoder
    return Geocoder(DB)


@pytest.mark.parametrize("addr,place,label,conf", [
    ("Đorđa Stanojevića 14", "Novi Beograd", "Đorđa Stanojevića 14", "ok"),
    ("Dorda Stanojevica 14", "", "Đorđa Stanojevića 14", "ok"),              # no diacritics
    ("Ђорђа Станојевића 14", "", "Đorđa Stanojevića 14", "ok"),              # Cyrillic
    ("Omladinskih brigada 31 A", "", "Omladinskih brigada 31a", "ok"),
    ("Bulevar AVNOJ-a 100", "", "Bulevar Zorana Đinđića 100", "ok"),          # old name
    ("Knez Mihailova 6", "Beograd", "Kneza Mihaila 6", "ok"),                 # colloquial
    ("Glavna 10", "Zemun", "Glavna 10", "ok"),                                # same name elsewhere
    ("Kneza Miloša 7", "Stari grad", "Kneza Miloša 7", "ok"),
    ("Gandijeva 76a", "", "Gandijeva 76", "approx"),                         # letter dropped
    ("Jurija Gagarina 9999", "", "Jurija Gagarina 277", "approx"),           # nearest number
])
def test_known(g, addr, place, label, conf):
    r = g.geocode(addr, place)
    assert (r.label, r.confidence) == (label, conf), r.note


def test_ambiguous_without_place_is_flagged(g):
    r = g.geocode("Glavna 10")
    assert r.confidence == "approx" and "više mesta" in r.note


def test_unknown_street_and_bb_need_a_pin(g):
    assert g.geocode("Izmišljena 12").confidence == "none"
    r = g.geocode("Nehruova bb")
    assert r.confidence == "none" and r.lat is None and r.alternatives[0]["hint"]
