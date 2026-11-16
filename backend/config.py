"""AIVY backend configuration and the one place that makes ``strategy/codes`` importable.

Importing this module first is enough for every other backend module to use
``import battery`` / ``import forecast`` without copying any strategy logic.
"""

import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
STRATEGY_DIR = PROJECT_ROOT / "strategy"
STRATEGY_CODES = STRATEGY_DIR / "codes"
STRATEGY_DATA = STRATEGY_DIR / "data"

# strategy.py, battery.py and forecast.py are plain modules in strategy/codes,
# so they just need to be importable as-is.
if str(STRATEGY_CODES) not in sys.path:
    sys.path.append(str(STRATEGY_CODES))

TIMEZONE = "Australia/Adelaide"
SCHEMA_VERSION = "0.1"

STATIONS = ("SBX-001", "SBX-002", "SBX-003")
SLOTS_PER_DAY = 288

# A reading is "at the deadband" when |watts| <= 50 -> idle / neutral.
DEADBAND_W = 50
FRESH_OK_SECONDS = 600       # <= 10 min old: system "online", data "current"
FRESH_STALE_SECONDS = 3600   # <= 1 h old: "delayed"; older: "stale"

# The fixed complete day replayed for "today". 2025-08-15 (the contract's
# example) is not in strategy/data, so this points at a day that exists for
# all three homes with no missing readings (replayed_from 2025-09-16);
# data_adapter falls back to the nearest complete day anyway.
REPLAY_DATE = os.getenv("AIVY_REPLAY_DATE", "2026-09-16")
STATIONS_JSON = STRATEGY_DATA / "stations.json"   # optional (produced by fetch_data.py)

# Warm the (slow, ~25 s/station) forecast cache in the background on startup.
PRELOAD_FORECASTS = os.getenv("AIVY_PRELOAD", "1") != "0"

# --- P2 assistant: Gemini over the OpenAI-compatible endpoint (nl_assistant.ipynb) ---
# The notebook's key is the documented fallback; AIVY_GEMINI_API_KEY overrides it.
# SECRET: this value is only ever sent as an Authorization header from
# services/assistant_service.py. It is never logged, never put in a prompt and never
# copied into an API response - do not import it anywhere else.
GEMINI_API_KEY = os.getenv("AIVY_GEMINI_API_KEY", "AQ.Ab8RN6I_quQGhlt0ZApBdolyA_XgiJJB-jjUDno2uLieY6-0rw")
GEMINI_URL = os.getenv(
    "AIVY_GEMINI_URL", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions")
GEMINI_MODELS = tuple(name.strip() for name in os.getenv(
    "AIVY_GEMINI_MODELS",
    "gemini-flash-lite-latest,gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-flash-latest",
).split(",") if name.strip())
GEMINI_TIMEOUT_SECONDS = float(os.getenv("AIVY_GEMINI_TIMEOUT", "30"))

MAX_CHAT_MESSAGE_CHARS = 1000     # a home owner's question, not an essay
CHAT_HISTORY_TURNS = 10           # last 10 turns per conversation, held in memory
