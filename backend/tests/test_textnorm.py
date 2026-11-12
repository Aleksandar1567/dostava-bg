import pytest

from app.textnorm import fold, norm_housenumber, norm_street, parse_address, translit


def test_translit_and_fold():
    assert translit("Ђорђа Станојевића") == "Đorđa Stanojevića"
    assert translit("ЉУБЉАНА") == "LJUBLJANA"
    assert translit("Џорџа Вашингтона") == "Džordža Vašingtona"
    assert fold("Đorđa Stanojevića") == "djordja stanojevica"


@pytest.mark.parametrize("a,b", [
    ("Bul. kralja Aleksandra", "bulevar kralja aleksandra"),
    ("ul. Kneza Miloša", "kneza milosa"),
    ("27. марта", "dvadeset sedmog marta"),
    ("Dvadeset sedmog marta", "dvadeset sedmog marta"),
    ("29. novembra", "dvadeset devetog novembra"),
    ("Ulica 27.", "27"),
])
def test_norm_street(a, b):
    assert norm_street(a) == b


@pytest.mark.parametrize("addr,street,hn,rest", [
    ("Bulevar Mihajla Pupina 10/25, stan 12", "Bulevar Mihajla Pupina", "10/25", ""),
    ("27. marta 12", "27. marta", "12", ""),
    ("Nehruova bb", "Nehruova", "bb", ""),
    ("Nehruova b.b.", "Nehruova", "bb", ""),
    ("Pupina 10, Novi Beograd", "Pupina", "10", "Novi Beograd"),
    ("Omladinskih brigada 31 A", "Omladinskih brigada", "31 A", ""),
    ("Ustanička 128a, ulaz 2", "Ustanička", "128a", ""),
    ("Булевар ослобођења 7/II", "Bulevar oslobođenja", "7", ""),
    ("Gandijeva 9 sprat 3", "Gandijeva", "9", ""),
    ("Marka Čelebonovića 10ž", "Marka Čelebonovića", "10ž", ""),
    ("Trg Republike", "Trg Republike", "", ""),
])
def test_parse_address(addr, street, hn, rest):
    assert parse_address(addr) == (street, hn, rest)


@pytest.mark.parametrize("a,b", [("31 A", "31a"), ("31/a", "31a"), ("123-a", "123a"),
                                 ("128а", "128a"), ("10đ", "10dj"), ("10/3", "10/3")])
def test_norm_housenumber(a, b):
    assert norm_housenumber(a) == b
