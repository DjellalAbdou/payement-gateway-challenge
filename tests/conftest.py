from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from payment_gateway_api.app import create_app
from payment_gateway_api.dependencies import get_acquiring_bank
from tests.fakes import FakeAcquiringBank

ALPHA_API_KEY = "sk_test_alpha"
BETA_API_KEY = "sk_test_beta"

ALPHA_HEADERS = {"X-Api-Key": ALPHA_API_KEY}
BETA_HEADERS = {"X-Api-Key": BETA_API_KEY}

AUTHORIZED_CARD = "2222405343248877"
DECLINED_CARD = "2222405343248112"
UNAVAILABLE_CARD = "2222405343248870"


def future_expiry() -> tuple[int, int]:
    """An expiry date that is comfortably in the future, whenever the tests run."""
    now = datetime.now(UTC)
    return now.month, now.year + 2


@pytest.fixture
def valid_payment_request() -> dict[str, object]:
    month, year = future_expiry()
    return {
        "card_number": AUTHORIZED_CARD,
        "expiry_month": month,
        "expiry_year": year,
        "currency": "GBP",
        "amount": 1050,
        "cvv": "123",
    }


@pytest.fixture
def fake_bank() -> FakeAcquiringBank:
    return FakeAcquiringBank()


@pytest.fixture
def app(fake_bank: FakeAcquiringBank) -> FastAPI:
    application = create_app()
    application.dependency_overrides[get_acquiring_bank] = lambda: fake_bank
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # The context manager runs startup/shutdown, so the app is exercised the same
    # way it is in production.
    with TestClient(app) as test_client:
        yield test_client
