"""Serbian text normalization: Cyrillic -> Latin, ASCII folding, street and
house-number parsing.

Everything that compares addresses goes through `fold()` so that
"Ђорђа Станојевића", "Đorđa Stanojevića" and "Djordja Stanojevica" end up as
the same key.
"""

import re
import unicodedata

_CYR2LAT = {
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Ђ": "Đ", "Е": "E",
    "Ж": "Ž", "З": "Z", "И": "I", "Ј": "J", "К": "K", "Л": "L", "Љ": "Lj",
    "М": "M", "Н": "N", "Њ": "Nj", "О": "O", "П": "P", "Р": "R", "С": "S",
    "Т": "T", "Ћ": "Ć", "У": "U", "Ф": "F", "Х": "H", "Ц": "C", "Ч": "Č",
    "Џ": "Dž", "Ш": "Š",
}
_CYR2LAT.update({k.lower(): v.lower() for k, v in _CYR2LAT.items()})

_FOLD = str.maketrans({
    "đ": "dj", "Đ": "Dj", "č": "c", "Č": "C", "ć": "c", "Ć": "C",
    "š": "s", "Š": "S", "ž": "z", "Ž": "Z",
})


def translit(text: str) -> str:
    """Serbian Cyrillic to Latin, keeping diacritics (for display)."""
    out = []
    for i, ch in enumerate(text):
        lat = _CYR2LAT.get(ch)
        if lat is None:
            out.append(ch)
        elif len(lat) == 2 and ch.isupper():
            nxt = text[i + 1] if i + 1 < len(text) else ""
            out.append(lat.upper() if nxt.isupper() else lat)
        else:
            out.append(lat)
    return "".join(out)


def fold(text: str) -> str:
    """Lowercase ASCII key: translit, drop diacritics, collapse whitespace."""
    text = translit(text).translate(_FOLD)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text.lower()).strip()


# Abbreviations people actually type on Belgrade delivery forms.
_ABBREV = {
    "bul": "bulevar", "bulev": "bulevar", "bulv": "bulevar", "blvd": "bulevar",
    "kr": "kralja", "vojv": "vojvode", "voj": "vojvode", "gen": "generala",
    "kn": "kneza", "kneg": "kneginje", "sv": "svetog", "mar": "marsala",
}
_DROP = {"ul", "ulica", "br", "broj"}

# RGZ (and therefore OSM) spells dates out: "27. marta" is "Dvadeset sedmog marta".
_ORD = ["", "prvog", "drugog", "treceg", "cetvrtog", "petog", "sestog", "sedmog", "osmog",
        "devetog", "desetog", "jedanaestog", "dvanaestog", "trinaestog", "cetrnaestog",
        "petnaestog", "sesnaestog", "sedamnaestog", "osamnaestog", "devetnaestog", "dvadesetog"]
_MONTHS = {"januara", "februara", "marta", "aprila", "maja", "juna", "jula", "avgusta",
           "septembra", "oktobra", "novembra", "decembra"}


def _ordinal(n: int) -> str:
    if n <= 20:
        return _ORD[n]
    if n < 30:
        return "dvadeset " + _ORD[n - 20]
    return "tridesetog" if n == 30 else "trideset prvog"


def norm_street(street: str) -> str:
    """Comparable street key: folded, punctuation removed, abbreviations
    expanded, dates spelled out the way the official register writes them."""
    s = fold(street)
    s = re.sub(r"[.,;:\"'()/\\\-]+", " ", s)
    raw = [t for t in s.split() if t not in _DROP]
    tokens = []
    for i, tok in enumerate(raw):
        nxt = raw[i + 1] if i + 1 < len(raw) else ""
        if tok.isdigit() and nxt in _MONTHS and 1 <= int(tok) <= 31:
            tokens.append(_ordinal(int(tok)))
        else:
            tokens.append(_ABBREV.get(tok, tok))
    return " ".join(tokens)


# Colloquial / pre-2004 names Belgraders still use, mapped to the register name.
MANUAL_ALIASES = {
    "knez mihailova": "kneza mihaila",
    "bulevar revolucije": "bulevar kralja aleksandra",
    "bulevar avnoja": "bulevar zorana djindjica",
    "bulevar lenjina": "bulevar mihajla pupina",
    "lenjinov bulevar": "bulevar mihajla pupina",
    "dvadeset devetog novembra": "bulevar despota stefana",
}


def norm_housenumber(hn: str) -> str:
    """'31 A' / '31/a' / '31-a' / '128а' (Cyrillic) -> '31a' / '128a'.

    A numeric part after the slash ('10/3') is kept, because some Belgrade
    buildings really are numbered like that; callers fall back to the base
    number when the full form is not found.
    """
    h = fold(hn).replace(" ", "")
    h = re.sub(r"^(\d+)[/\-]([a-z])$", r"\1\2", h)
    return h


def base_number(hn_norm: str) -> str:
    """'10/3' -> '10', '31a' -> '31'."""
    m = re.match(r"\d+", hn_norm)
    return m.group(0) if m else hn_norm


# Everything after these words is apartment/floor/entrance info: the courier
# only needs the building.
_SUFFIX_RE = re.compile(
    r"[,\s]+(stan|st|sprat|spr|ulaz|lamela|apt|apartman|interfon|kod)\b.*$",
    re.IGNORECASE,
)
# House number: '10', '10a', '10 A', '10/25', '31-a', '7/II', 'bb'.
# Serbian Latin letters appear in house numbers too: 10đ, 10ž, 4č
_L = "[a-zčćžšđ]"
_HN = rf"(?:b\.?\s?b\.?|\d+\s?{_L}?(?:\s?[/\-]\s?(?:\d+{_L}?|[ivx]+|{_L}))?)"
_HN_END_RE = re.compile(rf"^(?P<street>.*?\D)[\s,]+(?P<hn>{_HN})\s*\.?$", re.IGNORECASE)
_HN_MID_RE = re.compile(
    rf"^(?P<street>.*?\D)[\s,]+(?P<hn>{_HN})\b[\s,]+(?P<rest>[^\d]+)$", re.IGNORECASE
)


def parse_address(address: str) -> tuple[str, str, str]:
    """Split a free-form address into (street, house_number, trailing_text).

    'Bulevar Mihajla Pupina 10/25, stan 12' -> ('Bulevar Mihajla Pupina', '10/25', '')
    '27. marta 12'                         -> ('27. marta', '12', '')
    'Nehruova bb'                          -> ('Nehruova', 'bb', '')
    'Pupina 10, Novi Beograd'              -> ('Pupina', '10', 'Novi Beograd')
    House number is '' when none was found. trailing_text is usually a place
    name and is used as a place hint.
    """
    a = _SUFFIX_RE.sub("", translit(address).strip()).strip(" ,")
    m = _HN_END_RE.match(a)
    rest = ""
    if not m:
        m = _HN_MID_RE.match(a)
        if not m:
            return a, "", ""
        rest = m.group("rest").strip(" ,")
    street = m.group("street").strip(" ,")
    hn = m.group("hn").strip()
    if re.fullmatch(r"b\.?\s?b\.?", hn, re.IGNORECASE):
        return street, "bb", rest
    # Roman-numeral floor after a slash ('7/II') is not part of the number.
    hn = re.sub(r"\s?[/\-]\s?[ivx]+$", "", hn, flags=re.IGNORECASE)
    return street, hn, rest
