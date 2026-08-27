"""Vehicle identity resolution — the load-bearing wall (audit §6).

If identity is wrong, every downstream number is quietly wrong in a way that
still looks plausible, so these tests are deliberately paranoid.
"""

from __future__ import annotations

import pytest

from app.domain.enums import Drivetrain, FuelType, Transmission
from app.domain.identity import (
    KeyLevel,
    VehicleConfiguration,
    normalize_displacement,
)
from app.domain.normalization import (
    fold,
    known_makes,
    market_region,
    normalize_body,
    normalize_city,
    normalize_drivetrain,
    normalize_fuel,
    normalize_make,
    normalize_model,
    normalize_transmission,
    unmapped_tokens,
)


class TestFolding:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Ön ötürücü", "on oturucu"),
            ("Gəncə", "gence"),
            ("Bakı", "baki"),
            ("Mercedes-Benz", "mercedes benz"),
            ("ŞƏKİ", "seki"),
            ("  spaced   out  ", "spaced out"),
        ],
    )
    def test_folds_azerbaijani_characters(self, raw: str, expected: str) -> None:
        assert fold(raw) == expected

    def test_dotted_and_dotless_i_both_fold_to_i(self) -> None:
        """Python's default casing turns İ into i+combining dot, which breaks
        naive matching. Both forms must land on the same key."""
        assert fold("İmişli") == fold("imisli")

    def test_empty_input(self) -> None:
        assert fold("") == ""
        assert fold(None) == ""


class TestMakeNormalization:
    @pytest.mark.parametrize(
        "variant",
        ["Mercedes", "mercedes-benz", "Mersedes", "Мерседес", "MB", "benz"],
    )
    def test_mercedes_variants_converge(self, variant: str) -> None:
        assert normalize_make(variant) == "MERCEDES-BENZ"

    @pytest.mark.parametrize("variant", ["BMW", "бмв", "bmw", "  BMW  "])
    def test_bmw_variants_converge(self, variant: str) -> None:
        assert normalize_make(variant) == "BMW"

    def test_unknown_make_returns_none(self) -> None:
        """Unrecognized makes must not become their own pseudo-make.

        Echoing the raw string would silently fragment comparable sets; returning
        None routes it to the unmapped-token metric where it can be fixed.
        """
        assert normalize_make("Definitely Not A Car Brand") is None

    def test_blank_input(self) -> None:
        assert normalize_make("") is None
        assert normalize_make(None) is None

    def test_known_makes_are_sorted_and_nonempty(self) -> None:
        makes = known_makes()
        assert makes == sorted(makes)
        assert "BMW" in makes


class TestAttributeNormalization:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Benzin", FuelType.PETROL),
            ("бензин", FuelType.PETROL),
            ("Dizel", FuelType.DIESEL),
            ("Hibrid", FuelType.HYBRID),
            ("Elektro", FuelType.ELECTRIC),
            ("Qaz", FuelType.LPG),
            ("nonsense", FuelType.UNKNOWN),
        ],
    )
    def test_fuel(self, raw: str, expected: FuelType) -> None:
        assert normalize_fuel(raw) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Avtomat", Transmission.AUTOMATIC),
            ("Mexaniki", Transmission.MANUAL),
            ("механика", Transmission.MANUAL),
            ("Variator", Transmission.CVT),
            ("DSG", Transmission.DCT),
            ("Robotlaşdırılmış", Transmission.AMT),
        ],
    )
    def test_transmission(self, raw: str, expected: Transmission) -> None:
        assert normalize_transmission(raw) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Ön ötürücü", Drivetrain.FWD),
            ("Arxa ötürücü", Drivetrain.RWD),
            ("Tam ötürücü", Drivetrain.AWD),
            ("полный привод", Drivetrain.AWD),
            ("quattro", Drivetrain.AWD),
            ("xDrive", Drivetrain.AWD),
        ],
    )
    def test_drivetrain(self, raw: str, expected: Drivetrain) -> None:
        assert normalize_drivetrain(raw) == expected

    def test_body_styles(self) -> None:
        assert normalize_body("Hetçbek").value == "HATCHBACK"
        assert normalize_body("Offroader").value == "SUV"
        assert normalize_body("седан").value == "SEDAN"


class TestCities:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("BAKI", "Bakı"), ("baku", "Bakı"), ("баку", "Bakı"), ("Gence", "Gəncə")],
    )
    def test_canonical_city(self, raw: str, expected: str) -> None:
        assert normalize_city(raw) == expected

    def test_metro_grouping(self) -> None:
        """Baku and its commuter satellites trade as one area."""
        assert market_region("Bakı") == market_region("Sumqayıt") == "BAKU_METRO"
        assert market_region("Gəncə") == "Gəncə"

    def test_unknown_city(self) -> None:
        assert normalize_city("Atlantis") is None
        assert market_region(None) == "UNKNOWN"


