"""Validation rules from the brief, exercised one rule at a time."""

import pytest
import time_machine
from pydantic import ValidationError

from payment_gateway_api.api.schemas.payment_schema import PaymentRequest
from tests.conftest import future_expiry

# pyright: reportArgumentType=false
# build_request() returns dict[str, object] on purpose: several tests pass
# deliberately wrong-typed values (int, None, ...) to assert runtime validation.


def build_request(**overrides: object) -> dict[str, object]:
    month, year = future_expiry()
    payload: dict[str, object] = {
        "card_number": "2222405343248877",
        "expiry_month": month,
        "expiry_year": year,
        "currency": "GBP",
        "amount": 1050,
        "cvv": "123",
    }
    payload.update(overrides)
    return payload


def field_errors(exc: ValidationError) -> dict[str, str]:
    return {str(error["loc"][-1]): error["msg"] for error in exc.errors()}


class TestCardNumber:
    @pytest.mark.parametrize("card_number", ["1" * 14, "1" * 19, "2222405343248877"])
    def test_accepts_14_to_19_digits(self, card_number: str) -> None:
        request = PaymentRequest(**build_request(card_number=card_number))
        assert request.card_number == card_number

    @pytest.mark.parametrize("card_number", ["1" * 13, "1" * 20, ""])
    def test_rejects_lengths_outside_the_range(self, card_number: str) -> None:
        with pytest.raises(ValidationError) as exc_info:
            PaymentRequest(**build_request(card_number=card_number))
        assert "card_number" in field_errors(exc_info.value)

    @pytest.mark.parametrize(
        "card_number",
        [
            "4111-1111-1111-1111",  # separators
            "4111 1111 1111 1111",  # spaces
            "411111111111111a",  # letter
            "٤١١١١١١١١١١١١١١١",  # arabic-indic digits: str.isdigit() would allow these
        ],
    )
    def test_rejects_non_numeric_characters(self, card_number: str) -> None:
        with pytest.raises(ValidationError) as exc_info:
            PaymentRequest(**build_request(card_number=card_number))
        assert "card_number" in field_errors(exc_info.value)

    def test_rejects_a_card_number_sent_as_a_json_number(self) -> None:
        # Leading zeros would be lost, so a numeric PAN is a client bug.
        with pytest.raises(ValidationError):
            PaymentRequest(**build_request(card_number=2222405343248877))

    def test_rejects_a_missing_card_number(self) -> None:
        payload = build_request()
        del payload["card_number"]
        with pytest.raises(ValidationError) as exc_info:
            PaymentRequest(**payload)
        assert "card_number" in field_errors(exc_info.value)


