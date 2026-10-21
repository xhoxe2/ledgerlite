"""Money rules: amounts from JSON, the provider fee and the price of credits."""

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from pydantic import BeforeValidator, Field

CENT = Decimal("0.01")
PROVIDER_FEE_RATE = Decimal("0.015")
CREDIT_BASE_PRICE = Decimal("0.10")

# [0-9] rather than \d: \d also matches non-ASCII digits, and Decimal() accepts those.
_AMOUNT = re.compile(r"(0|[1-9][0-9]{0,11})(\.[0-9]{1,2})?")


def to_cents(amount: Decimal) -> Decimal:
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def parse_amount(value: object) -> Decimal:
    if not isinstance(value, str) or not _AMOUNT.fullmatch(value):
        raise ValueError("amount must be a string such as '10.00' with at most 2 decimal places")
    return to_cents(Decimal(value))


PositiveMoney = Annotated[Decimal, BeforeValidator(parse_amount), Field(gt=0)]
NonNegativeMoney = Annotated[Decimal, BeforeValidator(parse_amount), Field(ge=0)]


def provider_fee(paid: Decimal) -> Decimal:
    return to_cents(paid * PROVIDER_FEE_RATE)


def credits_price(credits: int) -> Decimal:
    if credits >= 1000:
        price_factor = Decimal("0.875")
    elif credits >= 500:
        price_factor = Decimal("0.95")
    else:
        price_factor = Decimal("1")
    return to_cents(credits * CREDIT_BASE_PRICE * price_factor)
