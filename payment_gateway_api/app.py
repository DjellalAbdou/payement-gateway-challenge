from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from payment_gateway_api.config import Settings, get_settings
from payment_gateway_api.infrastructure.acquiring_bank import AcquiringBankClient


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        async with httpx.AsyncClient(
            base_url=settings.acquiring_bank_url,
            timeout=httpx.Timeout(
                connect=settings.bank_connect_timeout_seconds,
                read=settings.bank_read_timeout_seconds,
                write=settings.bank_read_timeout_seconds,
                pool=settings.bank_connect_timeout_seconds,
            ),
        ) as client:
            app.state.acquiring_bank = AcquiringBankClient(
                client=client,
                max_attempts=settings.bank_max_attempts,
                backoff_seconds=settings.bank_retry_backoff_seconds,
            )
            yield
            # Perform any cleanup tasks here (e.g., closing database connection)

    app = FastAPI(
        title="Payment Gateway API",
        version="1.0.0",
        description="A simple payment gateway API built with FastAPI that processes card payments through an acquiring bank.",
    )

    return app


app = create_app()
