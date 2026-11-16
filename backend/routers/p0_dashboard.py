"""P0 dashboard endpoints (AIVY API contract)."""

from fastapi import APIRouter, Query

from schemas.contract import EnergyHistoryData, Envelope, MarketData, OverviewData, Station
from services import data_adapter, envelope, replay_clock

router = APIRouter(prefix="/api", tags=["P0 dashboard"])


@router.get("/stations", response_model=Envelope[list[Station]])
def list_stations():
    return envelope.ok(list(data_adapter.stations()),
                       source={"mode": "static", "dataset": "battery.SPECS + stations.json"})


@router.get("/stations/{station_id}/overview", response_model=Envelope[OverviewData])
def station_overview(station_id: str):
    sid = data_adapter.require_station(station_id)
    snapshot = data_adapter.overview(sid)
    return envelope.ok(snapshot.data, source=snapshot.source, station_id=sid,
                       data_timestamp=snapshot.data_timestamp,
                       freshness=snapshot.freshness_seconds,
                       status=snapshot.data_status, warnings=snapshot.warnings)


@router.get("/stations/{station_id}/energy-history", response_model=Envelope[EnergyHistoryData])
def energy_history(station_id: str, date: str = Query(..., description="YYYY-MM-DD in Australia/Adelaide")):
    sid = data_adapter.require_station(station_id)
    day = replay_clock.parse_date(date)
    sl = data_adapter.day_slice(sid, day)
    data = {
        "date": day.isoformat(),
        "expected_points": sl.expected_points,
        "received_points": sl.received_points,
        "completeness_pct": sl.completeness_pct,
        "missing_intervals": sl.missing_intervals,
        "is_partial": sl.is_partial,
        "points": sl.points,
    }
    return envelope.ok(data, source=sl.source(), station_id=sid,
                       data_timestamp=sl.data_timestamp, freshness=sl.freshness,
                       status=sl.data_status, warnings=sl.warnings)


@router.get("/stations/{station_id}/market", response_model=Envelope[MarketData])
def station_market(station_id: str):
    sid = data_adapter.require_station(station_id)
    snapshot = data_adapter.market(sid)
    return envelope.ok(snapshot.data, source=snapshot.source, station_id=sid,
                       data_timestamp=snapshot.data_timestamp,
                       freshness=snapshot.freshness_seconds,
                       status=snapshot.data_status, warnings=snapshot.warnings)
