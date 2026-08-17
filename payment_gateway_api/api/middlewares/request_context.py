import logging
import time
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from payment_gateway_api.logger_config import request_id_var

REQUEST_ID_HEADER = "X-Request-Id"
logger = logging.getLogger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        token = request_id_var.set(request_id)
        started = time.perf_counter()

        try:
            resp = await call_next(request)
        finally:
            request_id_var.reset(token)

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        resp.headers[REQUEST_ID_HEADER] = request_id
        # the request id is automatically added to all logs in the config
        logger.info(
            "http.request",
            extra={
                "method": request.method,
                # Only the route template is logged, never the query string, which
                # keeps unexpected user data out of the logs.
                "path": request.url.path,
                "status_code": resp.status_code,
                "duration_ms": duration_ms,
            },
        )
        return resp
