"""AIVY FastAPI entrypoint: CORS, lifespan forecast warm-up, error envelope handlers.

Run from this folder:  uvicorn main:app --reload --port 8000
"""

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

import config  # noqa: F401  (importing it also puts strategy/codes on sys.path)

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from routers import p0_dashboard, p1_strategies, p2_assistant, p2_fault
from services import envelope
from services.errors import ApiError

log = logging.getLogger("uvicorn.error")   # so warm-up progress shows in the uvicorn console


def _log_warm_failure(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        log.warning("forecast warm-up failed: %s", error)


async def _warm_strategies(station_id: str) -> None:
    """Pre-run today's baseline + active mode so the first P1 request is not slow."""
    from services import strategy_engine

    try:
        names = await asyncio.to_thread(strategy_engine.warm_today, station_id)
        log.info("strategy cache warm for %s (%s)", station_id, ", ".join(names))
    except Exception as error:          # a slow/failed warm-up must never stop boot
        log.warning("strategy warm-up failed for %s: %s", station_id, error)


async def _warm_forecasts() -> None:
    import forecast  # imported here so a strategy import problem cannot stop boot

    # ponytail: park the stations one at a time (the work is CPU-bound, so gathering them only
    # made the first one - SBX-001, the page's default - finish last under GIL contention).
    for sid in config.STATIONS:
        try:
            days = await asyncio.to_thread(forecast.prepare, sid)
        except Exception as error:      # a slow/failed warm-up must never stop boot
            log.warning("forecast.prepare(%s) failed: %s", sid, error)
            continue
        log.info("forecast cache warm for %s (%d days)", sid, len(days))
        await _warm_strategies(sid)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = None
    if config.PRELOAD_FORECASTS:
        log.info("warming forecast cache for %s in the background", ", ".join(config.STATIONS))
        task = asyncio.create_task(_warm_forecasts())
        task.add_done_callback(_log_warm_failure)
    app.state.forecast_warmup = task
    yield


app = FastAPI(
    title="AIVY API",
    version=config.SCHEMA_VERSION,
    description="Phase P0/P1/P2: real-data dashboard, virtual Adelaide replay clock, "
                "savings and MPC recommendations, a simulation-only strategy sandbox, "
                "and a grounded AI assistant.",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(p0_dashboard.router)
app.include_router(p1_strategies.router)
app.include_router(p2_assistant.router)
app.include_router(p2_fault.router)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request.state.request_id = uuid.uuid4().hex
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", uuid.uuid4().hex)


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError):
    return JSONResponse(status_code=exc.status_code, content=envelope.error(
        exc.code, exc.message, field=exc.field, retryable=exc.retryable,
        request_id=_request_id(request)))


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    field = None
    if errors:
        location = [part for part in errors[0].get("loc", ()) if part not in ("query", "path", "body")]
        field = ".".join(str(part) for part in location) or None
    message = errors[0].get("msg", "Invalid request") if errors else "Invalid request"
    return JSONResponse(status_code=422, content=envelope.error(
        "VALIDATION_ERROR", message, field=field, request_id=_request_id(request)))


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(status_code=exc.status_code, content=envelope.error(
        "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR", str(exc.detail),
        request_id=_request_id(request)))


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception):
    log.exception("unhandled error: %s", exc)
    return JSONResponse(status_code=500, content=envelope.error(
        "INTERNAL_ERROR", "Unexpected server error", request_id=_request_id(request)))
