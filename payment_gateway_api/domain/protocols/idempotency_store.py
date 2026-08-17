from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, kw_only=True)
class IdempotencyRecord:
    request_fingerprint: str
    payment_id: UUID | None = None


@dataclass(frozen=True, kw_only=True)
class IdempotencyStore(Protocol):
    async def reserve(
        self, merchant_id: str, key: str, request_finderprint: str
    ) -> IdempotencyRecord | None: ...

    async def release(self, merchant_id: str, key: str) -> None: ...
    async def complete(self, merchant_id: str, key: str, payment_id: UUID) -> None: ...
