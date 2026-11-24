from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from app.api import auth, cart, health, orders, products
from app.db import Base, make_engine, make_session_factory
from app.logging import configure_logging, request_id_var, test_id_var
from app.seed import seed_if_empty

logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    engine = make_engine()
    session_factory = make_session_factory(engine)
    Base.metadata.create_all(engine)
    seed_if_empty(session_factory)
    app.state.engine = engine
    app.state.SessionLocal = session_factory
    yield
    engine.dispose()


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="Shop", version="0.1.0", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(products.router)
    app.include_router(cart.router)
    app.include_router(orders.router)

    @app.middleware("http")
    async def request_context(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        test_id = request.headers.get("x-test-id")
        req_token = request_id_var.set(request_id)
        test_token = test_id_var.set(test_id)
        started = time.perf_counter()
        logger.info(
            "request_start %s %s",
            request.method,
            request.url.path,
            extra={"method": request.method, "path": request.url.path},
        )
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("request_failed %s %s", request.method, request.url.path)
            raise
        else:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.info(
                "request_end %s %s %s",
                request.method,
                request.url.path,
                response.status_code,
                extra={"status_code": response.status_code, "latency_ms": elapsed_ms},
            )
            response.headers["X-Request-Id"] = request_id
            if test_id:
                response.headers["X-Test-Id"] = test_id
            return response
        finally:
            request_id_var.reset(req_token)
            test_id_var.reset(test_token)

    return app
