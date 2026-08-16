"""FastAPI application factory and entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_exception_handlers
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging, get_logger

logger = get_logger("vocably")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    logger.info("Starting %s (%s)", settings.project_name, settings.environment)
    yield
    logger.info("Shutting down %s", settings.project_name)


def create_app() -> FastAPI:
    configure_logging()
    # All three, not just the two that were named here: `redoc_url` defaults to
    # "/redoc" in FastAPI, so leaving it unset published a second docs UI in
    # production that nothing in this file mentioned. `openapi_url` is the
    # load-bearing one — the spec is what a scanner consumes, and disabling it
    # while leaving `docs_url` on renders Swagger as a broken empty page rather
    # than a 404, which reads as an outage.
    docs = settings.docs_enabled
    app = FastAPI(
        title=settings.project_name,
        version="0.1.0",
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
        lifespan=lifespan,
    )

    if settings.backend_cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.backend_cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "environment": settings.environment}

    return app


app = create_app()
