"""FastAPI application factory."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app import __version__
from app.api.v1.internal import router as internal_v1
from app.api.v1.operator import router as operator_v1
from app.api.v1.public import router as public_v1
from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.db.session import get_engine

log = logging.getLogger("lead.http")
_DOC_PATHS = ("/api/docs", "/api/openapi.json")
_REQUEST_ID_OK = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")


def _request_id(header: str | None) -> str:
    if header and len(header) <= 64 and set(header) <= _REQUEST_ID_OK:
        return header
    return uuid.uuid4().hex


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        title="AI Lead Qualification & CRM Automation",
        version=__version__,
        description=(
            "Lead intake (form and signed webhooks), duplicate-safe processing, AI-assisted "
            "qualification with deterministic rules, CRM sync and human-approved follow-ups. "
            "Internal endpoints are called by n8n with a service token."
        ),
        docs_url="/api/docs" if settings.enable_docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.enable_docs else None,
    )
    install_error_handlers(app)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )

    @app.middleware("http")
    async def _request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = _request_id(request.headers.get("x-request-id"))
        request.state.request_id = request_id
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > settings.max_body_bytes:
            return JSONResponse(
                {
                    "error": {
                        "code": "payload_too_large",
                        "message": "Request body too large",
                        "request_id": request_id,
                    }
                },
                status_code=413,
            )
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if not request.url.path.startswith(_DOC_PATHS):
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; frame-ancestors 'none'"
            )
            response.headers["Cache-Control"] = "no-store"
        log.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": int((time.perf_counter() - started) * 1000),
            },
        )
        return response

    @app.get("/api/health", tags=["ops"])
    def health() -> dict[str, str]:
        """Liveness: the process is up."""
        return {"status": "ok", "version": __version__}

    @app.get("/api/ready", tags=["ops"])
    def ready() -> JSONResponse:
        """Readiness: the database answers and the schema is migrated."""
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1 FROM leads LIMIT 1"))
        except Exception:
            return JSONResponse({"status": "unavailable", "database": "error"}, status_code=503)
        return JSONResponse({"status": "ready", "database": "ok"})

    app.include_router(public_v1, prefix="/api/v1")
    app.include_router(operator_v1, prefix="/api/v1")
    app.include_router(internal_v1, prefix="/internal/v1", tags=["internal (n8n)"])
    return app


app = create_app()
