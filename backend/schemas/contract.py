"""Pydantic models for the AIVY API contract (schema_version 0.1).

Timestamps are plain ISO 8601 strings so the Adelaide offset (+09:30 / +10:30)
is emitted exactly as built; numbers are floats or ``None`` (never 0 for a
missing reading).
"""

from typing import Any, Generic, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from config import SCHEMA_VERSION

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------
class SourceInfo(BaseModel):
    mode: str                                  # replay | demo | static | simulation
    dataset: Optional[str] = None
    replay_date: Optional[str] = None          # the fixed day replayed for "today"
    real_date: Optional[str] = None            # the recording behind it (prices/weather)
    mapped_from: Optional[str] = None          # for modulo-mapped archive days


class Meta(BaseModel):
    station_id: Optional[str] = None
    timezone: str
    generated_at: str
    data_timestamp: Optional[str] = None
    freshness_seconds: Optional[int] = None
    data_status: str                           # current | delayed | stale | demo | static
    source: SourceInfo
    schema_version: str = SCHEMA_VERSION


class Warning(BaseModel):
    code: str
    message: str
    field: Optional[str] = None
    count: Optional[int] = None


class Envelope(BaseModel, Generic[T]):
    data: T
    meta: Meta
    warnings: list[Warning] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# /api/stations
# ---------------------------------------------------------------------------
class Capabilities(BaseModel):
    battery: bool = True
    weather: bool = True
    simulation: bool = True
    device_control: bool = False


class BatterySpec(BaseModel):
    capacity_kwh: float
    max_power_w: float
    soc_min: float
    soc_max: float
    efficiency: float


class Station(BaseModel):
    id: str
    name: str
    spec: BatterySpec
    capabilities: Capabilities
    profile: Optional[dict[str, Any]] = None


# ---------------------------------------------------------------------------
# /api/stations/{id}/overview
# ---------------------------------------------------------------------------
class Telemetry(BaseModel):
    pv_w: Optional[float]
    load_w: Optional[float]
    battery_w: Optional[float]
    grid_w: Optional[float]
    soc_pct: Optional[float]


class Display(BaseModel):
    solar_power_kw: Optional[float]
    home_usage_kw: Optional[float]
    battery_power_kw: Optional[float]          # absolute value
    grid_power_kw: Optional[float]             # absolute value


class States(BaseModel):
    battery: str                               # charging | discharging | idle | unknown
    grid: str                                  # exporting | importing | neutral | unknown
    system: str                                # online | delayed


class Flow(BaseModel):
    mode: str = "direction_only"
    active_directions: list[str]


class OverviewData(BaseModel):
    timestamp: str
    telemetry: Telemetry
    display: Display
    states: States
    flow: Flow


# ---------------------------------------------------------------------------
# /api/stations/{id}/energy-history
# ---------------------------------------------------------------------------
class EnergyPoint(BaseModel):
    timestamp: str
    pv_w: Optional[float]
    load_w: Optional[float]
    battery_w: Optional[float]
    grid_w: Optional[float]
    soc_pct: Optional[float]
    quality: str                               # good | missing


class MissingInterval(BaseModel):
    slot: int
    timestamp: str


class EnergyHistoryData(BaseModel):
    date: str
    expected_points: int
    received_points: int
    completeness_pct: float
    missing_intervals: list[MissingInterval]
    is_partial: bool
    points: list[EnergyPoint]


# ---------------------------------------------------------------------------
# /api/stations/{id}/market
# ---------------------------------------------------------------------------
class PriceQuote(BaseModel):
    value: Optional[float]                     # AUD/kWh (null when the price is missing)
    price_type: str = "wholesale_spot"
    unit: str = "AUD_per_kWh"
    currency: str = "AUD"
    valid_from: str
    valid_to: str


