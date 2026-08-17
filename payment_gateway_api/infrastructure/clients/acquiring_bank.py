import asyncio
import logging
import random

import httpx

from payment_gateway_api.domain.errors import (
    AcquiringBankProtocolError,
    AcquiringBankTimeoutError,
    AcquiringBankUnavailableError,
)
from payment_gateway_api.infrastructure.clients.models import (
    AuthorizationRequest,
    AuthorizationResult,
)

logger = logging.getLogger(__name__)

_AUTHORIZE_PATH = "/payments"
_RETRYABLE_EXCEPTIONS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
_RETRYABLE_STATUS_CODES = frozenset({503})


class AcquiringBankClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        max_attempts: int = 3,
        backoff_seconds: float = 0.2,
    ) -> None:
        self._client = client
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationResult:
        payload = {
            "card_number": request.card_number,
            "expiry_date": f"{request.expiry_month:02d}/{request.expiry_year}",
            "currency": request.currency,
            "amount": request.amount,
            "cvv": request.cvv,
        }

        last_error = ""
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._client.post(_AUTHORIZE_PATH, json=payload)
            except _RETRYABLE_EXCEPTIONS as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "acquiring_bank.connection_failed",
                    extra={"attempt": attempt, "error": last_error},
                )
            # request reached the bank and maybe processed
            except httpx.TimeoutException as exc:
                logger.error(
                    "acquiring_bank.timed_out",
                    extra={"attempt": attempt, "error": str(exc)},
                )
                raise AcquiringBankTimeoutError(
                    "The acquiring bank did not respond in time; the outcome is unknown"
                ) from exc
            else:
                if response.status_code not in _RETRYABLE_STATUS_CODES:
                    return self._parse(response)
                last_error = f"HTTP {response.status_code}"
                logger.warning(
                    "acquiring_bank.unavailable",
                    extra={"attempt": attempt, "status": last_error},
                )

            if attempt < self._max_attempts:
                await asyncio.sleep(self._backoff_delay(attempt))

        raise AcquiringBankUnavailableError(
            f"Failed to authorize payment after {self._max_attempts} attempts: {last_error}"
        )

    def _backoff_delay(self, attempt: int) -> float:
        """Exponential backoff with full jitter, to avoid a retry stampede."""
        return random.uniform(0, self._backoff_seconds * (2 ** (attempt - 1)))

    def _parse(self, response: httpx.Response) -> AuthorizationResult:
        if response.status_code != httpx.codes.OK:
            # The 400 error can be caught here if we send an invalid or missing fields
            logger.error(
                "acquiring_bank.rejected_request",
                extra={"status_code": response.status_code},
            )
            raise AcquiringBankProtocolError(f"Acquiring bank returned HTTP {response.status_code}")

        try:
            body = response.json()
            authorized = body["authorized"]
        except (ValueError, KeyError, TypeError) as exc:
            logger.error("acquiring_bank.unparsable_response")
            raise AcquiringBankProtocolError(
                "Acquiring bank returned an unreadable response"
            ) from exc

        if not isinstance(authorized, bool):
            raise AcquiringBankProtocolError("Acquiring bank returned a non-boolean 'authorized'")

        # The simulator sends an empty string rather than omitting the code on a
        # decline; normalise that to None.
        return AuthorizationResult(
            authorized=authorized,
            authorization_code=body.get("authorization_code") or None,
        )
