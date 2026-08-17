from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class PaymentStatus(StrEnum):
    AUTHORIZED = "Authorized"
    DECLINED = "Declined"


@dataclass(frozen=True, kw_only=True)
class Payment:
    id: UUID
    merchant_id: str
    status: PaymentStatus
    last_four_card_digits: str
    expiry_month: int
    expiry_year: int
    currency: str
    amount: int
    created_at: datetime
    authorization_code: str | None = None
