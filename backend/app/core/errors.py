"""Error handling and exception classes."""

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel


logger = structlog.get_logger(__name__)


class ErrorDetail(BaseModel):
    """Error response detail structure."""

    code: str
    message: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    """Standard error response structure."""

    error: ErrorDetail
    request_id: str


class AppError(Exception):
    """Base application error with status code and error code."""

    def __init__(
        self,
        message: str,
        status_code: int = 500,
        error_code: str = "internal_error",
        details: Any | None = None,
    ) -> None:
        """Initialize application error."""
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.details = details
        super().__init__(message)


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Handle AppError exceptions."""
    request_id = structlog.contextvars.get_contextvars().get("request_id", "unknown")

    logger.warning(
        "app_error",
        error_code=exc.error_code,
        status_code=exc.status_code,
        message=exc.message,
    )

    error_response = ErrorResponse(
        error=ErrorDetail(
            code=exc.error_code,
            message=exc.message,
            details=exc.details,
        ),
        request_id=request_id,
    )

    return JSONResponse(
        status_code=exc.status_code,
        content=error_response.model_dump(),
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unhandled exceptions."""
    request_id = structlog.contextvars.get_contextvars().get("request_id", "unknown")

    logger.error(
        "unhandled_exception",
        exc_info=exc,
        path=request.url.path,
    )

    error_response = ErrorResponse(
        error=ErrorDetail(
            code="internal_error",
            message="An unexpected error occurred",
            details=None,
        ),
        request_id=request_id,
    )

    return JSONResponse(
        status_code=500,
        content=error_response.model_dump(),
    )


def register_error_handlers(app: FastAPI) -> None:
    """Register error handlers with FastAPI app."""
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
