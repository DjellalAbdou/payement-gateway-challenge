"""POST /payments through the full application stack."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from payment_gateway_api.domain.errors import (
    AcquiringBankError,
    AcquiringBankUnavailableError,
)
from tests.conftest import ALPHA_HEADERS
from tests.fakes import FakeAcquiringBank


class TestAuthorizedPayment:
    def test_returns_201_with_the_documented_fields(
        self, client: TestClient, valid_payment_request: dict[str, Any]
    ) -> None:
        response = client.post("/payments", json=valid_payment_request, headers=ALPHA_HEADERS)

        assert response.status_code == 201
        body = response.json()
        assert set(body) == {
            "id",
            "status",
            "last_four_card_digits",
            "expiry_month",
            "expiry_year",
            "currency",
            "amount",
        }
        assert body["status"] == "Authorized"
        assert body["last_four_card_digits"] == "8877"
        assert body["currency"] == "GBP"
        assert body["amount"] == 1050

    def test_never_returns_the_full_card_number_or_cvv(
        self, client: TestClient, valid_payment_request: dict[str, Any]
    ) -> None:
        response = client.post("/payments", json=valid_payment_request, headers=ALPHA_HEADERS)

        assert valid_payment_request["card_number"] not in response.text
        assert "cvv" not in response.text

    def test_each_payment_gets_a_distinct_id(
        self, client: TestClient, valid_payment_request: dict[str, Any]
    ) -> None:
        first = client.post("/payments", json=valid_payment_request, headers=ALPHA_HEADERS)
        second = client.post("/payments", json=valid_payment_request, headers=ALPHA_HEADERS)

        assert first.json()["id"] != second.json()["id"]


class TestDeclinedPayment:
    def test_returns_201_with_a_declined_status(
        self,
        client: TestClient,
        fake_bank: FakeAcquiringBank,
        valid_payment_request: dict[str, Any],
    ) -> None:
        # A decline is a successful API call: the gateway did its job, the bank
        # said no.
        fake_bank.authorized = False

        response = client.post("/payments", json=valid_payment_request, headers=ALPHA_HEADERS)

        assert response.status_code == 201
        assert response.json()["status"] == "Declined"


class TestRejectedPayment:
    def test_returns_400_with_a_rejected_envelope(
        self, client: TestClient, valid_payment_request: dict[str, Any]
    ) -> None:
        response = client.post(
            "/payments", json={**valid_payment_request, "cvv": "12"}, headers=ALPHA_HEADERS
        )

        assert response.status_code == 400
        body = response.json()
        assert body["status"] == "Rejected"
        assert body["errors"] == [
            {"field": "cvv", "message": "String should have at least 3 characters"}
        ]

    def test_the_bank_is_never_called_for_a_rejected_request(
        self,
        client: TestClient,
        fake_bank: FakeAcquiringBank,
        valid_payment_request: dict[str, Any],
    ) -> None:
        client.post("/payments", json={**valid_payment_request, "amount": 0}, headers=ALPHA_HEADERS)

        assert fake_bank.call_count == 0

    def test_reports_every_invalid_field(
        self, client: TestClient, valid_payment_request: dict[str, Any]
    ) -> None:
        response = client.post(
            "/payments",
            json={**valid_payment_request, "cvv": "1", "currency": "JPY", "amount": -5},
            headers=ALPHA_HEADERS,
        )

        fields = {error["field"] for error in response.json()["errors"]}
        assert fields == {"cvv", "currency", "amount"}

    @pytest.mark.parametrize(
        "field", ["card_number", "expiry_month", "expiry_year", "currency", "amount", "cvv"]
    )
    def test_every_field_is_required(
        self, client: TestClient, valid_payment_request: dict[str, Any], field: str
    ) -> None:
        payload = {k: v for k, v in valid_payment_request.items() if k != field}

        response = client.post("/payments", json=payload, headers=ALPHA_HEADERS)

        assert response.status_code == 400
        assert response.json()["errors"][0]["field"] == field

    def test_rejects_a_body_that_is_not_an_object(self, client: TestClient) -> None:
        response = client.post("/payments", json=["not", "an", "object"], headers=ALPHA_HEADERS)

        assert response.status_code == 400
        assert response.json()["status"] == "Rejected"

    def test_rejects_a_malformed_json_body(self, client: TestClient) -> None:
        response = client.post(
            "/payments",
            content=b"{not json",
            headers={**ALPHA_HEADERS, "Content-Type": "application/json"},
        )

        assert response.status_code == 400
        assert response.json()["status"] == "Rejected"


class TestBankUnavailable:
    def test_returns_502_rather_than_declining(
        self,
        client: TestClient,
        fake_bank: FakeAcquiringBank,
        valid_payment_request: dict[str, Any],
    ) -> None:
        # An unknown outcome is not a decline: the merchant must be able to tell
        # the difference and retry.
        fake_bank.error = AcquiringBankUnavailableError("bank down")

        response = client.post("/payments", json=valid_payment_request, headers=ALPHA_HEADERS)

        assert response.status_code == 502
        assert response.json()["error"] == "acquiring_bank_unavailable"

    def test_no_payment_is_created(
        self,
        client: TestClient,
        fake_bank: FakeAcquiringBank,
        valid_payment_request: dict[str, Any],
    ) -> None:
        fake_bank.error = AcquiringBankUnavailableError("bank down")

        response = client.post("/payments", json=valid_payment_request, headers=ALPHA_HEADERS)

        assert "id" not in response.json()

    def test_advertises_a_retry_after_delay(
        self,
        client: TestClient,
        fake_bank: FakeAcquiringBank,
        valid_payment_request: dict[str, Any],
    ) -> None:
        # This is the one bank failure we actively invite a retry for, so the delay
        # is made machine-readable.
        fake_bank.error = AcquiringBankUnavailableError("bank down")

        response = client.post("/payments", json=valid_payment_request, headers=ALPHA_HEADERS)

        assert int(response.headers["Retry-After"]) > 0


class TestBankProtocolError:
    def test_returns_502_with_its_own_error_code(
        self,
        client: TestClient,
        fake_bank: FakeAcquiringBank,
        valid_payment_request: dict[str, Any],
    ) -> None:
        # Distinct from an outage: the bank answered, we just could not use the
        # answer, which means our integration is broken.
        fake_bank.fail_with_protocol_error()

        response = client.post("/payments", json=valid_payment_request, headers=ALPHA_HEADERS)

        assert response.status_code == 502
        assert response.json()["error"] == "acquiring_bank_error"

    def test_does_not_invite_a_retry(
        self,
        client: TestClient,
        fake_bank: FakeAcquiringBank,
        valid_payment_request: dict[str, Any],
    ) -> None:
        # Retrying cannot help until we ship a fix, so we neither promise it is safe
        # nor advertise a delay.
        fake_bank.fail_with_protocol_error()

        response = client.post("/payments", json=valid_payment_request, headers=ALPHA_HEADERS)

        assert "safely be retried" not in response.json()["message"]
        assert "Retry-After" not in response.headers

    def test_no_payment_is_created(
        self,
        client: TestClient,
        fake_bank: FakeAcquiringBank,
        valid_payment_request: dict[str, Any],
    ) -> None:
        fake_bank.fail_with_protocol_error()

        response = client.post("/payments", json=valid_payment_request, headers=ALPHA_HEADERS)

        assert "id" not in response.json()

    def test_a_new_kind_of_bank_error_falls_back_to_the_safe_response(
        self,
        client: TestClient,
        fake_bank: FakeAcquiringBank,
        valid_payment_request: dict[str, Any],
    ) -> None:
        # A subclass added later must not inherit a "safe to retry" promise just
        # because nobody remembered to give it a handler. The base-class handler is
        # registered precisely so the conservative response is the default.
        class FutureAcquiringBankError(AcquiringBankError):
            pass

        fake_bank.error = FutureAcquiringBankError("something new")

        response = client.post("/payments", json=valid_payment_request, headers=ALPHA_HEADERS)

        assert response.status_code == 502
        assert response.json()["error"] == "acquiring_bank_error"
        assert "Retry-After" not in response.headers


class TestBankTimeout:
    def test_returns_504_and_a_distinct_error_code(
        self,
        client: TestClient,
        fake_bank: FakeAcquiringBank,
        valid_payment_request: dict[str, Any],
    ) -> None:
        # Distinguished from 502 because the payment may actually have been taken:
        # the merchant needs to tell "definitely not charged" from "unknown".
        fake_bank.fail_with_timeout()

        response = client.post("/payments", json=valid_payment_request, headers=ALPHA_HEADERS)

        assert response.status_code == 504
        assert response.json()["error"] == "acquiring_bank_timeout"

    def test_does_not_tell_the_merchant_the_request_is_safe_to_retry(
        self,
        client: TestClient,
        fake_bank: FakeAcquiringBank,
        valid_payment_request: dict[str, Any],
    ) -> None:
        fake_bank.fail_with_timeout()

        response = client.post("/payments", json=valid_payment_request, headers=ALPHA_HEADERS)

        message = response.json()["message"]
        assert "unknown" in message
        assert "safely be retried" not in message

    def test_carries_no_retry_after_header(
        self,
        client: TestClient,
        fake_bank: FakeAcquiringBank,
        valid_payment_request: dict[str, Any],
    ) -> None:
        # We do not want this one retried at all.
        fake_bank.fail_with_timeout()

        response = client.post("/payments", json=valid_payment_request, headers=ALPHA_HEADERS)

        assert "Retry-After" not in response.headers

    def test_no_payment_is_created_either(
        self,
        client: TestClient,
        fake_bank: FakeAcquiringBank,
        valid_payment_request: dict[str, Any],
    ) -> None:
        # Even though the payment may have been taken, we cannot honestly record
        # one: reconciliation is what resolves this, not a guess.
        fake_bank.fail_with_timeout()

        response = client.post("/payments", json=valid_payment_request, headers=ALPHA_HEADERS)

        assert "id" not in response.json()
