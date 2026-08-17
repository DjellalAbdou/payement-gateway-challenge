from payment_gateway_api.domain.errors import (
    AcquiringBankProtocolError,
    AcquiringBankTimeoutError,
    AcquiringBankUnavailableError,
)
from payment_gateway_api.infrastructure.clients.models import (
    AuthorizationRequest,
    AuthorizationResult,
)


class FakeAcquiringBank:
    """A programmable stand-in for the acquiring bank.

    Records every request it receives, which is how the tests assert that a
    replayed idempotent request never reaches the bank a second time.
    """

    def __init__(
        self,
        *,
        authorized: bool = True,
        authorization_code: str | None = "test-auth-code",
        error: Exception | None = None,
    ) -> None:
        self.authorized = authorized
        self.authorization_code = authorization_code
        self.error = error
        self.requests: list[AuthorizationRequest] = []

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationResult:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return AuthorizationResult(
            authorized=self.authorized,
            authorization_code=self.authorization_code if self.authorized else None,
        )

    @property
    def call_count(self) -> int:
        return len(self.requests)

    def fail_with_unavailable(self) -> None:
        """Simulate an outage that provably took no payment."""
        self.error = AcquiringBankUnavailableError("simulated outage")

    def fail_with_timeout(self) -> None:
        """Simulate a timeout: the payment may or may not have been taken."""
        self.error = AcquiringBankTimeoutError("simulated timeout")

    def fail_with_protocol_error(self) -> None:
        """Simulate the bank answering unusably, i.e. a bug in our integration."""
        self.error = AcquiringBankProtocolError("simulated protocol error")
