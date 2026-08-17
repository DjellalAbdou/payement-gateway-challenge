"""End-to-end tests against the real Mountebank bank simulator.

Everything else stubs the bank; this suite proves the HTTP contract itself is
right, the wire format, the ``MM/YYYY`` expiry, and how each simulator response
maps onto what the merchant sees.

Run with the simulator up

They are marked ``integration`` and excluded from the default ``make test`` run so the unit
and API suites stay fast and network-free.
"""

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from payment_gateway_api.app import create_app
from payment_gateway_api.config import get_settings
from tests.conftest import ALPHA_HEADERS, future_expiry

pytestmark = pytest.mark.integration

# The simulator keys its behaviour off the final digit of the card number.
AUTHORIZED_CARD = "2222405343248877"  # ends 7 -> authorized
DECLINED_CARD = "2222405343248112"  # ends 2 -> declined
UNAVAILABLE_CARD = "2222405343248870"  # ends 0 -> HTTP 503


@pytest.fixture(scope="module")
def simulator_url() -> str:
    url = get_settings().acquiring_bank_url
    try:
        # Any response at all means something is listening; the simulator answers
        # 400 to a bare GET, which is fine for a liveness check.
        httpx.get(f"{url}/payments", timeout=2.0)
    except httpx.HTTPError:
        pytest.skip(
            f"Bank simulator is not running at {url} (start it with `docker compose up -d`)"
        )
    return url


@pytest.fixture
def client(simulator_url: str) -> Iterator[TestClient]:
    # No dependency overrides here: this app talks to the real simulator.
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def payment_request() -> dict[str, Any]:
    month, year = future_expiry()
    return {
        "card_number": AUTHORIZED_CARD,
        "expiry_month": month,
        "expiry_year": year,
        "currency": "GBP",
        "amount": 100,
        "cvv": "123",
    }


def test_an_authorizing_card_is_authorized(
    client: TestClient, payment_request: dict[str, Any]
) -> None:
    response = client.post("/payments", json=payment_request, headers=ALPHA_HEADERS)

    assert response.status_code == 201
    assert response.json()["status"] == "Authorized"
    assert response.json()["last_four_card_digits"] == "8877"


def test_a_declining_card_is_declined(client: TestClient, payment_request: dict[str, Any]) -> None:
    response = client.post(
        "/payments", json={**payment_request, "card_number": DECLINED_CARD}, headers=ALPHA_HEADERS
    )

    assert response.status_code == 201
    assert response.json()["status"] == "Declined"


def test_a_bank_outage_surfaces_as_502(client: TestClient, payment_request: dict[str, Any]) -> None:
    # The simulator answers 503 for a card ending in 0. We retry, then report an
    # unknown outcome rather than inventing a decline.
    response = client.post(
        "/payments",
        json={**payment_request, "card_number": UNAVAILABLE_CARD},
        headers=ALPHA_HEADERS,
    )

    assert response.status_code == 502
    assert response.json()["error"] == "acquiring_bank_unavailable"


def test_a_payment_can_be_retrieved_after_being_made(
    client: TestClient, payment_request: dict[str, Any]
) -> None:
    created = client.post("/payments", json=payment_request, headers=ALPHA_HEADERS).json()

    retrieved = client.get(f"/payments/{created['id']}", headers=ALPHA_HEADERS)

    assert retrieved.status_code == 200
    assert retrieved.json() == created


def test_an_idempotent_replay_hits_the_bank_once(
    client: TestClient, payment_request: dict[str, Any]
) -> None:
    headers = {**ALPHA_HEADERS, "Idempotency-Key": "b1f6c1d2-4e8a-4c2a-9f3b-2a1e5d6c7b8a"}

    first = client.post("/payments", json=payment_request, headers=headers).json()
    second = client.post("/payments", json=payment_request, headers=headers).json()

    assert first["id"] == second["id"]


def test_an_invalid_request_never_reaches_the_simulator(
    client: TestClient, payment_request: dict[str, Any]
) -> None:
    response = client.post("/payments", json={**payment_request, "cvv": "1"}, headers=ALPHA_HEADERS)

    assert response.status_code == 400
    assert response.json()["status"] == "Rejected"
