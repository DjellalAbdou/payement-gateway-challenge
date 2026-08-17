from typing import Protocol

from payment_gateway_api.infrastructure.clients.models import (
    AuthorizationRequest,
    AuthorizationResult,
)


class AcquiringBank(Protocol):
    async def authorize(self, request: AuthorizationRequest) -> AuthorizationResult: ...
