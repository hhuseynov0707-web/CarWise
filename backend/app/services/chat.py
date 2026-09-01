"""The expert conversation.

A chat is the hardest place to keep this product's one promise. Everywhere
else the model receives a fully-computed bundle and writes prose over it, and
a validator checks every figure it emits against that bundle. Here a person can
ask anything, including questions whose honest answer is a number nobody has
computed.

So the rule is pushed into the prompt and the shape of what the model is given:
it may state a figure only if that figure was handed to it, and when it was
not, the correct answer is to say so and offer the analysis that would produce
one. A model that guesses a market median in conversation has undone the thing
the rest of the system exists to guarantee.

Grounding is therefore explicit rather than inferred. The caller names a
configuration — from a find, a recommendation, a finished report — and the real
snapshot statistics for it go in front of the model. Without that the
conversation is general: how to read a listing, what to inspect, what to ask a
seller. Those are answerable without a single market figure.

Tone is adjusted per person because being addressed by name in your own
language is the difference between advice and a leaflet, and because a first
question from someone who has analysed thirty cars deserves a different
register than one from someone who has analysed none.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.llm.base import CompletionRequest, LLMError, LLMProvider
from app.db.models import (
    AnalysisRecord,
    Listing,
    MarketSnapshot,
    User,
    VehicleConfigurationRow,
)
from app.services.finds import REGION, WINDOW_DAYS

_LANGUAGE_NAMES = {"az": "Azerbaijani", "ru": "Russian", "en": "English"}

SYSTEM_PROMPT = """\
You are the used-car market adviser inside AutoIntel Azerbaijan. You speak to \
buyers about the Azerbaijani market: what a listing is really saying, what to \
inspect, what to ask a seller, how to read a price.

Two rules govern everything you say.

First, you never state a figure that was not given to you. Not a price, not a \
market average, not a depreciation rate, not a percentage. If a question needs \
a number you were not handed, say plainly that you do not have it and that \
running the analysis on that vehicle will produce one. Every number in this \
product is computed from market data, and one invented in conversation would \
be indistinguishable to the reader from one that was earned.

Second, you do not tell anyone whether to buy. You lay out what the evidence \
says and what it does not, and the decision stays with the person you are \
talking to.

Be warm and direct. Short paragraphs. No hedging for its own sake, no \
disclaimers stacked on disclaimers — say the useful thing, then say honestly \
where it stops.

Reply as JSON: {"reply": "..."} and nothing else."""


@dataclass(frozen=True, slots=True)
class ListingContext:
    """One advert's own figures, as stored."""

    listing_id: int
    config_id: str | None
    label: str
    price_azn: Decimal
    mileage_km: int | None
    city: str | None
    damage_disclosed: bool | None
    repaint_disclosed: bool | None
    description: str | None


@dataclass(frozen=True, slots=True)
class MarketContext:
    """Real statistics for one configuration, or nothing."""

    config_id: str
    label: str
    sample_size: int
    median_azn: Decimal | None
    p25_azn: Decimal | None
    p75_azn: Decimal | None
    median_mileage_km: int | None


@dataclass
class ChatService:
    session: AsyncSession
    provider: LLMProvider | None

    async def market_context(self, config_id: str) -> MarketContext | None:
        latest = (
            await self.session.scalars(
                select(func.max(MarketSnapshot.snapshot_date))
                .where(MarketSnapshot.region == REGION)
                .where(MarketSnapshot.window_days == WINDOW_DAYS)
            )
        ).first()
        if latest is None:
            return None

        row = (
            await self.session.execute(
                select(
                    MarketSnapshot.sample_size,
                    MarketSnapshot.median_azn,
                    MarketSnapshot.p25_azn,
                    MarketSnapshot.p75_azn,
                    MarketSnapshot.median_mileage_km,
                    VehicleConfigurationRow.canonical_string,
                )
                .join(
                    VehicleConfigurationRow,
                    VehicleConfigurationRow.config_id == MarketSnapshot.config_id,
                )
                .where(MarketSnapshot.config_id == config_id)
                .where(MarketSnapshot.snapshot_date == latest)
                .where(MarketSnapshot.region == REGION)
                .where(MarketSnapshot.window_days == WINDOW_DAYS)
            )
        ).first()
        if row is None:
            return None

        return MarketContext(
            config_id=config_id,
            label=row.canonical_string,
            sample_size=row.sample_size,
            median_azn=row.median_azn,
            p25_azn=row.p25_azn,
            p75_azn=row.p75_azn,
            median_mileage_km=row.median_mileage_km,
        )

    async def listing_context(self, listing_id: int) -> ListingContext | None:
        row = (
            await self.session.execute(
                select(
                    Listing.id,
                    Listing.config_id,
                    Listing.price_azn,
                    Listing.mileage_km,
                    Listing.city,
                    Listing.has_damage_disclosure,
                    Listing.has_repaint_disclosure,
                    Listing.description,
                    VehicleConfigurationRow.canonical_string,
                )
                .join(
                    VehicleConfigurationRow,
                    VehicleConfigurationRow.config_id == Listing.config_id,
                )
                .where(Listing.id == listing_id)
            )
        ).first()
        if row is None:
            return None
        return ListingContext(
            listing_id=row.id,
            config_id=row.config_id,
            label=row.canonical_string,
            price_azn=row.price_azn,
            mileage_km=row.mileage_km,
            city=row.city,
            damage_disclosed=row.has_damage_disclosure,
            repaint_disclosed=row.has_repaint_disclosure,
            description=row.description,
        )

    async def profile_note(self, user: User | None) -> str:
        """One line telling the model who it is talking to.

        Deliberately thin: a first name, a language, and roughly how much of
        the product this person has used. Anything more would be building a
        profile for its own sake rather than for the answer.
        """
        if user is None:
            return "You are talking to a visitor who is not signed in. Do not assume prior context."

        analyses = (
            await self.session.scalars(
                select(func.count())
                .select_from(AnalysisRecord)
                .where(AnalysisRecord.user_id == user.id)
            )
        ).first() or 0

        name = user.first_name or user.display_name
        who = f"You are talking to {name}." if name else "You are talking to a signed-in user."

        if analyses == 0:
            familiarity = (
                "They have not run an analysis yet, so explain what you offer rather than "
                "assuming they know."
            )
        elif analyses < 5:
            familiarity = f"They have run {analyses} analyses, so they are finding their way around."
        else:
            familiarity = (
                f"They have run {analyses} analyses and know the product. Skip the basics."
            )
        return f"{who} {familiarity}"

    async def reply(
        self,
        *,
        messages: list[dict[str, str]],
        user: User | None,
        language: str,
        config_id: str | None = None,
        listing_id: int | None = None,
    ) -> tuple[str, str | None]:
        """Answer, and say which configuration grounded it.

        Raises :class:`LLMError` when the provider is unavailable — the caller
        decides what to show, because a chat has no computed fallback the way a
        report does.
        """
        if self.provider is None:
            raise LLMError("the reasoning layer is not configured")

        listing = await self.listing_context(listing_id) if listing_id else None
        # A listing knows its own configuration, so naming the advert is enough
        # to bring the market it belongs to with it.
        context = await self.market_context(config_id or (listing.config_id if listing else None) or "")
        system = "\n\n".join(
            part
            for part in (
                SYSTEM_PROMPT,
                f"Answer in the language the person wrote to you in. Being replied to "
                f"in a language you did not use reads as not having been listened to. "
                f"Only when their language is genuinely unclear, use "
                f"{_LANGUAGE_NAMES.get(language, 'English')}.",
                await self.profile_note(user),
                _listing_block(listing),
                _context_block(context),
                _opening_instruction() if not messages else "",
            )
            if part
        )

        response = await self.provider.complete_json(
            CompletionRequest(
                system=system,
                user=_transcript(messages) if messages else _OPENING_TURN,
                temperature=0.4,
                max_tokens=800,
            )
        )
        return _extract_reply(response.text), (context.config_id if context else None)


