"""Cardholder data must never reach a log line, a traceback or a stored record.

These are the tests that would catch a careless ``logger.info(f"{request}")`` or an
exception message that interpolates the request object.
"""

import json
import logging

from payment_gateway_api.api.schemas.payment_schema import ProcessPaymentCommand
from payment_gateway_api.infrastructure.clients.models import AuthorizationRequest
from payment_gateway_api.logger_config import JsonFormatter, request_id_var

CARD_NUMBER = "4111111111111111"
CVV = "737"

REQUEST = AuthorizationRequest(
    card_number=CARD_NUMBER,
    expiry_month=4,
    expiry_year=2030,
    currency="GBP",
    amount=100,
    cvv=CVV,
)


class TestReprRedaction:
    def test_the_authorization_request_repr_hides_the_pan_and_cvv(self) -> None:
        rendered = repr(REQUEST)

        assert CARD_NUMBER not in rendered
        assert CVV not in rendered
        # The non-sensitive fields are still there, so the repr stays useful.
        assert "GBP" in rendered

    def test_the_command_repr_hides_the_pan_and_cvv(self) -> None:
        command = ProcessPaymentCommand(
            merchant_id="merchant_alpha",
            card_number=CARD_NUMBER,
            expiry_month=4,
            expiry_year=2030,
            currency="GBP",
            amount=100,
            cvv=CVV,
        )

        rendered = repr(command)

        assert CARD_NUMBER not in rendered
        assert CVV not in rendered

    def test_a_traceback_interpolating_the_request_stays_clean(self) -> None:
        # An exception raised with the request in its message is a realistic way
        # to leak a PAN; repr=False is what prevents it.
        try:
            raise ValueError(f"failed to process {REQUEST}")
        except ValueError as exc:
            assert CARD_NUMBER not in str(exc)


class TestJsonFormatter:
    def _format(self, record: logging.LogRecord) -> dict[str, object]:
        return json.loads(JsonFormatter().format(record))

    def _record(self, **extra: object) -> logging.LogRecord:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="payment.processed",
            args=None,
            exc_info=None,
        )
        record.__dict__.update(extra)
        return record

    def test_includes_the_standard_fields(self) -> None:
        payload = self._format(self._record())

        assert payload["level"] == "INFO"
        assert payload["message"] == "payment.processed"
        assert "timestamp" in payload

    def test_includes_extra_context(self) -> None:
        payload = self._format(self._record(merchant_id="merchant_alpha", amount=100))

        assert payload["merchant_id"] == "merchant_alpha"
        assert payload["amount"] == 100

    def test_includes_the_current_request_id(self) -> None:
        token = request_id_var.set("request-123")
        try:
            payload = self._format(self._record())
        finally:
            request_id_var.reset(token)

        assert payload["request_id"] == "request-123"

    def test_emits_one_json_object_per_line(self) -> None:
        rendered = JsonFormatter().format(self._record(note="line\nbreak"))

        assert "\n" not in rendered
