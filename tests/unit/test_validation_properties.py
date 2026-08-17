"""Property-based validation tests.

The example-based tests check the rules we thought of. These check the rules hold
for inputs we did not think of: whatever is thrown at the validator, it either
produces a well-formed request or a clean ``ValidationError`` -- never a crash, and
never a value that breaks the rules.
"""

from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from payment_gateway_api.api.schemas.payment_schema import PaymentRequest
from tests.conftest import future_expiry

MONTH, YEAR = future_expiry()

# Anything a JSON body could plausibly contain.
json_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | st.text(),
    lambda children: (
        st.lists(children, max_size=3) | st.dictionaries(st.text(), children, max_size=3)
    ),
    max_leaves=3,
)


@settings(max_examples=300)
@given(
    card_number=json_values,
    expiry_month=json_values,
    expiry_year=json_values,
    currency=json_values,
    amount=json_values,
    cvv=json_values,
)
def test_arbitrary_input_never_crashes_the_validator(**payload: object) -> None:
    try:
        request = PaymentRequest(**payload)  # pyright: ignore[reportArgumentType]
    except ValidationError:
        return  # Rejected cleanly, which is the expected outcome.

    # If it validated, every rule from the brief must actually hold.
    assert 14 <= len(request.card_number) <= 19
    assert request.card_number.isascii() and request.card_number.isdigit()
    assert 1 <= request.expiry_month <= 12
    assert request.currency in {"GBP", "USD", "EUR"}
    assert request.amount >= 1
    assert 3 <= len(request.cvv) <= 4


@settings(max_examples=200)
@given(card_number=st.text(alphabet="0123456789", min_size=0, max_size=25))
def test_a_card_number_is_accepted_exactly_when_its_length_is_in_range(card_number: str) -> None:
    payload = {
        "card_number": card_number,
        "expiry_month": MONTH,
        "expiry_year": YEAR,
        "currency": "GBP",
        "amount": 100,
        "cvv": "123",
    }
    try:
        PaymentRequest(**payload)
    except ValidationError:
        assert not 14 <= len(card_number) <= 19
    else:
        assert 14 <= len(card_number) <= 19


@settings(max_examples=200)
@given(amount=st.integers())
def test_an_amount_is_accepted_exactly_when_it_is_within_bounds(amount: int) -> None:
    payload = {
        "card_number": "2222405343248877",
        "expiry_month": MONTH,
        "expiry_year": YEAR,
        "currency": "GBP",
        "amount": amount,
        "cvv": "123",
    }
    try:
        PaymentRequest(**payload)
    except ValidationError:
        assert not 1 <= amount <= 100_000_000_000
    else:
        assert 1 <= amount <= 100_000_000_000
