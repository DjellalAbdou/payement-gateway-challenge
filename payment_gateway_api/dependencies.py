from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from payment_gateway_api.api.services.payment_service import PaymentService
from payment_gateway_api.config import Settings, get_settings
from payment_gateway_api.domain.protocols.acquiring_bank import AcquiringBank
from payment_gateway_api.domain.protocols.idempotency_store import IdempotencyStore
from payment_gateway_api.domain.protocols.payment_repository import PaymentRepository

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)


# request signin
# private secret keys
def get_current_merchant(
    api_key: Annotated[str, Security(api_key_header)],
    store: Annotated[Settings, Depends(get_settings)],
) -> str:
    merchant_id = store.api_keys.get(api_key)
    if not merchant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid merchant key, a valid X-API-Key header is required",
        )
    return merchant_id


def get_payment_repository(request: Request) -> PaymentRepository:
    return request.app.state.payment_repository


def get_idempotency_store(request: Request) -> IdempotencyStore:
    return request.app.state.idempotency_store


def get_acquiring_bank(request: Request) -> AcquiringBank:
    return request.app.state.acquiring_bank


def get_payment_service(
    repository: Annotated[PaymentRepository, Depends(get_payment_repository)],
    idempotency_store: Annotated[IdempotencyStore, Depends(get_idempotency_store)],
    bank_client: Annotated[AcquiringBank, Depends(get_acquiring_bank)],
) -> PaymentService:
    return PaymentService(
        repository=repository,
        idempotency_store=idempotency_store,
        bank_client=bank_client,
    )


IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        max_length=36,
        min_length=36,
        strict=True,
        description=(
            "A unique uuid key with hyphens provided by the merchant to"
            "ensure idempotent payment requests. Must be exactly 36 characters long."
        ),
    ),
]

CurrentMerchant = Annotated[str, Depends(get_current_merchant)]

# inject the right repository and idempotency store, we can change them easily for testing or if we want to change the implementation in the future"""
PaymentServiceDep = Annotated[PaymentService, Depends(get_payment_service)]
