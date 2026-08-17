"""Idempotency-Key behaviour over HTTP."""

from typing import Any

from fastapi.testclient import TestClient

from payment_gateway_api.domain.errors import AcquiringBankUnavailableError
from tests.conftest import ALPHA_HEADERS, BETA_HEADERS
from tests.fakes import FakeAcquiringBank

KEY = {"Idempotency-Key": "b1f6c1d2-4e8a-4c2a-9f3b-2a1e5d6c7b8a"}


def test_replaying_a_request_returns_the_original_payment(
    client: TestClient, fake_bank: FakeAcquiringBank, valid_payment_request: dict[str, Any]
) -> None:
    headers = {**ALPHA_HEADERS, **KEY}

    first = client.post("/payments", json=valid_payment_request, headers=headers)
    second = client.post("/payments", json=valid_payment_request, headers=headers)

    assert first.json() == second.json()
    # The shopper's card was only charged once.
    assert fake_bank.call_count == 1


def test_the_replayed_response_is_still_201(
    client: TestClient, valid_payment_request: dict[str, Any]
) -> None:
    headers = {**ALPHA_HEADERS, **KEY}
    client.post("/payments", json=valid_payment_request, headers=headers)

    second = client.post("/payments", json=valid_payment_request, headers=headers)

    assert second.status_code == 201


def test_reusing_a_key_with_a_different_body_is_a_409(
    client: TestClient, valid_payment_request: dict[str, Any]
) -> None:
    headers = {**ALPHA_HEADERS, **KEY}
    client.post("/payments", json=valid_payment_request, headers=headers)

    response = client.post(
        "/payments", json={**valid_payment_request, "amount": 9999}, headers=headers
    )

    assert response.status_code == 409
    assert response.json()["error"] == "idempotency_conflict"


def test_keys_do_not_collide_across_merchants(
    client: TestClient, fake_bank: FakeAcquiringBank, valid_payment_request: dict[str, Any]
) -> None:
    first = client.post("/payments", json=valid_payment_request, headers={**ALPHA_HEADERS, **KEY})
    second = client.post("/payments", json=valid_payment_request, headers={**BETA_HEADERS, **KEY})

    assert first.json()["id"] != second.json()["id"]
    assert fake_bank.call_count == 2


def test_a_key_is_reusable_after_the_bank_was_unavailable(
    client: TestClient, fake_bank: FakeAcquiringBank, valid_payment_request: dict[str, Any]
) -> None:
    headers = {**ALPHA_HEADERS, **KEY}
    fake_bank.error = AcquiringBankUnavailableError("bank down")
    assert client.post("/payments", json=valid_payment_request, headers=headers).status_code == 502

    fake_bank.error = None
    retry = client.post("/payments", json=valid_payment_request, headers=headers)

    assert retry.status_code == 201


def test_a_key_cannot_be_reused_after_a_timeout(
    client: TestClient, fake_bank: FakeAcquiringBank, valid_payment_request: dict[str, Any]
) -> None:
    # The payment may already have been authorized, so the merchant is stopped from
    # replaying the key rather than being allowed to charge the shopper twice.
    headers = {**ALPHA_HEADERS, **KEY}
    fake_bank.fail_with_timeout()
    assert client.post("/payments", json=valid_payment_request, headers=headers).status_code == 504

    fake_bank.error = None
    retry = client.post("/payments", json=valid_payment_request, headers=headers)

    assert retry.status_code == 409
    assert fake_bank.call_count == 1


def test_requests_without_a_key_are_independent(
    client: TestClient, fake_bank: FakeAcquiringBank, valid_payment_request: dict[str, Any]
) -> None:
    first = client.post("/payments", json=valid_payment_request, headers=ALPHA_HEADERS)
    second = client.post("/payments", json=valid_payment_request, headers=ALPHA_HEADERS)

    assert first.json()["id"] != second.json()["id"]
    assert fake_bank.call_count == 2