_OPENING_TURN = "Assess the listing described above."


def _opening_instruction() -> str:
    """What to write when nobody has asked anything yet.

    Reached when someone pressed "discuss this one" rather than typing a
    question, so the useful thing is the assessment they were reaching for —
    not a greeting asking what they would like to know about a car they just
    clicked on.
    """
    return (
        "No question has been asked yet. Open with your own assessment of this "
        "specific listing in a few short paragraphs: where its price sits against "
        "the figures you were given, what in the listing does or does not explain "
        "that, what you would check first, and what the data cannot tell you. End "
        "by inviting a question. Do not greet, do not ask what they want to know."
    )


def _listing_block(listing: ListingContext | None) -> str:
    if listing is None:
        return ""
    facts: dict[str, Any] = {
        "vehicle": listing.label,
        "asking_price_azn": float(listing.price_azn),
        "mileage_km": listing.mileage_km,
        "city": listing.city,
        # Tri-state on purpose: "the seller says it was never hit" and "the
        # seller does not mention it" are different, and flattening them here
        # would let you imply a clean history nobody claimed.
        "seller_states_no_damage": listing.damage_disclosed is False,
        "seller_states_damage": listing.damage_disclosed is True,
        "damage_not_stated": listing.damage_disclosed is None,
        "seller_states_repainted": listing.repaint_disclosed is True,
    }
    block = (
        "This is the specific advert under discussion. These figures are stored "
        "from the listing itself and you may quote them:\n" + json.dumps(facts, ensure_ascii=False)
    )
    if listing.description:
        # Truncated: the seller's own words are worth reading, but a long
        # advert would crowd out the figures it is meant to be read against.
        block += "\n\nThe seller's description, verbatim:\n" + listing.description[:1200]
    return block


def _context_block(context: MarketContext | None) -> str:
    if context is None:
        return (
            "No market statistics have been supplied for this conversation. You therefore "
            "have no figures at all, and must not produce any."
        )
    figures: dict[str, Any] = {
        "configuration": context.label,
        "listings_in_sample": context.sample_size,
        "median_price_azn": _number(context.median_azn),
        "lower_quartile_azn": _number(context.p25_azn),
        "upper_quartile_azn": _number(context.p75_azn),
        "median_mileage_km": context.median_mileage_km,
        "window_days": WINDOW_DAYS,
    }
    return (
        "These are the only figures you may state, and they are computed from listings "
        "observed over the window shown. Quote them as they are; do not derive new "
        "numbers from them beyond simple comparison to a price the user gives you.\n"
        + json.dumps(figures, ensure_ascii=False)
    )


def _transcript(messages: list[dict[str, str]]) -> str:
    """Flatten the exchange into the single user turn the provider takes.

    The provider's contract is one system and one user message, which is all
    the narrative path ever needed. Labelling the turns keeps the history
    legible without widening that contract for one caller.
    """
    lines = [
        f"{'User' if m['role'] == 'user' else 'You'}: {m['content'].strip()}" for m in messages
    ]
    return "\n\n".join(lines) + "\n\nYou:"


def _extract_reply(text: str) -> str:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # A model that ignored the JSON instruction still said something
        # useful; discarding it to punish the format would serve nobody.
        return text.strip()
    reply = parsed.get("reply") if isinstance(parsed, dict) else None
    return (reply or text).strip()


def _number(value: Decimal | None) -> float | None:
    return None if value is None else float(value)
