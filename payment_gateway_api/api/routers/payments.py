from uuid import UUID

from fastapi import APIRouter, status

from payment_gateway_api.api.schemas.payment_schema import (
    ErrorResponse,
    PaymentRequest,
    PaymentResponse,
    ProcessPaymentCommand,
    RejectedResponse,
)
from payment_gateway_api.dependencies import (
    CurrentMerchant,
    IdempotencyKey,
    PaymentServiceDep,
)

router = APIRouter(prefix="/payments", tags=["Payments"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=PaymentResponse,
    summary="Process a card payment",
    responses={
        400: {
            "model": RejectedResponse,
            "description": "Rejected: invalid payment details",
        },
        401: {"model": ErrorResponse},
        409: {"model": ErrorResponse, "description": "Idempotency key conflict"},
        502: {
            "model": ErrorResponse,
            "description": "The acquiring bank was unreachable",
        },
    },
)
async def process_payment(
    request: PaymentRequest,
    paymentService: PaymentServiceDep,
    merchant_id: CurrentMerchant,
    idempotency_key: IdempotencyKey = None,
) -> PaymentResponse:
    payment = await paymentService.process_payment(
        command=ProcessPaymentCommand(
            merchant_id=merchant_id,
            card_number=request.card_number,
            expiry_month=request.expiry_month,
            expiry_year=request.expiry_year,
            currency=request.currency,
            cvv=request.cvv,
            amount=request.amount,
            idempotency_key=idempotency_key,
        )
    )

    return PaymentResponse.from_domain(payment)


@router.get(
    "/{payment_id}",
    response_model=PaymentResponse,
    summary="Retrieve a previously made payment",
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def get_payment(
    payment_id: UUID, merchant_id: CurrentMerchant, payment_service: PaymentServiceDep
) -> PaymentResponse:
    payment = await payment_service.get_payment(payment_id, merchant_id)
    return PaymentResponse.from_domain(payment)
