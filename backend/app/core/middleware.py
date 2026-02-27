"""Request middleware for request ID and context binding."""

import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Middleware that generates a unique request ID and binds it to logging context."""

    async def dispatch(self, request: Request, call_next) -> Response:
        """Generate request_id, bind to context, and add to response headers."""
        request_id = str(uuid.uuid4())

        # Bind request_id to structlog context
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        # Process request
        response = await call_next(request)

        # Add request_id to response headers
        response.headers["X-Request-ID"] = request_id

        return response
