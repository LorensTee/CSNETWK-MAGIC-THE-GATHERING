from __future__ import annotations

import sys
from typing import Any

# log outgoing client PDU
def log_pdu_sent(pdu: dict[str, Any]) -> None:
    ptype = pdu.get("type", "?")
    seq = pdu.get("seq_num", "?")
    print(f"[C→S] {ptype} seq={seq}", file=sys.stderr)

#log an incoming server PDU
def log_pdu_received(pdu: dict[str, Any]) -> None:
    ptype = pdu.get("type", "?")
    seq = pdu.get("seq_num", "?")
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

#log client state change
def log_state_transition(from_state: str, to_state: str) -> None:
    print(f"[CLIENT] {from_state} → {to_state}", file=sys.stderr)

# log successful connection to server
def log_connection(host: str, port: int) -> None:
    print(f"[CONN] Connected to {host}:{port}", file=sys.stderr)

# log a client event or status message
def log_event(msg: str) -> None:
    print(f"[CLIENT] {msg}", file=sys.stderr)
