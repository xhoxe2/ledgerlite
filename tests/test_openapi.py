"""The OpenAPI schema must tell clients what the API accepts: money is a string."""

import pytest

from wallet.app import create_app


def resolve(schemas: dict, schema: dict) -> dict:
    """Follows a $ref or a single-entry allOf to the schema that carries the type."""
    if "$ref" in schema:
        return resolve(schemas, schemas[schema["$ref"].rsplit("/", 1)[-1]])
    if "allOf" in schema:
        [only] = schema["allOf"]
        return resolve(schemas, only)
    return schema


@pytest.mark.parametrize("model", ["DepositIn", "TransferIn"])
def test_money_fields_are_advertised_as_strings_with_a_pattern(settings, model):
    schemas = create_app(settings).openapi()["components"]["schemas"]
    amount = resolve(schemas, schemas[model]["properties"]["amount"])
    assert "anyOf" not in amount, amount  # a number alternative makes Swagger send {"amount": 0}
    assert "oneOf" not in amount, amount
    assert amount["type"] == "string"
    assert amount["pattern"]
