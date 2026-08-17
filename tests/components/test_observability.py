"""Correlation ids, error handling and the generated API documentation."""

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from payment_gateway_api.dependencies import get_payment_service
from tests.conftest import ALPHA_HEADERS


def test_every_response_carries_a_request_id(client: TestClient) -> None:
    response = client.get("/")

    assert response.headers["X-Request-Id"]


def test_a_supplied_request_id_is_echoed_back(client: TestClient) -> None:
    response = client.get("/", headers={"X-Request-Id": "merchant-trace-1"})

    assert response.headers["X-Request-Id"] == "merchant-trace-1"


def test_request_ids_differ_between_requests(client: TestClient) -> None:
    first = client.get("/").headers["X-Request-Id"]
    second = client.get("/").headers["X-Request-Id"]

    assert first != second


def test_an_unexpected_error_returns_a_generic_500(
    app: FastAPI, valid_payment_request: dict[str, Any]
) -> None:
    def explode() -> None:
        raise RuntimeError("database on fire: user=admin password=hunter2")

    app.dependency_overrides[get_payment_service] = explode

    # raise_server_exceptions=False makes TestClient behave like a real server.
    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.post("/payments", json=valid_payment_request, headers=ALPHA_HEADERS)

    assert response.status_code == 500
    assert response.json() == {"error": "internal_error", "message": "An unexpected error occurred"}
    # Internal detail must never reach the merchant.
    assert "hunter2" not in response.text


def test_the_openapi_schema_documents_both_endpoints(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()

    assert "/payments" in schema["paths"]
    assert "/payments/{payment_id}" in schema["paths"]
    assert "APIKeyHeader" in schema["components"]["securitySchemes"]
