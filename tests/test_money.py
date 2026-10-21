from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

from wallet.money import NonNegativeMoney, PositiveMoney, credits_price, provider_fee


class Payment(BaseModel):
    amount: PositiveMoney


class PaidAmount(BaseModel):
    amount: NonNegativeMoney


@pytest.mark.parametrize(
    ("paid", "fee", "credited"),
    [
        ("10.00", "0.15", "9.85"),
        ("0.99", "0.01", "0.98"),
        ("33.33", "0.50", "32.83"),
        ("1.00", "0.02", "0.98"),  # float arithmetic gives a 0.01 fee
        ("3.00", "0.05", "2.95"),  # ROUND_HALF_EVEN gives a 0.04 fee
    ],
)
def test_provider_fee_is_rounded_half_up(paid, fee, credited):
    assert provider_fee(Decimal(paid)) == Decimal(fee)
    assert Decimal(paid) - provider_fee(Decimal(paid)) == Decimal(credited)


@pytest.mark.parametrize(
    ("credits", "price"),
    [
        (333, "33.30"),
        (507, "48.17"),  # float and ROUND_HALF_EVEN give 48.16
        (1005, "87.94"),
        (499, "49.90"),
        (500, "47.50"),
        (999, "94.91"),  # ROUND_HALF_EVEN gives 94.90
        (1000, "87.50"),
    ],
)
def test_credits_price_applies_discount_tiers(credits, price):
    assert credits_price(credits) == Decimal(price)


@pytest.mark.parametrize("raw", ["10.00", "10", "10.5", "0.01", "999999999999.99"])
def test_amount_accepts_decimal_strings(raw):
    assert Payment.model_validate({"amount": raw}).amount == Decimal(raw)


def test_amount_is_normalized_to_cents():
    assert str(Payment.model_validate({"amount": "10"}).amount) == "10.00"


@pytest.mark.parametrize(
    "raw_json",
    [
        "10.5",  # a JSON number, not a string
        '"10.001"',
        '"1e2"',
        '" 5.00 "',
        '"-1.00"',
        '"01.00"',
        '"0"',
        '"0.00"',
        '"NaN"',
        '"1\\u0663"',  # ASCII 1 + ARABIC-INDIC DIGIT THREE: \d would accept it, Decimal() reads 13
        "null",
        '"1000000000000.00"',
        '"1.\\u0663"',  # \d would read it as 1.3
        '"10."',  # a trailing dot is not an amount
        '".5"',  # the integer part is required
        '"+1.00"',  # no sign
        '"10.00\\n"',  # $ matches before a trailing newline, fullmatch does not
    ],
)
def test_amount_rejects_anything_else(raw_json):
    with pytest.raises(ValidationError):
        Payment.model_validate_json(f'{{"amount": {raw_json}}}')


@pytest.mark.parametrize("raw_json", ["10.5", '"10.001"', '"1e2"', '"1\\u0663"'])
def test_paid_amount_rejects_numbers_and_non_decimal_strings(raw_json):
    with pytest.raises(ValidationError):
        PaidAmount.model_validate_json(f'{{"amount": {raw_json}}}')


def test_non_negative_money_accepts_zero():
    assert PaidAmount.model_validate({"amount": "0.00"}).amount == Decimal("0.00")
