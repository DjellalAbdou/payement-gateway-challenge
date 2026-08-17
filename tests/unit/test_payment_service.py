"""Service-level behaviour, with every protocol replaced by a fake."""

from uuid import uuid4

import pytest

from payment_gateway_api.api.schemas.payment_schema import ProcessPaymentCommand
from payment_gateway_api.api.services.payment_service import PaymentService
from payment_gateway_api.domain.errors import (
    AcquiringBankError,
    AcquiringBankTimeoutError,
    IdempotencyConflictError,
    PaymentNotFoundError,
)
from payment_gateway_api.domain.models.payment import PaymentStatus
from payment_gateway_api.infrastructure.db.in_memory.idempotency_store import (
    InMemoryIdempotencyStore,
)
from payment_gateway_api.infrastructure.db.in_memory.payment_repository import (
    InMemoryPaymentRepository,
)
from tests.conftest import future_expiry
from tests.fakes import FakeAcquiringBank

MERCHANT = "merchant_alpha"
OTHER_MERCHANT = "merchant_beta"


def build_command(**overrides: object) -> ProcessPaymentCommand:
    month, year = future_expiry()
    defaults: dict[str, object] = {
        "merchant_id": MERCHANT,
        "card_number": "2222405343248877",
        "expiry_month": month,
        "expiry_year": year,
        "currency": "GBP",
        "amount": 1050,
        "cvv": "123",
    }
    defaults.update(overrides)
    return ProcessPaymentCommand(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def repository() -> InMemoryPaymentRepository:
    return InMemoryPaymentRepository()


@pytest.fixture
def idempotency_store() -> InMemoryIdempotencyStore:
    return InMemoryIdempotencyStore()


@pytest.fixture
def bank() -> FakeAcquiringBank:
    return FakeAcquiringBank()


@pytest.fixture
def service(
    bank: FakeAcquiringBank,
    repository: InMemoryPaymentRepository,
    idempotency_store: InMemoryIdempotencyStore,
) -> PaymentService:
    return PaymentService(repository, idempotency_store, bank)


class TestProcessPayment:
    async def test_authorized_payment_is_stored(
        self, service: PaymentService, repository: InMemoryPaymentRepository
    ) -> None:
        payment = await service.process_payment(build_command())

        assert payment.status is PaymentStatus.AUTHORIZED
        assert payment.last_four_card_digits == "8877"
        assert await repository.get(payment.id, MERCHANT) == payment

    async def test_declined_payment_is_stored(
        self, bank: FakeAcquiringBank, service: PaymentService
    ) -> None:
        bank.authorized = False

        payment = await service.process_payment(build_command())

        assert payment.status is PaymentStatus.DECLINED
        assert payment.authorization_code is None

    async def test_only_the_last_four_digits_are_retained(self, service: PaymentService) -> None:
        payment = await service.process_payment(build_command(card_number="4111111111111111"))

        assert payment.last_four_card_digits == "1111"
        # The full PAN must not be reachable from the stored payment at all.
        assert "4111111111111111" not in str(payment.__dict__)

    async def test_the_full_card_number_reaches_the_bank(
        self, bank: FakeAcquiringBank, service: PaymentService
    ) -> None:
        await service.process_payment(build_command(card_number="4111111111111111"))

        assert bank.requests[0].card_number == "4111111111111111"

    async def test_nothing_is_stored_when_the_bank_is_unavailable(
        self,
        bank: FakeAcquiringBank,
        service: PaymentService,
        repository: InMemoryPaymentRepository,
    ) -> None:
        bank.fail_with_unavailable()

        with pytest.raises(AcquiringBankError):
            await service.process_payment(build_command())

        # An unknown outcome must never be recorded as a payment.
        assert repository._payments == {}


class TestGetPayment:
    async def test_returns_a_previously_made_payment(self, service: PaymentService) -> None:
        created = await service.process_payment(build_command())

        assert await service.get_payment(created.id, MERCHANT) == created

    async def test_raises_for_an_unknown_id(self, service: PaymentService) -> None:
        with pytest.raises(PaymentNotFoundError):
            await service.get_payment(uuid4(), MERCHANT)

    async def test_a_merchant_cannot_read_another_merchants_payment(
        self, service: PaymentService
    ) -> None:
        created = await service.process_payment(build_command())

        with pytest.raises(PaymentNotFoundError):
            await service.get_payment(created.id, OTHER_MERCHANT)


class TestIdempotency:
    async def test_replaying_a_key_returns_the_original_payment(
        self, bank: FakeAcquiringBank, service: PaymentService
    ) -> None:
        command = build_command(idempotency_key="key-1")

        first = await service.process_payment(command)
        second = await service.process_payment(command)

        assert first.id == second.id
        # Crucially, the shopper was only charged once.
        assert bank.call_count == 1

    async def test_reusing_a_key_with_a_different_request_is_a_conflict(
        self, service: PaymentService
    ) -> None:
        # The alternative would be to silently return the first payment, leaving a
        # merchant who meant to charge 9999 believing they had, when in fact 1050
        # was taken. A conflict tells them immediately instead.
        first = await service.process_payment(build_command(idempotency_key="key-1"))

        with pytest.raises(IdempotencyConflictError):
            await service.process_payment(build_command(idempotency_key="key-1", amount=9999))

        assert first.amount == 1050

    async def test_the_same_key_is_scoped_to_one_merchant(
        self, bank: FakeAcquiringBank, service: PaymentService
    ) -> None:
        first = await service.process_payment(build_command(idempotency_key="key-1"))
        second = await service.process_payment(
            build_command(idempotency_key="key-1", merchant_id=OTHER_MERCHANT)
        )

        assert first.id != second.id
        assert bank.call_count == 2

    async def test_a_key_is_reusable_after_the_bank_failed(
        self, bank: FakeAcquiringBank, service: PaymentService
    ) -> None:
        # The first attempt never produced a payment, so the merchant must be able
        # to retry safely with the same key.
        bank.fail_with_unavailable()
        with pytest.raises(AcquiringBankError):
            await service.process_payment(build_command(idempotency_key="key-1"))

        bank.error = None
        payment = await service.process_payment(build_command(idempotency_key="key-1"))

        assert payment.status is PaymentStatus.AUTHORIZED

    async def test_a_key_stays_claimed_after_a_timeout(
        self, bank: FakeAcquiringBank, service: PaymentService
    ) -> None:
        # The payment may already have been taken, so replaying the key must NOT
        # reach the bank again, that would be the double charge this whole
        # mechanism exists to prevent.
        bank.fail_with_timeout()
        with pytest.raises(AcquiringBankTimeoutError):
            await service.process_payment(build_command(idempotency_key="key-1"))

        bank.error = None
        with pytest.raises(IdempotencyConflictError):
            await service.process_payment(build_command(idempotency_key="key-1"))

        assert bank.call_count == 1

    async def test_an_unexpected_error_also_leaves_the_key_claimed(
        self, bank: FakeAcquiringBank, service: PaymentService
    ) -> None:
        # An error we did not anticipate says nothing about whether the payment was
        # taken, so the safe default applies.
        bank.error = RuntimeError("something we did not foresee")
        with pytest.raises(RuntimeError):
            await service.process_payment(build_command(idempotency_key="key-1"))

        bank.error = None
        with pytest.raises(IdempotencyConflictError):
            await service.process_payment(build_command(idempotency_key="key-1"))

    async def test_without_a_key_identical_requests_create_separate_payments(
        self, bank: FakeAcquiringBank, service: PaymentService
    ) -> None:
        first = await service.process_payment(build_command())
        second = await service.process_payment(build_command())

        assert first.id != second.id
        assert bank.call_count == 2
