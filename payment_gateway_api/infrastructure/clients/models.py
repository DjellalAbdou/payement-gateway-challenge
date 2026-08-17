from dataclasses import dataclass, field


@dataclass(frozen=True, kw_only=True)
class AuthorizationRequest:
    card_number: str = field(repr=False)
    expiry_month: int
    expiry_year: int
    currency: str
    amount: int
    cvv: str = field(repr=False)

    @property
    def last_four_digits(self) -> str:
        return self.card_number[-4:]


@dataclass(frozen=True, kw_only=True)
class AuthorizationResult:
    authorized: bool
    authorization_code: str | None = None  # empty when declined
