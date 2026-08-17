"""GET /payments/{id} through the full application stack."""

from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from tests.conftest import ALPHA_HEADERS, BETA_HEADERS
from tests.fakes import FakeAcquiringBank


def create_payment(client: TestClient, payload: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/payments", json=payload, headers=ALPHA_HEADERS)
    assert response.status_code == 201
    return response.json()


def test_returns_the_previously_made_payment(
    client: TestClient, valid_payment_request: dict[str, Any]
) -> None:
    created = create_payment(client, valid_payment_request)

    response = client.get(f"/payments/{created['id']}", headers=ALPHA_HEADERS)

    assert response.status_code == 200
    assert response.json() == created


def test_a_declined_payment_is_also_retrievable(
    client: TestClient, fake_bank: FakeAcquiringBank, valid_payment_request: dict[str, Any]
) -> None:
    fake_bank.authorized = False
    created = create_payment(client, valid_payment_request)

    response = client.get(f"/payments/{created['id']}", headers=ALPHA_HEADERS)

    assert response.json()["status"] == "Declined"


def test_the_response_never_contains_the_full_card_number(
    client: TestClient, valid_payment_request: dict[str, Any]
) -> None:
    created = create_payment(client, valid_payment_request)

    response = client.get(f"/payments/{created['id']}", headers=ALPHA_HEADERS)

    assert valid_payment_request["card_number"] not in response.text
    assert response.json()["last_four_card_digits"] == "8877"


def test_returns_404_for_an_unknown_id(client: TestClient) -> None:
    response = client.get(f"/payments/{uuid4()}", headers=ALPHA_HEADERS)

    assert response.status_code == 404
    assert response.json()["error"] == "payment_not_found"


def test_rejects_an_id_that_is_not_a_uuid(client: TestClient) -> None:
    response = client.get("/payments/not-a-uuid", headers=ALPHA_HEADERS)

    assert response.status_code == 400
    assert response.json()["status"] == "Rejected"


def test_another_merchant_gets_404_rather_than_403(
    client: TestClient, valid_payment_request: dict[str, Any]
) -> None:
    # 403 would confirm the id exists; 404 gives nothing away.
    created = create_payment(client, valid_payment_request)

    response = client.get(f"/payments/{created['id']}", headers=BETA_HEADERS)

    assert response.status_code == 404
