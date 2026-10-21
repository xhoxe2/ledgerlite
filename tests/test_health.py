"""GET /health: answers without an API key and reports a database it cannot query as 503."""

import asyncpg
import pytest

pytestmark = pytest.mark.anyio


class FailingPool:
    """Stands in for the asyncpg pool when the database cannot be queried."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def fetchval(self, query: str, *args):
        raise self.error


async def test_health_is_ok_without_an_api_key(client):
    response = await client.get("/health")
    assert (response.status_code, response.json()) == (200, {"status": "ok"})


@pytest.mark.parametrize(
    "error",
    [
        ConnectionRefusedError("connection refused"),
        asyncpg.CannotConnectNowError("the database system is starting up"),
    ],
    ids=["connection-refused", "database-starting-up"],
)
async def test_health_is_503_when_the_database_cannot_be_queried(app, client, error):
    real_pool = app.state.pool
    app.state.pool = FailingPool(error)
    try:
        response = await client.get("/health")
    finally:
        # The app fixture closes app.state.pool on teardown, so the real pool must be back.
        app.state.pool = real_pool
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "db_unavailable"
