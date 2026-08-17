import asyncio
from uuid import UUID

from payment_gateway_api.domain.protocols.idempotency_store import IdempotencyRecord


class InMemoryIdempotencyStore:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str], IdempotencyRecord] = {}
        self._lock = asyncio.Lock()

    async def reserve(
        self, merchant_id: str, key: str, request_fingerprint: str
    ) -> IdempotencyRecord | None:
        async with self._lock:
            existing = self._records.get((merchant_id, key))
            if existing:
                return existing

            self._records[(merchant_id, key)] = IdempotencyRecord(
                request_fingerprint=request_fingerprint
            )

            return None

    async def release(self, merchant_id: str, key: str) -> None:
        async with self._lock:
            self._records.pop((merchant_id, key))

    async def complete(self, merchant_id: str, key: str, payment_id: UUID) -> None:
        async with self._lock:
            record = self._records[(merchant_id, key)]
            self._records[(merchant_id, key)] = IdempotencyRecord(
                request_fingerprint=record.request_fingerprint, payment_id=payment_id
            )