class Prices(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    import_: PriceQuote = Field(alias="import")
    feed_in: PriceQuote


class WeatherData(BaseModel):
    temperature_c: Optional[float]
    cloud_cover_pct: Optional[float]
    ghi_w_m2: Optional[float]
    condition: str                             # night | clear | partly_cloudy | cloudy | overcast | unknown
    observed_at: str


class PriceHistoryPoint(BaseModel):
    timestamp: str
    price_aud_per_kwh: Optional[float]


class MarketData(BaseModel):
    slot: int
    price: Prices
    history: list[PriceHistoryPoint] = Field(default_factory=list)  # today's finished slots
    weather: WeatherData
    expected_peak_price_time: Optional[str] = None   # highest forecast price still ahead today


# ---------------------------------------------------------------------------
# /api/stations/{id}/battery
# ---------------------------------------------------------------------------
class BatteryDetailData(BaseModel):
    station_id: str
    timestamp: str
    soc_pct: Optional[float]
    state: str                                 # charging | discharging | idle | unknown
    power_kw: Optional[float]                  # absolute value
    signed_power_kw: Optional[float]           # > 0 charging, < 0 discharging
    capacity_kwh: float
    max_charge_power_kw: float
    max_discharge_power_kw: float
    reserve_pct: float
    estimated_time_to_full_min: Optional[float]  # null unless charging
    estimate_quality: str                      # high | unavailable


# ---------------------------------------------------------------------------
# /api/stations/{id}/financial-summary
# ---------------------------------------------------------------------------
class CostBreakdown(BaseModel):
    import_kwh: float
    export_kwh: float
    import_cost_aud: float
    export_earnings_aud: float
    net_cost_aud: float                        # import_cost - export_earnings


class BaselineInfo(BaseModel):
    status: str                                # available | failed
    method: str                                # self_consumption_v1
    method_version: str
    net_cost_aud: Optional[float]
    message: Optional[str] = None


class ActualInfo(BaseModel):
    strategy: str
    optimisation_goal: str
    battery_reserve_pct: float
    net_cost_aud: Optional[float]


class Coverage(BaseModel):
    expected_intervals_to_now: int
    valid_energy_intervals: int
    valid_price_intervals: int
    coverage_pct: float


class SummaryPeriod(BaseModel):
    date: str
    start_at: str
    end_at: Optional[str] = None
    intervals: int
    is_partial: bool


class FinancialSummaryData(BaseModel):
    date: str
    currency: str = "AUD"
    actual_net_cost_aud: Optional[float]        # null until a value can be computed
    baseline_net_cost_aud: Optional[float]
    today_savings_aud: Optional[float]         # baseline_net - actual_net
    savings_pct: Optional[float]
    baseline: BaselineInfo
    actual: ActualInfo
    baseline_costs: Optional[CostBreakdown] = None
    actual_costs: Optional[CostBreakdown] = None
    coverage: Coverage
    period: SummaryPeriod


# ---------------------------------------------------------------------------
# /api/stations/{id}/recommendation
# ---------------------------------------------------------------------------
class RecommendationWindow(BaseModel):
    start_at: str
    end_at: str


class RecommendationData(BaseModel):
    recommendation_id: str
    station_id: str
    action: str                                # charge | discharge | export | hold
    text: str
    reason: str
    reserve_pct: float
    window: RecommendationWindow
    estimated_savings_aud: Optional[float]
    status: str = "draft"
    valid_from: str
    valid_to: str
    input_freshness: Optional[int] = None
    model_version: str
    price_now_c_per_kwh: Optional[float] = None
    soc_pct: Optional[float] = None
    planned_battery_w: Optional[float] = None
    planned_grid_w: Optional[float] = None

# ---------------------------------------------------------------------------
# /api/stations/{id}/simulate (simulation only)
# ---------------------------------------------------------------------------
class SimulatedStrategySpec(BaseModel):
    type: str
    optimisation_goal: str
    battery_reserve_pct: float                 # no Field(ge=) so bad ranges raise 400, not 422
    start_at: str
    end_at: str


class SimulationRequest(BaseModel):
    mode: str                                  # must be "simulation"
    strategy: SimulatedStrategySpec
    recommendation_id: Optional[str] = None


class SimulationData(BaseModel):
    strategy_id: str
    station_id: str
    mode: str = "simulation"
    status: str = "applied_in_simulation"
    device_control_attempted: bool = False
    strategy_type: str
    optimisation_goal: str
    battery_reserve_pct: float
    valid_from: str
    valid_to: str
    date: str
    replay_date: Optional[str] = None
    baseline_net_cost_aud: Optional[float] = None
    simulated_net_cost_aud: Optional[float] = None
    estimated_benefit_aud: Optional[float] = None
    recommendation_id: Optional[str] = None
    message: str
    created_at: str


# ---------------------------------------------------------------------------
# /api/stations/{id}/strategy (GET / PUT)
# ---------------------------------------------------------------------------
class StrategySettings(BaseModel):
    station_id: str
    current_mode: str
    battery_reserve_pct: float
    optimisation_goal: str
    strategy_status: str                       # active (in the simulator)
    updated_at: str
    updated_by: str


class StrategyData(BaseModel):
    station_id: str
    settings: StrategySettings
    latest_simulation: Optional[SimulationData] = None


class StrategySettingsUpdate(BaseModel):
    current_mode: Optional[str] = None
    battery_reserve_pct: Optional[float] = None
    optimisation_goal: Optional[str] = None


# ---------------------------------------------------------------------------
# /api/assistant/chat
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    conversation_id: Optional[str] = None      # "conv_xxxxxxxx" generated when absent
    station_id: str
    message: str                               # 1..1000 characters (400, not 422)
    client_context: Optional[dict[str, Any]] = None   # timezone / locale hints only


class EvidenceItem(BaseModel):
    label: str
    value: Optional[float] = None              # null when the metric is unavailable
    unit: Optional[str] = None
    timestamp: Optional[str] = None
    price_type: Optional[str] = None           # wholesale_spot for price evidence


class SuggestedStrategy(BaseModel):
    strategy_id: str                           # prop_strat_xxxxxxxx
    type: str                                  # charge | discharge | export | hold
    mode_name: str                             # save_money | balanced | save_energy
    start_at: str
    end_at: str
    battery_reserve_pct: float
    optimisation_goal: str
    estimated_benefit_aud: Optional[float] = None   # null when the baseline failed
    reason: str


class ChatResponseData(BaseModel):
    conversation_id: str
    message_id: str                            # msg_xxxxxxxx
    role: str = "assistant"
    content: str
    evidence: list[EvidenceItem] = Field(default_factory=list)
    suggested_strategy: Optional[SuggestedStrategy] = None


# ---------------------------------------------------------------------------
# /api/stations/{station_id}/health
# ---------------------------------------------------------------------------
class FaultTrend(BaseModel):
    detected: bool
    change_pct: Optional[float] = None
    correlation: Optional[float] = None
    days_used: Optional[int] = None
    reason: Optional[str] = None               # set when the check had too little data


class StationHealthData(BaseModel):
    date: str
    status: str                                # healthy | sub_healthy | broken
    fault_types: list[str] = Field(default_factory=list)   # sudden_drop_to_zero | midday_flattening | ...
    explanation: str                           # plain English, from fault_detection.py
    pct_flagged: float
    soc_ceiling_pct: float                     # the station's own "battery full" level
    charge_limit_w: float                      # the station's own maximum charging rate
    energy_balance_violation_pct: float
    suspected_sign_issue: list[str] = Field(default_factory=list)   # battery | load
    trends: dict[str, FaultTrend] = Field(default_factory=dict)     # gradual_pv_decline | battery_capacity_shrink
    fault_injected: bool = False               # true while fault_config.json zeroes PV
    fault_window: Optional[str] = None         # "11:00-13:00" when injected
    days_analysed: int
    rows_dropped: int = 0


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class ErrorBody(BaseModel):
    code: str                                  # STATION_NOT_FOUND | INVALID_DATE | VALIDATION_ERROR | ...
    message: str
    field: Optional[str] = None
    retryable: bool = False
    request_id: str


class ErrorMeta(BaseModel):
    generated_at: str
    schema_version: str = SCHEMA_VERSION


class ErrorEnvelope(BaseModel):
    error: ErrorBody
    meta: ErrorMeta
