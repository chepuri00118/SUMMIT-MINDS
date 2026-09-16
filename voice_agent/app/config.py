"""Configuration loading: environment variables plus the two YAML playbooks."""
from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"


def _require(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(
            f"Missing required environment variable {name}. Copy .env.example to .env and fill it in."
        )
    return value


class Settings:
    """Runtime settings. Secrets come from the environment, never from YAML."""

    # --- telephony -------------------------------------------------------
    twilio_account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    twilio_auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    twilio_from_number = os.environ.get("TWILIO_FROM_NUMBER", "")

    # --- speech ----------------------------------------------------------
    deepgram_api_key = os.environ.get("DEEPGRAM_API_KEY", "")
    elevenlabs_api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    elevenlabs_voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "")
    elevenlabs_model = os.environ.get("ELEVENLABS_MODEL", "eleven_flash_v2_5")

    # --- brain -----------------------------------------------------------
    anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    model = os.environ.get("AGENT_MODEL", "claude-opus-5")

    # --- server ----------------------------------------------------------
    public_base_url = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    port = int(os.environ.get("PORT", "8080"))

    # --- behaviour -------------------------------------------------------
    # How long of a pause (ms) counts as "they finished talking".
    endpointing_ms = int(os.environ.get("ENDPOINTING_MS", "300"))
    # Hang up after this much dead air.
    max_silence_seconds = float(os.environ.get("MAX_SILENCE_SECONDS", "12"))
    # Hard cap so a stuck call can never burn minutes forever.
    max_call_seconds = float(os.environ.get("MAX_CALL_SECONDS", "300"))
    # Calls are only placed inside this local-time window for the lead.
    calling_window_local = (
        int(os.environ.get("CALL_WINDOW_START_HOUR", "9")),
        int(os.environ.get("CALL_WINDOW_END_HOUR", "17")),
    )
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"

    def require_live_credentials(self) -> None:
        """Fail loudly before a real campaign rather than mid-call."""
        for name in (
            "TWILIO_ACCOUNT_SID",
            "TWILIO_AUTH_TOKEN",
            "TWILIO_FROM_NUMBER",
            "DEEPGRAM_API_KEY",
            "ELEVENLABS_API_KEY",
            "ELEVENLABS_VOICE_ID",
            "ANTHROPIC_API_KEY",
            "PUBLIC_BASE_URL",
        ):
            _require(name)


settings = Settings()


@functools.lru_cache(maxsize=None)
def load_yaml(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def company() -> dict[str, Any]:
    return load_yaml("company.yaml")


def script() -> dict[str, Any]:
    return load_yaml("script.yaml")
