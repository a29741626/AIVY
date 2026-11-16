"""Build the standard response envelope (data + meta + warnings) and error bodies."""

from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

import config
from schemas.contract import SCHEMA_VERSION

ADL = ZoneInfo(config.TIMEZONE)


def now_adl() -> datetime:
    return datetime.now(ADL)


def generated_at(now: Optional[datetime] = None) -> str:
    return (now or now_adl()).isoformat()


def data_status(mode: str, freshness: Optional[int]) -> str:
    """One word the frontend can switch on."""
    if mode == "static":
        return "static"
    if mode == "demo":
        return "demo"
    if freshness is None:
        return "unknown"
    if freshness <= config.FRESH_OK_SECONDS:
        return "current"
    if freshness <= config.FRESH_STALE_SECONDS:
        return "delayed"
    return "stale"


def ok(data: Any, *, source: dict, station_id: Optional[str] = None,
       data_timestamp: Optional[str] = None, freshness: Optional[int] = None,
       status: Optional[str] = None, warnings: Optional[list[dict]] = None,
       now: Optional[datetime] = None) -> dict:
    return {
        "data": data,
        "meta": {
            "station_id": station_id,
            "timezone": config.TIMEZONE,
            "generated_at": generated_at(now),
            "data_timestamp": data_timestamp,
            "freshness_seconds": freshness,
            "data_status": status if status is not None else data_status(source.get("mode", ""), freshness),
            "source": source,
            "schema_version": SCHEMA_VERSION,
        },
        "warnings": warnings or [],
    }


def error(code: str, message: str, *, field: Optional[str] = None,
          retryable: bool = False, request_id: str) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "field": field,
            "retryable": retryable,
            "request_id": request_id,
        },
        "meta": {"generated_at": generated_at(), "schema_version": SCHEMA_VERSION},
    }
