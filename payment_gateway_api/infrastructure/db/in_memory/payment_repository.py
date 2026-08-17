import asyncio
from uuid import UUID

from payment_gateway_api.domain.models.payment import Payment


class InMemoryPaymentRepository:
    def __init__(self) -> None:
        self._payments: dict[UUID, Payment] = {}
        # We try to mimick the work of a transaction using a lock to avoid concurrent read/writes that will take different results
        self._lock = asyncio.Lock()

    async def add(self, payment: Payment) -> None:
        async with self._lock:
            self._payments[payment.id] = payment

    async def get(self, payment_id: UUID, merchant_id: str) -> Payment | None:
        async with self._lock:
            payment = self._payments.get(payment_id)
            # we shouldnt return the payments just by UUID
            # because we didnt check about the merchant id
            if payment and payment.merchant_id == merchant_id:
                return payment

            return None
