"""Text normalization for the Azerbaijani used-car market.

Listings in this market arrive in mixed script and mixed language: Azerbaijani
Latin, Russian Cyrillic, and English, often within a single listing. "Мерседес",
"Mercedes" and "Mersedes" denote one make. Naive ``str.lower()`` treats them as
three, which silently fragments every comparable set that touches them.

This module is deliberately data-driven. The tables below are *seed* mappings
covering the high-frequency vocabulary of local listings; they are meant to grow
from observed ingestion. :func:`unmapped_tokens` exists so the ingestion
pipeline can report vocabulary it could not canonicalize, turning table gaps
into a visible, fixable metric rather than silent misclassification.

Nothing here invents market data. These are linguistic mappings only.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.domain.enums import (
    BodyStyle,
    Drivetrain,
    FuelType,
    SellerType,
    Transmission,
)

# --- Character folding -----------------------------------------------------

# Azerbaijani and Turkish letters, plus the dotted/dotless I pair that breaks
# naive casing. Applied before case folding so that "İ" and "ı" both land on
# "I" rather than on Python's default combining-dot expansion.
_CHAR_FOLD = str.maketrans(
    {
        "ə": "e", "Ə": "e",
        "ı": "i", "I": "i", "İ": "i", "i": "i",
        "ğ": "g", "Ğ": "g",
        "ş": "s", "Ş": "s",
        "ç": "c", "Ç": "c",
        "ö": "o", "Ö": "o",
        "ü": "u", "Ü": "u",
        "â": "a", "Â": "a",
        "é": "e", "É": "e",
        "ń": "n",
    }
)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_WHITESPACE = re.compile(r"\s+")


def fold(text: str | None) -> str:
    """Reduce a free-text token to a comparison key.

    Case-insensitive, diacritic-insensitive, punctuation-insensitive. Used as
    the lookup key for every synonym table in this module.

        >>> fold("Ön ötürücü")
        'on oturucu'
        >>> fold("Mercedes-Benz")
        'mercedes benz'
    """
    if not text:
        return ""
    folded = text.translate(_CHAR_FOLD).lower()
    folded = _NON_ALNUM.sub(" ", folded)
    return _WHITESPACE.sub(" ", folded).strip()


def _cyrillic_key(text: str) -> str:
    """Lowercased, punctuation-stripped key preserving Cyrillic characters."""
    lowered = text.lower()
    lowered = re.sub(r"[^\w\s]+", " ", lowered, flags=re.UNICODE)
    return _WHITESPACE.sub(" ", lowered).strip()


def _key(text: str) -> str:
    """Lookup key that works for both Latin and Cyrillic input."""
    cyr = _cyrillic_key(text)
    if re.search(r"[Ѐ-ӿ]", cyr):
        return cyr
    return fold(text)


# --- Makes -----------------------------------------------------------------

# Canonical make -> accepted variants (Latin aliases, Cyrillic forms, common
# local misspellings). Canonical names follow common manufacturer spelling.
_MAKE_VARIANTS: dict[str, tuple[str, ...]] = {
    "MERCEDES-BENZ": ("mercedes", "mercedes benz", "mersedes", "merc", "benz", "mb",
                      "мерседес", "мерседес бенц", "мерс"),
    "BMW": ("bmw", "bumer", "бмв", "бэмвэ"),
    "TOYOTA": ("toyota", "toyata", "тойота"),
    "LEXUS": ("lexus", "лексус"),
    "HYUNDAI": ("hyundai", "hunday", "hyndai", "хендай", "хундай", "хёндэ"),
    "KIA": ("kia", "киа", "кия"),
    "NISSAN": ("nissan", "nisan", "ниссан"),
    "HONDA": ("honda", "хонда"),
    "VOLKSWAGEN": ("volkswagen", "vw", "folksvagen", "фольксваген", "вольксваген"),
    "AUDI": ("audi", "ауди"),
    "OPEL": ("opel", "опель"),
    "FORD": ("ford", "форд"),
    "CHEVROLET": ("chevrolet", "chevy", "шевроле"),
    "MITSUBISHI": ("mitsubishi", "митсубиси", "мицубиси"),
    "MAZDA": ("mazda", "мазда"),
    "SUBARU": ("subaru", "субару"),
    "SKODA": ("skoda", "шкода"),
    "RENAULT": ("renault", "reno", "рено"),
    "PEUGEOT": ("peugeot", "пежо"),
    "CITROEN": ("citroen", "ситроен"),
    "FIAT": ("fiat", "фиат"),
    "VOLVO": ("volvo", "вольво"),
    "LAND ROVER": ("land rover", "landrover", "range rover", "rangerover",
                   "ленд ровер", "рендж ровер"),
    "JEEP": ("jeep", "джип"),
    "PORSCHE": ("porsche", "порше"),
    "MINI": ("mini", "мини"),
    "INFINITI": ("infiniti", "инфинити"),
    "ACURA": ("acura", "акура"),
    "SSANGYONG": ("ssangyong", "ссангйонг", "санг йонг"),
    "SUZUKI": ("suzuki", "сузуки"),
    "DAEWOO": ("daewoo", "дэу", "деу"),
    "LADA": ("lada", "vaz", "ваз", "лада", "жигули"),
    "GAZ": ("gaz", "газ"),
    "UAZ": ("uaz", "уаз"),
    "TESLA": ("tesla", "тесла"),
    "BYD": ("byd", "бид"),
    "CHERY": ("chery", "чери"),
    "GEELY": ("geely", "джили", "джили"),
    "HAVAL": ("haval", "хавал"),
    "MG": ("mg", "мг"),
    "DACIA": ("dacia", "дачия"),
    "SEAT": ("seat", "сеат"),
    "JAGUAR": ("jaguar", "ягуар"),
    "CADILLAC": ("cadillac", "кадиллак"),
    "CHRYSLER": ("chrysler", "крайслер"),
    "DODGE": ("dodge", "додж"),
    "GMC": ("gmc",),
    "GENESIS": ("genesis", "генезис"),
    "ISUZU": ("isuzu", "исузу"),
    "IVECO": ("iveco",),
    "MAN": ("man",),
}

_MAKE_LOOKUP: dict[str, str] = {}
for _canonical, _variants in _MAKE_VARIANTS.items():
    _MAKE_LOOKUP[_key(_canonical)] = _canonical
    for _variant in _variants:
        _MAKE_LOOKUP[_key(_variant)] = _canonical


def normalize_make(raw: str | None) -> str | None:
    """Canonicalize a manufacturer name. Returns ``None`` if unrecognized.

    Returning ``None`` rather than echoing the input is deliberate: an
    unrecognized make must reach :func:`unmapped_tokens` and be fixed in the
    table, not quietly become its own pseudo-make and fragment comparables.
    """
    if not raw or not raw.strip():
        return None
    return _MAKE_LOOKUP.get(_key(raw))


# --- Models ----------------------------------------------------------------

# Aliases only where a model is genuinely known by several names. Model text is
# otherwise canonicalized structurally (upper-cased, punctuation collapsed) and
# decomposed by the identity resolver, not by string matching here.
_MODEL_ALIASES: dict[tuple[str, str], str] = {
    ("BMW", "5 er"): "5 SERIES",
    ("BMW", "5er"): "5 SERIES",
    ("BMW", "5 series"): "5 SERIES",
    ("BMW", "5"): "5 SERIES",
    ("BMW", "3 er"): "3 SERIES",
    ("BMW", "3er"): "3 SERIES",
    ("BMW", "3 series"): "3 SERIES",
    ("BMW", "3"): "3 SERIES",
    ("BMW", "7 series"): "7 SERIES",
    ("BMW", "7"): "7 SERIES",
    ("MERCEDES-BENZ", "e klasse"): "E-CLASS",
    ("MERCEDES-BENZ", "e class"): "E-CLASS",
    ("MERCEDES-BENZ", "c klasse"): "C-CLASS",
    ("MERCEDES-BENZ", "c class"): "C-CLASS",
    ("MERCEDES-BENZ", "s klasse"): "S-CLASS",
    ("MERCEDES-BENZ", "s class"): "S-CLASS",
    ("VOLKSWAGEN", "jetta"): "JETTA",
    ("VOLKSWAGEN", "passat cc"): "CC",
    ("LADA", "2107"): "2107",
    ("LADA", "niva"): "NIVA",
}

_TRIM_NOISE = re.compile(
    r"\b(sedan|hetcbek|hatchback|universal|suv|crossover|coupe|kupe|"
    r"avtomat|mexaniki|benzin|dizel|hibrid|full|tam)\b"
)


def normalize_model(make: str | None, raw: str | None) -> str | None:
    """Canonicalize a model name within the context of its make.

    Body-style and drivetrain words that sellers append to model fields are
    stripped, because they belong in their own canonical fields and would
    otherwise create distinct pseudo-models ("Camry Sedan" vs "Camry").
    """
    if not raw or not raw.strip():
        return None
    folded = fold(raw)
    folded = _TRIM_NOISE.sub(" ", folded)
    folded = _WHITESPACE.sub(" ", folded).strip()
    if not folded:
        return None
    if make:
        alias = _MODEL_ALIASES.get((make, folded))
        if alias:
            return alias
    return folded.upper()


# --- Categorical attribute vocabularies ------------------------------------

_FUEL_VARIANTS: dict[FuelType, tuple[str, ...]] = {
    FuelType.PETROL: ("benzin", "petrol", "gasoline", "бензин"),
    FuelType.DIESEL: ("dizel", "diesel", "дизель", "дт"),
    FuelType.HYBRID: ("hibrid", "hybrid", "гибрид"),
    FuelType.PLUGIN_HYBRID: ("plug in hibrid", "plugin hybrid", "plug in hybrid", "phev",
                             "плагин гибрид"),
    FuelType.ELECTRIC: ("elektro", "elektrik", "electric", "ev", "электро",
                        "электрический"),
    FuelType.LPG: ("qaz", "lpg", "benzin qaz", "газ", "пропан"),
    FuelType.CNG: ("cng", "metan", "метан"),
}

_TRANSMISSION_VARIANTS: dict[Transmission, tuple[str, ...]] = {
    Transmission.MANUAL: ("mexaniki", "manual", "mexanika", "механика",
                          "механическая", "mt"),
    Transmission.AUTOMATIC: ("avtomat", "automatic", "auto", "автомат",
                             "автоматическая", "at"),
    Transmission.CVT: ("variator", "cvt", "вариатор"),
    Transmission.DCT: ("dsg", "dct", "pdk", "s tronic", "dual clutch"),
    Transmission.AMT: ("robotlasdirilmis", "robot", "amt", "робот",
                       "роботизированная"),
}

_DRIVETRAIN_VARIANTS: dict[Drivetrain, tuple[str, ...]] = {
    Drivetrain.FWD: ("on", "on oturucu", "front", "fwd", "передний",
                     "передний привод"),
    Drivetrain.RWD: ("arxa", "arxa oturucu", "rear", "rwd", "задний",
                     "задний привод"),
    Drivetrain.AWD: ("tam", "tam oturucu", "awd", "4wd", "4x4", "quattro",
                     "xdrive", "4matic", "полный", "полный привод"),
}

_BODY_VARIANTS: dict[BodyStyle, tuple[str, ...]] = {
    BodyStyle.SEDAN: ("sedan", "седан"),
    BodyStyle.HATCHBACK: ("hetcbek", "hatchback", "хэтчбек", "хетчбек"),
    BodyStyle.LIFTBACK: ("liftbek", "liftback", "лифтбек"),
    BodyStyle.WAGON: ("universal", "wagon", "estate", "универсал"),
    BodyStyle.SUV: ("offroader", "off roader", "suv", "cip", "jeep", "внедорожник"),
    BodyStyle.CROSSOVER: ("krossover", "crossover", "кроссовер"),
    BodyStyle.COUPE: ("kupe", "coupe", "купе"),
    BodyStyle.CONVERTIBLE: ("kabriolet", "convertible", "roadster", "rodster",
                            "кабриолет", "родстер"),
    BodyStyle.PICKUP: ("pikap", "pickup", "пикап"),
    BodyStyle.MINIVAN: ("miniven", "minivan", "минивэн"),
    BodyStyle.VAN: ("furqon", "van", "mikroavtobus", "фургон", "микроавтобус"),
}

_SELLER_VARIANTS: dict[SellerType, tuple[str, ...]] = {
    SellerType.DEALER: ("diler", "dealer", "salon", "avtosalon", "дилер",
                        "автосалон", "салон",
                        # Badge an authorised dealer carries on its listings.
                        "resmi numayende", "официальный представитель"),
    SellerType.PRIVATE: ("sexsi", "private", "owner", "sahibi", "частное",
                         "частник", "собственник"),
    SellerType.IMPORTER: ("idxalci", "importer", "import", "импортер"),
}


def _build_lookup(variants: dict[object, tuple[str, ...]]) -> dict[str, object]:
    table: dict[str, object] = {}
    for member, aliases in variants.items():
        for alias in aliases:
            table[_key(alias)] = member
    return table


_FUEL_LOOKUP = _build_lookup(_FUEL_VARIANTS)  # type: ignore[arg-type]
_TRANSMISSION_LOOKUP = _build_lookup(_TRANSMISSION_VARIANTS)  # type: ignore[arg-type]
_DRIVETRAIN_LOOKUP = _build_lookup(_DRIVETRAIN_VARIANTS)  # type: ignore[arg-type]
#: Which style wins when one description names two. Turbo.az writes "SUV Kupe"
#: for a coupe-profile SUV: the platform decides what it should be compared
#: against, the roofline does not, so the structural term is read first.
_BODY_PRECEDENCE: tuple[BodyStyle, ...] = (
    BodyStyle.MINIVAN,
    BodyStyle.VAN,
    BodyStyle.PICKUP,
    BodyStyle.SUV,
    BodyStyle.CROSSOVER,
    BodyStyle.WAGON,
    BodyStyle.LIFTBACK,
    BodyStyle.HATCHBACK,
    BodyStyle.CONVERTIBLE,
    BodyStyle.COUPE,
    BodyStyle.SEDAN,
)

_BODY_LOOKUP = _build_lookup(_BODY_VARIANTS)  # type: ignore[arg-type]
_SELLER_LOOKUP = _build_lookup(_SELLER_VARIANTS)  # type: ignore[arg-type]


def normalize_fuel(raw: str | None) -> FuelType:
    """Map listing fuel text to :class:`FuelType`, defaulting to ``UNKNOWN``."""
    if not raw:
        return FuelType.UNKNOWN
    return _FUEL_LOOKUP.get(_key(raw), FuelType.UNKNOWN)  # type: ignore[return-value]


def normalize_transmission(raw: str | None) -> Transmission:
    if not raw:
        return Transmission.UNKNOWN
    return _TRANSMISSION_LOOKUP.get(_key(raw), Transmission.UNKNOWN)  # type: ignore[return-value]


def normalize_drivetrain(raw: str | None) -> Drivetrain:
    if not raw:
        return Drivetrain.UNKNOWN
    return _DRIVETRAIN_LOOKUP.get(_key(raw), Drivetrain.UNKNOWN)  # type: ignore[return-value]


def normalize_body(raw: str | None) -> BodyStyle:
    """Map listing body text to :class:`BodyStyle`.

    Sources rarely write a bare body style. Turbo.az appends the door count
    ("Hetçbek, 5 qapı"), names two platforms at once ("Offroader / SUV"), or
    qualifies the cabin ("Pikap, ikiqat kabin"). An exact lookup matched none
    of those and sent more than half the observed market to UNKNOWN, where it
    reads as a configuration we could not identify rather than as text we had
    not parsed.

    A bare value still resolves by exact match. Anything else is read token by
    token, since :func:`fold` has already reduced the punctuation between them
    to spaces, and where a description names two styles precedence decides.
    """
    if not raw:
        return BodyStyle.UNKNOWN

    key = _key(raw)
    exact = _BODY_LOOKUP.get(key)
    if exact is not None:
        return exact  # type: ignore[return-value]

    found = {
        style for token in key.split() if (style := _BODY_LOOKUP.get(token)) is not None
    }
    for style in _BODY_PRECEDENCE:
        if style in found:
            return style
    return BodyStyle.UNKNOWN


def normalize_seller_type(raw: str | None) -> SellerType:
    if not raw:
        return SellerType.UNKNOWN
    return _SELLER_LOOKUP.get(_key(raw), SellerType.UNKNOWN)  # type: ignore[return-value]


# --- Geography -------------------------------------------------------------

# Canonical city name -> accepted variants. Geographic segmentation matters
# because a single national average hides real regional spread (spec §15).
_CITY_VARIANTS: dict[str, tuple[str, ...]] = {
    "Bakı": ("baki", "baku", "баку"),
    "Sumqayıt": ("sumqayit", "sumgait", "sumgayit", "сумгаит", "сумгайыт"),
    "Gəncə": ("gence", "ganja", "gyandzha", "гянджа"),
    "Xırdalan": ("xirdalan", "khirdalan", "хырдалан"),
    "Mingəçevir": ("mingecevir", "mingachevir", "мингечевир"),
    "Şirvan": ("sirvan", "shirvan", "ширван"),
    "Naxçıvan": ("naxcivan", "nakhchivan", "нахчыван", "нахичевань"),
    "Lənkəran": ("lenkeran", "lankaran", "ленкорань"),
    "Şəki": ("seki", "sheki", "шеки"),
    "Yevlax": ("yevlax", "yevlakh", "евлах"),
    "Quba": ("quba", "guba", "куба"),
    "Şamaxı": ("samaxi", "shamakhi", "шемаха"),
    "Zaqatala": ("zaqatala", "zagatala", "загатала"),
    "Xaçmaz": ("xacmaz", "khachmaz", "хачмаз"),
    "Salyan": ("salyan", "сальян"),
    "Bərdə": ("berde", "barda", "барда"),
    "Astara": ("astara", "астара"),
    "Masallı": ("masalli", "масаллы"),
    "Ağdam": ("agdam", "агдам"),
    "Ağcabədi": ("agcabedi", "agjabadi", "агджабеди"),
    "Sabirabad": ("sabirabad", "сабирабад"),
    "Göyçay": ("goycay", "goychay", "геокчай"),
    "İmişli": ("imisli", "imishli", "имишли"),
    "Qazax": ("qazax", "gazakh", "казах"),
    "Tovuz": ("tovuz", "товуз"),
    "Şəmkir": ("semkir", "shamkir", "шамкир"),
    "Cəlilabad": ("celilabad", "jalilabad", "джалилабад"),
    "Kürdəmir": ("kurdemir", "kurdamir", "кюрдамир"),
    "Qusar": ("qusar", "gusar", "гусар"),
    "Beyləqan": ("beyleqan", "beylagan", "бейлаган"),
    "Füzuli": ("fuzuli", "физули"),
    "Tərtər": ("terter", "tartar", "тертер"),
    "Goranboy": ("goranboy", "геранбой"),
    "Şabran": ("sabran", "shabran", "шабран"),
    "Siyəzən": ("siyezen", "siyazan", "сиязань"),
    "Ucar": ("ucar", "ujar", "уджар"),
    "Balakən": ("balaken", "balakan", "балакен"),
    "Gədəbəy": ("gedebey", "gadabay", "гедабек"),
}

_CITY_LOOKUP: dict[str, str] = {}
for _canonical_city, _city_variants in _CITY_VARIANTS.items():
    _CITY_LOOKUP[_key(_canonical_city)] = _canonical_city
    for _variant in _city_variants:
        _CITY_LOOKUP[_key(_variant)] = _canonical_city


def normalize_city(raw: str | None) -> str | None:
    """Canonicalize an Azerbaijani city name. ``None`` if unrecognized."""
    if not raw or not raw.strip():
        return None
    return _CITY_LOOKUP.get(_key(raw))


# Coarse market grouping used when a single city has too few comparables to
# stand alone. Baku and its commuter satellites form one trading area in
# practice; the engine widens to this grouping before it widens nationally, and
# reports which level it used.
_METRO_GROUPS: dict[str, str] = {
    "Bakı": "BAKU_METRO",
    "Xırdalan": "BAKU_METRO",
    "Sumqayıt": "BAKU_METRO",
}


def market_region(city: str | None) -> str:
    """Coarse trading area for a canonical city name."""
    if not city:
        return "UNKNOWN"
    return _METRO_GROUPS.get(city, city)


# --- Vocabulary gap reporting ---------------------------------------------

_LOOKUPS: dict[str, dict[str, object]] = {
    "make": _MAKE_LOOKUP,  # type: ignore[dict-item]
    "fuel": _FUEL_LOOKUP,
    "transmission": _TRANSMISSION_LOOKUP,
    "drivetrain": _DRIVETRAIN_LOOKUP,
    "body": _BODY_LOOKUP,
    "seller_type": _SELLER_LOOKUP,
    "city": _CITY_LOOKUP,  # type: ignore[dict-item]
}


def unmapped_tokens(field: str, values: Iterable[str]) -> list[str]:
    """Return the distinct values a field's synonym table does not recognize.

    Ingestion calls this to emit a data-quality metric. A rising unmapped rate
    means the market's vocabulary has drifted past our tables, which is a
    fixable data problem — as opposed to a silent misclassification, which is
    not visible at all.
    """
    table = _LOOKUPS.get(field)
    if table is None:
        raise KeyError(f"no synonym table for field {field!r}")
    seen: dict[str, None] = {}
    for value in values:
        if not value or not value.strip():
            continue
        if _key(value) not in table:
            seen.setdefault(value.strip(), None)
    return list(seen)


def known_makes() -> list[str]:
    """Canonical makes currently recognized, for UI dropdowns and validation."""
    return sorted(_MAKE_VARIANTS)


def known_cities() -> list[str]:
    """Canonical city names currently recognized."""
    return sorted(_CITY_VARIANTS)
