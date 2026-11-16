"""P2 fault diagnostics endpoint: real recorded data through the real detector.

The fault itself is simulated (``backend/fault_config.json``), the analysis is not:
the response is ``fault_detection.py`` run on the station's recorded days, and every
injected slot is announced in ``warnings`` (``FAULT_INJECTED``) so no client can show
a simulated drop as a genuine one.
"""

from fastapi import APIRouter

from schemas.contract import Envelope, StationHealthData
from services import data_adapter, envelope, fault_service

router = APIRouter(prefix="/api", tags=["P2 fault diagnostics"])


@router.get("/stations/{station_id}/health", response_model=Envelope[StationHealthData])
def station_health(station_id: str):
    sid = data_adapter.require_station(station_id)
    snapshot = fault_service.health(sid)
    return envelope.ok(snapshot.data, source=snapshot.source, station_id=sid,
                       data_timestamp=snapshot.data_timestamp,
                       freshness=snapshot.freshness_seconds,
                       status=snapshot.data_status, warnings=snapshot.warnings)
