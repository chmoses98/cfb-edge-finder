"""Minimal env-var-driven configuration loader.

Deliberately small: a dataclass populated from environment variables (see
.env.example), no config framework dependency. Expand only when a second
configuration source (e.g. per-environment YAML) is actually needed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    cfbd_api_key: str | None = None
    kalshi_api_key_id: str | None = None
    kalshi_private_key_path: str | None = None
    visual_crossing_api_key: str | None = None
    nws_user_agent_contact: str | None = None

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            cfbd_api_key=os.environ.get("CFBD_API_KEY") or None,
            kalshi_api_key_id=os.environ.get("KALSHI_API_KEY_ID") or None,
            kalshi_private_key_path=os.environ.get("KALSHI_PRIVATE_KEY_PATH") or None,
            visual_crossing_api_key=os.environ.get("VISUAL_CROSSING_API_KEY") or None,
            nws_user_agent_contact=os.environ.get("NWS_USER_AGENT_CONTACT") or None,
        )
