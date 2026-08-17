import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from payment_gateway_api.api.schemas.payment_schema import (
    ErrorResponse,
    FieldError,
    RejectedResponse,
)
from payment_gateway_api.domain.errors import (
    AcquiringBankError,
    AcquiringBankProtocolError,
    AcquiringBankTimeoutError,
    AcquiringBankUnavailableError,
    IdempotencyConflictError,
    PaymentNotFoundError,
)

logger = logging.getLogger(__name__)

# Advertised on an outage so a merchant's client backs off rather than hammering a
# bank that is already in trouble. Only sent where a retry is actually appropriate.
RETRY_AFTER_SECONDS = 5

_STATUS_ERROR_CODES = {
    status.HTTP_401_UNAUTHORIZED: "unauthorized",
    status.HTTP_404_NOT_FOUND: "not_found",
    status.HTTP_405_METHOD_NOT_ALLOWED: "method_not_allowed",
}


def _json(
    status_code: int,
    model: ErrorResponse | RejectedResponse,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content=model.model_dump(), headers=headers
    )


def _field_name(location: tuple[str | int, ...]) -> str:
    """Turn a pydantic error location such as ``("body", "cvv")`` into ``"cvv"``."""
    parts = [
        str(part)
        for part in location
        if part not in ("body", "query", "path", "header")
    ]
    return ".".join(parts) or "body"


_PYDANTIC_MESSAGE_PREFIXES = ("Value error, ", "Assertion failed, ")


def _clean_message(message: str) -> str:
    for prefix in _PYDANTIC_MESSAGE_PREFIXES:
        if message.startswith(prefix):
            return message.removeprefix(prefix)
    return message


def _bank_error_response() -> JSONResponse:
    """The response for a bank failure whose outcome we cannot vouch for.

    Shared by the protocol-error and fallback handlers, which differ only in what
    they log. Deliberately carries no ``Retry-After`` and no promise of retry
    safety: neither case knows whether the payment was taken.
    """
    return _json(
        status.HTTP_502_BAD_GATEWAY,
        ErrorResponse(
            error="acquiring_bank_error",
            message=(
                "The payment could not be processed because of an error "
                "communicating with the acquiring bank. It was not authorized. "
                "Please contact support rather than retrying."
            ),
        ),
    )


def register_bank_exceptions(app: FastAPI) -> None:
    @app.exception_handler(AcquiringBankTimeoutError)
    async def handle_bank_timeout(
        _: Request, exc: AcquiringBankTimeoutError
    ) -> JSONResponse:
        logger.error("acquiring_bank.timeout", extra={"error": str(exc)})
        return _json(
            status.HTTP_504_GATEWAY_TIMEOUT,
            ErrorResponse(
                error="acquiring_bank_timeout",
                message=(
                    "The acquiring bank did not respond in time, so the outcome of this "
                    "payment is unknown and it may still have been authorized. Do not "
                    "retry with a new idempotency key; retrying with the same key will "
                    "report a conflict until the payment has been reconciled."
                ),
            ),
        )

    @app.exception_handler(AcquiringBankProtocolError)
    async def handle_bank_protocol_error(
        _: Request, exc: AcquiringBankProtocolError
    ) -> JSONResponse:
        # The bank answered, but unusably -- which means our integration is at
        # fault, not the merchant's request and not the bank's availability.
        logger.error("acquiring_bank.protocol_error", extra={"error": str(exc)})
        return _bank_error_response()

    @app.exception_handler(AcquiringBankUnavailableError)
    async def handle_bank_unavailable(
        _: Request, exc: AcquiringBankUnavailableError
    ) -> JSONResponse:
        logger.error("acquiring_bank.unavailable", extra={"error": str(exc)})
        return _json(
            status.HTTP_502_BAD_GATEWAY,
            ErrorResponse(
                error="acquiring_bank_unavailable",
                message=(
                    "The acquiring bank could not be reached, so the payment was not "
                    "processed. The request can safely be retried."
                ),
            ),
            headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
        )

    @app.exception_handler(AcquiringBankError)
    async def handle_bank_error(_: Request, exc: AcquiringBankError) -> JSONResponse:
        """Fallback for a bank failure with no more specific handler."""
        logger.error(
            "acquiring_bank.unclassified_error",
            extra={"error": str(exc), "error_type": type(exc).__name__},
        )
        return _bank_error_response()


def register_exception_handlers(app: FastAPI) -> None:
    register_bank_exceptions(app)

    @app.exception_handler(IdempotencyConflictError)
    async def idempotency_conflict_handler(
        _: Request, exc: IdempotencyConflictError
    ) -> JSONResponse:
        return _json(
            status.HTTP_409_CONFLICT,
            ErrorResponse(error="idempotency_conflict", message=str(exc)),
        )

    @app.exception_handler(PaymentNotFoundError)
    async def handle_payment_not_found(
        _: Request, exc: PaymentNotFoundError
    ) -> JSONResponse:
        return _json(
            status.HTTP_404_NOT_FOUND,
            ErrorResponse(error="payment_not_found", message="Payment not found"),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        print(exc)
        errors = [
            FieldError(
                field=_field_name(error["loc"]), message=_clean_message(error["msg"])
            )
            for error in exc.errors()
        ]
        return _json(status.HTTP_400_BAD_REQUEST, RejectedResponse(errors=errors))

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        _: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = _STATUS_ERROR_CODES.get(exc.status_code, "error")
        return _json(
            exc.status_code, ErrorResponse(error=code, message=str(exc.detail))
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        # The traceback goes to the logs; the merchant gets nothing that could
        # disclose internal state.
        logger.exception("unhandled_error")
        return _json(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            ErrorResponse(
                error="internal_error", message="An unexpected error occurred"
            ),
        )
