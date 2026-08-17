"""The in-memory adapters, including their behaviour under concurrency."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from payment_gateway_api.domain.models.payment import Payment, PaymentStatus
from payment_gateway_api.infrastructure.db.in_memory.idempotency_store import (
    InMemoryIdempotencyStore,
)
from payment_gateway_api.infrastructure.db.in_memory.payment_repository import (
    InMemoryPaymentRepository,
)

MERCHANT = "merchant_alpha"


def build_payment(merchant_id: str = MERCHANT) -> Payment:
    return Payment(
        id=uuid4(),
        merchant_id=merchant_id,
        status=PaymentStatus.AUTHORIZED,
        last_four_card_digits="8877",
        expiry_month=4,
        expiry_year=2030,
        currency="GBP",
        amount=100,
        created_at=datetime.now(UTC),
        authorization_code="code",
    )


class TestRepository:
    async def test_stores_and_returns_a_payment(self) -> None:
        repository = InMemoryPaymentRepository()
        payment = build_payment()

        await repository.add(payment)

        assert await repository.get(payment.id, MERCHANT) == payment

    async def test_returns_none_for_an_unknown_id(self) -> None:
        assert await InMemoryPaymentRepository().get(uuid4(), MERCHANT) is None

    async def test_hides_payments_belonging_to_another_merchant(self) -> None:
        repository = InMemoryPaymentRepository()
        payment = build_payment()
        await repository.add(payment)

        assert await repository.get(payment.id, "merchant_beta") is None

    async def test_concurrent_writes_are_all_persisted(self) -> None:
        repository = InMemoryPaymentRepository()
        payments = [build_payment() for _ in range(50)]

        await asyncio.gather(*(repository.add(payment) for payment in payments))

        stored = await asyncio.gather(
            *(repository.get(p.id, MERCHANT) for p in payments)
        )
        assert stored == payments


class TestIdempotencyStore:
    async def test_the_first_reservation_wins_and_returns_none(self) -> None:
        store = InMemoryIdempotencyStore()

        assert await store.reserve(MERCHANT, "key", "fingerprint") is None

    async def test_a_second_reservation_sees_the_existing_record(self) -> None:
        store = InMemoryIdempotencyStore()
        await store.reserve(MERCHANT, "key", "fingerprint")

        record = await store.reserve(MERCHANT, "key", "fingerprint")

        assert record is not None
        # Still in progress: no payment has been attached yet.
        assert record.payment_id is None

    async def test_completing_attaches_the_payment_id(self) -> None:
        store = InMemoryIdempotencyStore()
        payment_id = uuid4()
        await store.reserve(MERCHANT, "key", "fingerprint")

        await store.complete(MERCHANT, "key", payment_id)

        record = await store.reserve(MERCHANT, "key", "fingerprint")
        assert record is not None
        assert record.payment_id == payment_id

    async def test_releasing_frees_the_key_for_a_retry(self) -> None:
        store = InMemoryIdempotencyStore()
        await store.reserve(MERCHANT, "key", "fingerprint")

        await store.release(MERCHANT, "key")

        assert await store.reserve(MERCHANT, "key", "fingerprint") is None

    async def test_keys_are_scoped_per_merchant(self) -> None:
        store = InMemoryIdempotencyStore()
        await store.reserve(MERCHANT, "key", "fingerprint")

        assert await store.reserve("merchant_beta", "key", "fingerprint") is None

    async def test_only_one_of_many_concurrent_reservations_wins(self) -> None:
        # This is the property that stops a double charge when a merchant fires
        # the same request twice at once.
        store = InMemoryIdempotencyStore()

        results = await asyncio.gather(
            *(store.reserve(MERCHANT, "key", "fingerprint") for _ in range(25))
        )

        assert sum(1 for result in results if result is None) == 1