class TestExpiry:
    @pytest.mark.parametrize("expiry_month", [1, 6, 12])
    def test_accepts_months_1_to_12(self, expiry_month: int) -> None:
        _, year = future_expiry()
        assert (
            PaymentRequest(
                **build_request(expiry_month=expiry_month, expiry_year=year)
            ).expiry_month
            == expiry_month
        )

    @pytest.mark.parametrize("expiry_month", [0, 13, -1])
    def test_rejects_months_outside_1_to_12(self, expiry_month: int) -> None:
        with pytest.raises(ValidationError) as exc_info:
            PaymentRequest(**build_request(expiry_month=expiry_month))
        assert "expiry_month" in field_errors(exc_info.value)

    @time_machine.travel("2026-08-15T12:00:00Z")
    def test_accepts_a_card_expiring_this_month(self) -> None:
        # A card is valid through the last day of its expiry month.
        request = PaymentRequest(**build_request(expiry_month=8, expiry_year=2026))
        assert request.expiry_month == 8

    @time_machine.travel("2026-08-15T12:00:00Z")
    def test_rejects_last_month(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            PaymentRequest(**build_request(expiry_month=7, expiry_year=2026))
        assert "Card expiry date 7/2026 is in the past" in str(exc_info.value)

    @time_machine.travel("2026-08-15T12:00:00Z")
    def test_rejects_a_past_year_even_with_a_later_month(self) -> None:
        with pytest.raises(ValidationError):
            PaymentRequest(**build_request(expiry_month=12, expiry_year=2025))

    @time_machine.travel("2026-12-31T23:59:00Z")
    def test_accepts_december_of_the_current_year(self) -> None:
        assert PaymentRequest(**build_request(expiry_month=12, expiry_year=2026))


class TestCurrency:
    @pytest.mark.parametrize("currency", ["GBP", "USD", "EUR"])
    def test_accepts_supported_currencies(self, currency: str) -> None:
        assert PaymentRequest(**build_request(currency=currency)).currency == currency

    def test_normalises_case(self) -> None:
        assert PaymentRequest(**build_request(currency="gbp")).currency == "GBP"

    @pytest.mark.parametrize("currency", ["JPY", "CHF", "XXX"])
    def test_rejects_unsupported_currencies(self, currency: str) -> None:
        with pytest.raises(ValidationError) as exc_info:
            PaymentRequest(**build_request(currency=currency))
        assert "currency" in field_errors(exc_info.value)

    @pytest.mark.parametrize("currency", ["GB", "GBPP", ""])
    def test_rejects_codes_that_are_not_three_characters(self, currency: str) -> None:
        with pytest.raises(ValidationError) as exc_info:
            PaymentRequest(**build_request(currency=currency))
        if len(currency) < 3:
            assert "String should have at least 3 characters" in str(exc_info.value)
        else:
            assert "String should have at most 3 characters" in str(exc_info.value)


class TestAmount:
    @pytest.mark.parametrize("amount", [1, 1050, 100_000_000_000])
    def test_accepts_positive_integers(self, amount: int) -> None:
        assert PaymentRequest(**build_request(amount=amount)).amount == amount

    @pytest.mark.parametrize("amount", [0, -1, -1050])
    def test_rejects_zero_and_negative_amounts(self, amount: int) -> None:
        with pytest.raises(ValidationError) as exc_info:
            PaymentRequest(**build_request(amount=amount))
        assert "amount" in field_errors(exc_info.value)

    @pytest.mark.parametrize("amount", [10.5, "1050", None])
    def test_rejects_non_integers(self, amount: object) -> None:
        # 10.50 is a common mistake: the amount is in minor units already.
        with pytest.raises(ValidationError) as exc_info:
            PaymentRequest(**build_request(amount=amount))
        assert "amount" in field_errors(exc_info.value)

    def test_rejects_an_absurd_amount(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            PaymentRequest(**build_request(amount=10**15))
        assert "amount" in field_errors(exc_info.value)


class TestCvv:
    @pytest.mark.parametrize("cvv", ["123", "1234"])
    def test_accepts_three_or_four_digits(self, cvv: str) -> None:
        assert PaymentRequest(**build_request(cvv=cvv)).cvv == cvv

    @pytest.mark.parametrize("cvv", ["12", "12345", ""])
    def test_rejects_other_lengths(self, cvv: str) -> None:
        with pytest.raises(ValidationError) as exc_info:
            PaymentRequest(**build_request(cvv=cvv))
        assert "cvv" in field_errors(exc_info.value)

    @pytest.mark.parametrize("cvv", ["12a", "1 2", 123])
    def test_rejects_non_numeric_cvvs(self, cvv: object) -> None:
        with pytest.raises(ValidationError) as exc_info:
            PaymentRequest(**build_request(cvv=cvv))
        assert "cvv" in field_errors(exc_info.value)


class TestUnknownFields:
    def test_rejects_unknown_fields(self) -> None:
        # A typo such as "ammount" must be reported, never silently dropped.
        with pytest.raises(ValidationError) as exc_info:
            PaymentRequest(**build_request(ammount=1050))
        assert "ammount" in field_errors(exc_info.value)


def test_reports_every_invalid_field_at_once() -> None:
    with pytest.raises(ValidationError) as exc_info:
        PaymentRequest(**build_request(card_number="abc", cvv="1", currency="JPY", amount=0))
    assert {"card_number", "cvv", "currency", "amount"} <= set(field_errors(exc_info.value))
