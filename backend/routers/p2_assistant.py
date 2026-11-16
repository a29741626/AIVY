"""P2 endpoint: the grounded AI assistant.

The reply is built server-side from the P0/P1 services, so the client cannot inject
telemetry; a strategy suggestion is a proposal for the simulation sandbox, never a
device command. The Gemini key never leaves services/assistant_service.py.
"""

from fastapi import APIRouter

from schemas.contract import ChatRequest, ChatResponseData, Envelope
from services import assistant_service, data_adapter, envelope

router = APIRouter(prefix="/api", tags=["P2 assistant"])


@router.post("/assistant/chat", response_model=Envelope[ChatResponseData])
def assistant_chat(request: ChatRequest):
    sid = data_adapter.require_station(request.station_id)
    snapshot = assistant_service.chat(sid, request.model_dump())
    return envelope.ok(snapshot.data, source=snapshot.source, station_id=sid,
                       data_timestamp=snapshot.data_timestamp,
                       freshness=snapshot.freshness_seconds,
                       status=snapshot.data_status, warnings=snapshot.warnings)