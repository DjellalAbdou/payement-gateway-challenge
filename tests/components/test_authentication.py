"""Merchant authentication and scoping."""

from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from tests.conftest import ALPHA_HEADERS


def test_posting_without_an_api_key_is_unauthorized(
    client: TestClient, valid_payment_request: dict[str, Any]
) -> None:
    response = client.post("/payments", json=valid_payment_request)

    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


def test_an_unknown_api_key_is_unauthorized(
    client: TestClient, valid_payment_request: dict[str, Any]
) -> None:
    response = client.post(
        "/payments", json=valid_payment_request, headers={"X-Api-Key": "sk_test_nonsense"}
    )

    assert response.status_code == 401


def test_getting_without_an_api_key_is_unauthorized(client: TestClient) -> None:
    response = client.get(f"/payments/{uuid4()}")

    assert response.status_code == 401


def test_authentication_is_checked_before_the_bank_is_called(
    client: TestClient, fake_bank, valid_payment_request: dict[str, Any]
) -> None:
    client.post("/payments", json=valid_payment_request)

    assert fake_bank.call_count == 0


def test_the_health_endpoint_needs_no_api_key(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {"app": "payment-gateway-api", "status": 200}


def test_a_valid_key_is_accepted(client: TestClient, valid_payment_request: dict[str, Any]) -> None:
    response = client.post("/payments", json=valid_payment_request, headers=ALPHA_HEADERS)

    assert response.status_code == 201
