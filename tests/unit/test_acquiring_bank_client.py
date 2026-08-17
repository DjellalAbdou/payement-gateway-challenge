from collections.abc import AsyncIterator
from typing import TypedDict, Unpack

import httpx
import pytest
import respx

from payment_gateway_api.domain.errors import (
    AcquiringBankError,
    AcquiringBankTimeoutError,
    AcquiringBankUnavailableError,
)
from payment_gateway_api.infrastructure.clients.acquiring_bank import (
    AcquiringBankClient,
)
from payment_gateway_api.infrastructure.clients.models import AuthorizationRequest

BANK_URL = "http://bank.test"
AUTHORIZE_URL = f"{BANK_URL}/payments"

REQUEST = AuthorizationRequest(
    card_number="2222405343248877",
    expiry_month=4,
    expiry_year=2030,
    currency="GBP",
    amount=100,
    cvv="123",
)


class ClientOptions(TypedDict, total=False):
    max_attempts: int
    backoff_seconds: float


def build_client(
    **overrides: Unpack[ClientOptions],
) -> tuple[AcquiringBankClient, httpx.AsyncClient]:
    client = httpx.AsyncClient(base_url=BANK_URL)
    options: ClientOptions = {"max_attempts": 3, "backoff_seconds": 0.0}
    options.update(overrides)
    return AcquiringBankClient(client, **options), client


@pytest.fixture
async def bank() -> AsyncIterator[AcquiringBankClient]:
    adapter, client = build_client()
    yield adapter
    await client.aclose()


class TestSuccessfulCalls:
    @respx.mock
    async def test_authorized_response(self, bank: AcquiringBankClient) -> None:
        respx.post(AUTHORIZE_URL).respond(
            200, json={"authorized": True, "authorization_code": "0bb07405-6d44"}
        )

        result = await bank.authorize(REQUEST)

        assert result.authorized is True
        assert result.authorization_code == "0bb07405-6d44"

    @respx.mock
    async def test_declined_response_normalises_the_empty_code_to_none(
        self, bank: AcquiringBankClient
    ) -> None:
        respx.post(AUTHORIZE_URL).respond(200, json={"authorized": False, "authorization_code": ""})

        result = await bank.authorize(REQUEST)

        assert result.authorized is False
        assert result.authorization_code is None

    @respx.mock
    async def test_sends_the_wire_format_the_simulator_expects(
        self, bank: AcquiringBankClient
    ) -> None:
        route = respx.post(AUTHORIZE_URL).respond(
            200, json={"authorized": True, "authorization_code": "code"}
        )

        await bank.authorize(REQUEST)

        assert route.calls.last.request.read() == (
            b'{"card_number":"2222405343248877","expiry_date":"04/2030",'
            b'"currency":"GBP","amount":100,"cvv":"123"}'
        )

    @respx.mock
    async def test_pads_a_single_digit_expiry_month(self, bank: AcquiringBankClient) -> None:
        route = respx.post(AUTHORIZE_URL).respond(
            200, json={"authorized": True, "authorization_code": "code"}
        )

        await bank.authorize(
            AuthorizationRequest(
                card_number="2222405343248877",
                expiry_month=1,
                expiry_year=2030,
                currency="GBP",
                amount=100,
                cvv="123",
            )
        )

        assert b'"expiry_date":"01/2030"' in route.calls.last.request.read()


