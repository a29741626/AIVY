"""P1 endpoints: battery detail, savings, MPC recommendation and the simulation sandbox.

All of P1 is simulation over recorded data - nothing here commands a real device.
"""

from fastapi import APIRouter, Query

from schemas.contract import (BatteryDetailData, Envelope, FinancialSummaryData,
                              RecommendationData, SimulationData, SimulationRequest,
                              StrategyData, StrategySettingsUpdate)
from services import data_adapter, envelope, replay_clock, strategy_engine

router = APIRouter(prefix="/api", tags=["P1 strategies"])


def _ok(snapshot, station_id: str):
    return envelope.ok(snapshot.data, source=snapshot.source, station_id=station_id,
                       data_timestamp=snapshot.data_timestamp,
                       freshness=snapshot.freshness_seconds,
                       status=snapshot.data_status, warnings=snapshot.warnings)


@router.get("/stations/{station_id}/battery", response_model=Envelope[BatteryDetailData])
def station_battery(station_id: str):
    sid = data_adapter.require_station(station_id)
    return _ok(strategy_engine.battery_detail(sid), sid)


@router.get("/stations/{station_id}/financial-summary", response_model=Envelope[FinancialSummaryData])
def financial_summary(station_id: str, date: str = Query(..., description="YYYY-MM-DD in Australia/Adelaide")):
    sid = data_adapter.require_station(station_id)
    day = replay_clock.parse_date(date)
    return _ok(strategy_engine.financial_summary(sid, day), sid)


@router.get("/stations/{station_id}/recommendation", response_model=Envelope[RecommendationData])
def station_recommendation(station_id: str):
    sid = data_adapter.require_station(station_id)
    return _ok(strategy_engine.recommendation(sid), sid)


@router.post("/stations/{station_id}/simulate", response_model=Envelope[SimulationData])
def simulate(station_id: str, request: SimulationRequest):
    sid = data_adapter.require_station(station_id)
    return _ok(strategy_engine.simulate(sid, request.model_dump()), sid)


@router.get("/stations/{station_id}/strategy", response_model=Envelope[StrategyData])
def current_strategy(station_id: str):
    sid = data_adapter.require_station(station_id)
    return _ok(strategy_engine.current_strategy(sid), sid)


@router.put("/stations/{station_id}/strategy", response_model=Envelope[StrategyData])
def update_strategy(station_id: str, request: StrategySettingsUpdate):
    sid = data_adapter.require_station(station_id)
    return _ok(strategy_engine.update_strategy_settings(sid, request.model_dump()), sid)
