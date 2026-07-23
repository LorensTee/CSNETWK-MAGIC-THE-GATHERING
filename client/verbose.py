"""
client/verbose.py — Client-Side Verbose Logging (Module 03: Client App)

Provides formatting and logging functions for client-side verbose mode.
All output goes to stderr.  Callers check the local ``verbose`` flag
before calling these helpers.
"""

from __future__ import annotations

import sys
from typing import Any


def log_pdu_sent(pdu: dict[str, Any]) -> None:
    """Log an outgoing PDU to the server."""
    ptype = pdu.get("type", "?")
    seq = pdu.get("seq_num", "?")
    print(f"[C→S] {ptype} seq={seq}", file=sys.stderr)


def log_pdu_received(pdu: dict[str, Any]) -> None:
    """Log an incoming PDU from the server."""
    ptype = pdu.get("type", "?")
    seq = pdu.get("seq_num", "?")
    # Show extra context for grant / update / transition.
    extra = ""
    if ptype == "PRIORITY_GRANT":
        extra = f" player={pdu.get('player_id','?')} timeout={pdu.get('time_limit_ms','?')}ms"
    elif ptype == "GAME_STATE_UPDATE":
        state = pdu.get("state", {})
        extra = f" phase={state.get('phase','?')}"
    elif ptype == "PHASE_TRANSITION":
        extra = f" {pdu.get('from_phase','?')} → {pdu.get('to_phase','?')}"
    elif ptype == "GAME_OVER":
        extra = f" winner={pdu.get('winner_id','?')} reason={pdu.get('reason','?')}"
    elif ptype == "ERROR":
        extra = f" code={pdu.get('code','?')}"

    print(f"[S→C] {ptype} seq={seq}{extra}", file=sys.stderr)


def log_state_transition(from_state: str, to_state: str) -> None:
    """Log a client state transition."""
    print(f"[CLIENT] {from_state} → {to_state}", file=sys.stderr)


def log_connection(host: str, port: int) -> None:
    """Log a connection event."""
    print(f"[CONN] Connected to {host}:{port}", file=sys.stderr)


def log_event(msg: str) -> None:
    """Log a generic client event."""
    print(f"[CLIENT] {msg}", file=sys.stderr)