class TestFailureHandling:
    @respx.mock
    async def test_a_503_is_retried_then_reported_as_unavailable(
        self, bank: AcquiringBankClient
    ) -> None:
        route = respx.post(AUTHORIZE_URL).respond(503)

        with pytest.raises(AcquiringBankError):
            await bank.authorize(REQUEST)

        assert route.call_count == 3

    @respx.mock
    @pytest.mark.parametrize("status_code", [500, 502, 504])
    async def test_other_5xx_responses_are_not_retried(
        self, bank: AcquiringBankClient, status_code: int
    ) -> None:
        route = respx.post(AUTHORIZE_URL).respond(status_code)

        with pytest.raises(AcquiringBankError):
            await bank.authorize(REQUEST)

        assert route.call_count == 1

    @respx.mock
    async def test_recovers_when_a_retry_succeeds(self, bank: AcquiringBankClient) -> None:
        route = respx.post(AUTHORIZE_URL)
        route.side_effect = [
            httpx.Response(503),
            httpx.Response(200, json={"authorized": True, "authorization_code": "code"}),
        ]

        result = await bank.authorize(REQUEST)

        assert result.authorized is True
        assert route.call_count == 2

    @respx.mock
    async def test_a_connection_failure_is_retried(self, bank: AcquiringBankClient) -> None:
        route = respx.post(AUTHORIZE_URL)
        route.side_effect = [
            httpx.ConnectError("refused"),
            httpx.Response(200, json={"authorized": True, "authorization_code": "code"}),
        ]

        result = await bank.authorize(REQUEST)

        assert result.authorized is True

    @respx.mock
    @pytest.mark.parametrize("timeout", [httpx.ReadTimeout, httpx.WriteTimeout])
    async def test_a_read_or_write_timeout_is_reported_as_an_unknown_outcome(
        self, bank: AcquiringBankClient, timeout: type[httpx.TimeoutException]
    ) -> None:
        route = respx.post(AUTHORIZE_URL)
        route.side_effect = timeout("too slow")

        with pytest.raises(AcquiringBankTimeoutError) as exc_info:
            await bank.authorize(REQUEST)

        assert route.call_count == 1
        # This is what stops the service from freeing the idempotency key.
        assert exc_info.value.definitely_not_processed is False

    @respx.mock
    async def test_a_pool_timeout_is_retried_like_a_connection_failure(
        self, bank: AcquiringBankClient
    ) -> None:
        route = respx.post(AUTHORIZE_URL)
        route.side_effect = [
            httpx.PoolTimeout("no connection available"),
            httpx.Response(200, json={"authorized": True, "authorization_code": "code"}),
        ]

        result = await bank.authorize(REQUEST)

        assert result.authorized is True
        assert route.call_count == 2

    @respx.mock
    async def test_an_outage_is_reported_as_provably_unprocessed(
        self, bank: AcquiringBankClient
    ) -> None:
        respx.post(AUTHORIZE_URL).respond(503)

        with pytest.raises(AcquiringBankUnavailableError) as exc_info:
            await bank.authorize(REQUEST)

        assert exc_info.value.definitely_not_processed is True

    @respx.mock
    async def test_a_400_is_not_retried_and_is_reported_as_unavailable(
        self, bank: AcquiringBankClient
    ) -> None:
        route = respx.post(AUTHORIZE_URL).respond(
            400,
            json={"error_message": "Not all required properties were sent in the request"},
        )

        with pytest.raises(AcquiringBankError):
            await bank.authorize(REQUEST)

        assert route.call_count == 1

    @respx.mock
    @pytest.mark.parametrize(
        "body",
        [
            {"authorization_code": "code"},  # 'authorized' missing
            {"authorized": "yes"},  # not a boolean
            "not json at all",
        ],
    )
    async def test_an_unreadable_response_is_reported_as_unavailable(
        self, bank: AcquiringBankClient, body: object
    ) -> None:
        if isinstance(body, str):
            respx.post(AUTHORIZE_URL).respond(200, text=body)
        else:
            respx.post(AUTHORIZE_URL).respond(200, json=body)

        with pytest.raises(AcquiringBankError):
            await bank.authorize(REQUEST)

    @respx.mock
    async def test_honours_a_single_attempt_configuration(self) -> None:
        adapter, client = build_client(max_attempts=1)
        route = respx.post(AUTHORIZE_URL).respond(503)

        with pytest.raises(AcquiringBankError):
            await adapter.authorize(REQUEST)

        assert route.call_count == 1
        await client.aclose()
