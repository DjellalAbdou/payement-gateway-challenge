from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # The context manager runs startup/shutdown, so the app is exercised the same
    # way it is in production.
    with TestClient(app) as test_client:
        yield test_client


def future_expiry() -> tuple[int, int]:
    """An expiry date that is comfortably in the future, whenever the tests run."""
    now = datetime.now(UTC)
    return now.month, now.year + 2
