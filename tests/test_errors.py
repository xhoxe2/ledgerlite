import pytest

pytestmark = pytest.mark.anyio


async def test_unknown_route_uses_the_error_format(client):
    response = await client.get("/v1/unknown")
    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "Not Found", "details": {}}
    }


async def test_wrong_method_uses_the_error_format(client):
    response = await client.post("/v1/accounts/me")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"
