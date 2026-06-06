# core/shared_state.py
"""
Shared mutable state for runtime settings toggled via Telegram / Dashboard.

Pydantic BaseSettings is immutable-at-runtime by design, so we keep
runtime-mutable flags here instead of on ``settings``.
"""


class SharedMutableState:
    autobuy_enabled: bool = False
    autosell_enabled: bool = False


shared_state = SharedMutableState()
