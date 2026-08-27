"""Monetary values and currency conversion.

A large share of Azerbaijani listings quote prices in USD rather than AZN, so
currency handling is a correctness requirement, not a nicety: mixing the two in
one comparable set silently produces a bimodal price distribution and a
meaningless median.

Conversion always requires an explicitly supplied :class:`FxRate`. The domain
layer owns no rate table and reads no clock — inventing an exchange rate would
be exactly the kind of fabricated input the architecture forbids. Rates come
from an adapter (the Central Bank of Azerbaijan publishes official daily rates)
and travel with their own timestamp and source so a converted figure remains
auditable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from app.domain.enums import Currency

_CENT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class Money:
    """An amount in a specific currency.

    Arithmetic between different currencies raises rather than guessing, which
    is what stops a mixed-currency comparable set from ever reaching the
    statistics layer.
    """

    amount: Decimal
    currency: Currency = Currency.AZN

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            object.__setattr__(self, "amount", Decimal(str(self.amount)))
        if self.amount < 0:
            raise ValueError(f"negative monetary amount: {self.amount}")

    @classmethod
    def azn(cls, amount: float | int | str | Decimal) -> Money:
        return cls(Decimal(str(amount)), Currency.AZN)

    @classmethod
    def of(cls, amount: float | int | str | Decimal, currency: Currency) -> Money:
        return cls(Decimal(str(amount)), currency)

    def quantized(self) -> Money:
        return Money(self.amount.quantize(_CENT, rounding=ROUND_HALF_UP), self.currency)

    def as_float(self) -> float:
        """Float view for statistical routines. Never used for storage."""
        return float(self.amount)

    def _require_same_currency(self, other: Money) -> None:
        if self.currency is not other.currency:
            raise ValueError(
                f"cannot combine {self.currency.value} and {other.currency.value}; "
                "convert with an explicit FxRate first"
            )

    def __add__(self, other: Money) -> Money:
        self._require_same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._require_same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __mul__(self, factor: float | int | Decimal) -> Money:
        return Money(self.amount * Decimal(str(factor)), self.currency)

    def __lt__(self, other: Money) -> bool:
        self._require_same_currency(other)
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        self._require_same_currency(other)
        return self.amount <= other.amount

    def ratio_to(self, other: Money) -> float:
        """This amount divided by another, as a plain float."""
        self._require_same_currency(other)
        if other.amount == 0:
            raise ZeroDivisionError("cannot take ratio against zero")
        return float(self.amount / other.amount)

    def pct_difference_from(self, baseline: Money) -> float:
        """Signed percentage difference from a baseline.

        ``Money.azn(105).pct_difference_from(Money.azn(100))`` is ``5.0``.
        """
        self._require_same_currency(baseline)
        if baseline.amount == 0:
            raise ZeroDivisionError("cannot compare against a zero baseline")
        return float((self.amount - baseline.amount) / baseline.amount * 100)

    def format(self) -> str:
        whole = self.amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return f"{whole:,} {self.currency.value}"

    def __str__(self) -> str:
        return self.format()


@dataclass(frozen=True, slots=True)
class FxRate:
    """One currency conversion rate, with its origin.

    ``rate`` is the number of ``quote`` units per one ``base`` unit, so
    ``FxRate(USD, AZN, 1.70)`` reads "1 USD = 1.70 AZN".
    """

    base: Currency
    quote: Currency
    rate: Decimal
    as_of: datetime
    source: str

    def __post_init__(self) -> None:
        if not isinstance(self.rate, Decimal):
            object.__setattr__(self, "rate", Decimal(str(self.rate)))
        if self.rate <= 0:
            raise ValueError(f"exchange rate must be positive, got {self.rate}")
        if self.base is self.quote:
            raise ValueError("base and quote currency must differ")

    def inverted(self) -> FxRate:
        return FxRate(
            base=self.quote,
            quote=self.base,
            rate=Decimal(1) / self.rate,
            as_of=self.as_of,
            source=self.source,
        )

    def convert(self, money: Money) -> Money:
        """Convert an amount using this rate, in either direction."""
        if money.currency is self.base:
            return Money(money.amount * self.rate, self.quote)
        if money.currency is self.quote:
            return Money(money.amount / self.rate, self.base)
        raise ValueError(
            f"rate {self.base.value}/{self.quote.value} cannot convert "
            f"{money.currency.value}"
        )


class FxTable:
    """A small set of rates valid at one point in time.

    Held per ingestion batch or per analysis run so that every price in a
    comparable set is converted with the *same* rates — otherwise a listing
    ingested on Monday and one ingested on Friday are not on the same scale.
    """

    def __init__(self, rates: tuple[FxRate, ...] = ()) -> None:
        self._rates: dict[tuple[Currency, Currency], FxRate] = {}
        for rate in rates:
            self.add(rate)

    def add(self, rate: FxRate) -> None:
        self._rates[(rate.base, rate.quote)] = rate
        self._rates[(rate.quote, rate.base)] = rate.inverted()

    def to(self, money: Money, target: Currency) -> Money:
        """Convert into ``target``. Raises if no rate is available.

        Raising is deliberate: a missing rate means we do not know this price,
        and a listing whose price we do not know must be excluded from the
        comparable set rather than included at a guessed value.
        """
        if money.currency is target:
            return money
        rate = self._rates.get((money.currency, target))
        if rate is None:
            raise LookupError(
                f"no exchange rate available for {money.currency.value}->{target.value}"
            )
        return rate.convert(money)

    def try_to(self, money: Money, target: Currency) -> Money | None:
        """Non-raising variant for callers that filter rather than fail."""
        try:
            return self.to(money, target)
        except LookupError:
            return None

    def has(self, base: Currency, quote: Currency) -> bool:
        return (base, quote) in self._rates

    @property
    def rates(self) -> tuple[FxRate, ...]:
        seen: dict[tuple[Currency, Currency], FxRate] = {}
        for (base, quote), rate in self._rates.items():
            if (quote, base) not in seen:
                seen[(base, quote)] = rate
        return tuple(seen.values())