class TestUnmappedTokens:
    def test_reports_only_unrecognized(self) -> None:
        unmapped = unmapped_tokens("make", ["BMW", "Мерседес", "Zaporozhets", ""])
        assert unmapped == ["Zaporozhets"]

    def test_unknown_field_raises(self) -> None:
        with pytest.raises(KeyError):
            unmapped_tokens("not_a_field", ["x"])


class TestDisplacement:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [(2.0, 2.0), (1998, 2.0), (1600, 1.6), (2.04, 2.0), (None, None), (0, None)],
    )
    def test_normalizes_to_litres(self, raw: float | None, expected: float | None) -> None:
        assert normalize_displacement(raw) == expected

    def test_rejects_implausible(self) -> None:
        assert normalize_displacement(50) is None


class TestConfigurationIdentity:
    def _bmw(self, **overrides: object) -> VehicleConfiguration:
        base = dict(
            make="BMW",
            model="5 Series",
            model_year=2019,
            trim="530i",
            displacement=1998,
            fuel="Benzin",
            transmission="Avtomat",
            drivetrain="Arxa ötürücü",
            body="Sedan",
        )
        base.update(overrides)
        return VehicleConfiguration.from_raw(**base)  # type: ignore[arg-type]

    def test_cross_script_listings_share_one_config_id(self) -> None:
        """A Russian-language and an English-language listing for the same car
        must key identically, or the comparable set silently splits in two."""
        english = self._bmw()
        russian = VehicleConfiguration.from_raw(
            make="бмв",
            model="5er",
            model_year=2019,
            trim="530i",
            displacement=2.0,
            fuel="бензин",
            transmission="автомат",
            drivetrain="задний привод",
            body="седан",
        )
        assert english.config_id == russian.config_id

    def test_config_id_is_deterministic(self) -> None:
        assert self._bmw().config_id == self._bmw().config_id

    @pytest.mark.parametrize(
        "change",
        [
            {"model_year": 2020},
            {"trim": "540i"},
            {"displacement": 3.0},
            {"drivetrain": "Tam ötürücü"},
            {"transmission": "Mexaniki"},
            {"fuel": "Dizel"},
        ],
    )
    def test_material_differences_produce_different_ids(self, change: dict) -> None:
        assert self._bmw().config_id != self._bmw(**change).config_id

    def test_horsepower_does_not_fragment_identity(self) -> None:
        """Sources quote power differently (DIN vs SAE). Including it in the key
        would split one real configuration into several."""
        a = self._bmw().with_updates(horsepower=252)
        b = self._bmw().with_updates(horsepower=248)
        assert a.config_id == b.config_id

    def test_unknown_trim_is_distinct_from_a_named_trim(self) -> None:
        """Conflating "not stated" with "base" biases comparables downward."""
        assert self._bmw(trim=None).config_id != self._bmw(trim="BASE").config_id

    def test_key_ladder_widens_monotonically(self) -> None:
        config = self._bmw()
        assert config.model_key in config.generation_key
        assert config.generation_key in config.powertrain_key
        for level in KeyLevel:
            assert config.key_at(level)

    def test_ladder_ignores_year_above_config_level(self) -> None:
        """Widening to the generation rung is what lets us compare across years."""
        assert self._bmw(model_year=2019).generation_key == self._bmw(model_year=2021).generation_key

    def test_resolvability_requires_make_and_model(self) -> None:
        assert self._bmw().is_resolvable
        assert not VehicleConfiguration(make="BMW").is_resolvable
        assert not VehicleConfiguration().is_resolvable

    def test_specificity_increases_with_detail(self) -> None:
        sparse = VehicleConfiguration.from_raw(make="BMW", model="5 Series")
        assert sparse.specificity < self._bmw().specificity
        assert 0.0 <= sparse.specificity <= 1.0

    def test_unknown_fields_are_actionable(self) -> None:
        sparse = VehicleConfiguration.from_raw(make="BMW", model="5 Series")
        assert "model_year" in sparse.unknown_fields
        assert "trim" in sparse.unknown_fields

    def test_describe_is_human_readable(self) -> None:
        assert "2019" in self._bmw().describe()
        assert "BMW" in self._bmw().describe()

    def test_rejects_impossible_year(self) -> None:
        with pytest.raises(ValueError):
            VehicleConfiguration(make="BMW", model="X", model_year=1700)

    def test_body_noise_is_stripped_from_model(self) -> None:
        """Sellers append body style to the model field; left alone it creates
        a distinct pseudo-model."""
        assert normalize_model("TOYOTA", "Camry Sedan") == normalize_model("TOYOTA", "Camry")
