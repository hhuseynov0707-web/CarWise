"""What the expert is told about a car before it speaks.

The block below is the entire basis for anything the expert says about a
specific advert, so the risk here is not that it looks wrong — it is that a
flattened tri-state quietly turns "the seller never mentioned damage" into
"the seller says there is none", and the expert then reassures somebody on a
claim nobody made.
"""

from __future__ import annotations

import json
from decimal import Decimal

from app.services.chat import ListingContext, _listing_block, _opening_instruction


def _listing(damage: bool | None, repaint: bool | None = None) -> ListingContext:
    return ListingContext(
        listing_id=1,
        config_id="cfg_x",
        label="BMW|530|2019",
        price_azn=Decimal("42000"),
        mileage_km=120_000,
        city="Bakı",
        damage_disclosed=damage,
        repaint_disclosed=repaint,
        description=None,
    )


def _facts(listing: ListingContext) -> dict:
    block = _listing_block(listing)
    return json.loads(block[block.index("{") : block.rindex("}") + 1])


class TestDamageIsTriState:
    def test_a_seller_stating_no_damage_is_recorded_as_that(self) -> None:
        facts = _facts(_listing(damage=False))
        assert facts["seller_states_no_damage"] is True
        assert facts["seller_states_damage"] is False
        assert facts["damage_not_stated"] is False

    def test_a_seller_stating_damage_is_recorded_as_that(self) -> None:
        facts = _facts(_listing(damage=True))
        assert facts["seller_states_damage"] is True
        assert facts["seller_states_no_damage"] is False

    def test_silence_is_not_reported_as_a_clean_history(self) -> None:
        """The one that matters. Nobody claimed anything, and the expert must
        not be handed a claim."""
        facts = _facts(_listing(damage=None))
        assert facts["damage_not_stated"] is True
        assert facts["seller_states_no_damage"] is False
        assert facts["seller_states_damage"] is False


class TestListingBlock:
    def test_nothing_is_written_without_a_listing(self) -> None:
        assert _listing_block(None) == ""

    def test_the_asking_price_and_mileage_travel_with_it(self) -> None:
        facts = _facts(_listing(damage=False))
        assert facts["asking_price_azn"] == 42000.0
        assert facts["mileage_km"] == 120_000

    def test_a_long_description_is_truncated(self) -> None:
        """The seller's words are worth reading; a 6,000-character advert would
        crowd out the figures it is meant to be read against."""
        listing = ListingContext(
            listing_id=1, config_id="c", label="x", price_azn=Decimal("1"),
            mileage_km=None, city=None, damage_disclosed=None, repaint_disclosed=None,
            description="д" * 6000,
        )
        assert len(_listing_block(listing)) < 2500


def test_the_opening_does_not_ask_what_they_want() -> None:
    """Someone who pressed "discuss this one" has already said what they want,
    and a greeting that asks again is a wasted turn."""
    text = _opening_instruction().lower()
    assert "do not greet" in text
    assert "assessment" in text
