from typing import Protocol
from uuid import UUID

from payment_gateway_api.domain.models.payment import Payment


class PaymentRepository(Protocol):
    async def add(self, payment: Payment) -> None: ...
    async def get(self, payment_id: UUID, merchant_id: str) -> Payment | None: ...
