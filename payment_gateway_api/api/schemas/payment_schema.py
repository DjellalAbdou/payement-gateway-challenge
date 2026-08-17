from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from payment_gateway_api.config import get_settings
from payment_gateway_api.domain.models.payment import Payment, PaymentStatus

# Guards against a nonsensical year while leaving room for long-dated cards.
MIN_EXPIRY_YEAR = 2000
MAX_EXPIRY_YEAR = 2100


class PaymentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    card_number: str = Field(min_length=14, max_length=19, pattern=r"^\d+$")
    expiry_month: int = Field(ge=1, le=12)
    expiry_year: int = Field(ge=MIN_EXPIRY_YEAR, le=MAX_EXPIRY_YEAR)
    currency: str = Field(min_length=3, max_length=3)
    cvv: str = Field(min_length=3, max_length=4, pattern=r"^\d+$")
    amount: int

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        supported = get_settings().supported_currencies
        currency = value.upper()
        if currency not in supported:
            raise ValueError(
                f"Unsupported currency: {currency}, currency must be one of {', '.join(supported)}"
            )
        return currency

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: int) -> int:
        max_amount = get_settings().max_amount_minor_units
        min_amount = get_settings().min_amount_minor_units
        if not (min_amount <= value <= max_amount):
            raise ValueError(
                f"Amount must be between {min_amount} and {max_amount} minor units"
            )
        return value

    @model_validator(mode="after")
    def validate_expiry_in_the_future(self) -> "PaymentRequest":
        now = datetime.now(UTC)
        # we accept cards that expire in the current month, so we check for strictly less than the current month/year
        if (self.expiry_year, self.expiry_month) < (now.year, now.month):
            raise ValueError(
                f"Card expiry date {self.expiry_month}/{self.expiry_year} is in the past"
            )
        return self


# intent to change state
@dataclass(frozen=True, kw_only=True)
class ProcessPaymentCommand:
    """A validated payment request that is ready to be processed"""

    merchant_id: str
    card_number: str = field(repr=False)
    expiry_month: int
    expiry_year: int
    currency: str
    amount: int
    cvv: str = field(repr=False)
    idempotency_key: str | None = None


class PaymentResponse(BaseModel):
    id: str
    status: PaymentStatus
    last_four_card_digits: str
    expiry_month: int
    expiry_year: int
    currency: str
    amount: int

    @classmethod
    def from_domain(cls, payment: Payment) -> "PaymentResponse":
        return cls(
            id=str(payment.id),
            status=payment.status,
            last_four_card_digits=payment.last_four_card_digits,
            expiry_month=payment.expiry_month,
            expiry_year=payment.expiry_year,
            currency=payment.currency,
            amount=payment.amount,
        )


class FieldError(BaseModel):
    field: str
    message: str


class RejectedResponse(BaseModel):
    """Returned when the request never reached the acquiring bank."""

    status: str = "Rejected"
    errors: list[FieldError]


class ErrorResponse(BaseModel):
    error: str
    message: str
