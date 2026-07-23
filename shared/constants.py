"""
shared/constants.py — Protocol Constants (Module 01: Network Protocol)

All MTGNP protocol-level constants used by both the server and client.
Includes port numbers, timeouts, error codes, phase strings, and PDU type
enumerations.  Standard-library only — no external dependencies.
"""

import enum

# ── TCP Transport (RFC §5.1) ────────────────────────────────────────────────

DEFAULT_PORT: int = 4444
"""Default TCP port for MTGNP server."""

MAX_PDU_BYTES: int = 65535
"""Maximum size in bytes of a single PDU JSON payload (RFC §5.2)."""

# ── Game Rules (RFC §6–§8) ──────────────────────────────────────────────────

MAX_DECK_SIZE: int = 50
"""Maximum cards per deck (RFC §6.2)."""

MIN_DECK_SIZE: int = 1
"""Minimum cards per deck (RFC §6.2)."""

STARTING_LIFE: int = 20
"""Starting life total for each player (RFC §6.3)."""

INITIAL_HAND_SIZE: int = 7
"""Cards drawn during GAME_SETUP (RFC §6.3)."""

MAX_HAND_SIZE: int = 7
"""Maximum hand size during CLEANUP step (RFC §8.15)."""

# ── Heartbeat (RFC §4.3) ────────────────────────────────────────────────────

PING_INTERVAL_S: int = 30
"""Recommended interval between PING PDUs (seconds)."""

PONG_TIMEOUT_S: int = 10
"""Client MUST disconnect if no PONG received within this many seconds."""

# ── Priority (RFC §7.3) ─────────────────────────────────────────────────────

DEFAULT_TIME_LIMIT_MS: int = 60_000
"""Default time limit for a PRIORITY_GRANT (60 seconds)."""

# ── Error Codes (RFC §11) ───────────────────────────────────────────────────


class ErrorCode(str, enum.Enum):
    """All error codes defined by MTGNP 1.0 (RFC §11)."""

    INVALID_JSON = "INVALID_JSON"
    ILLEGAL_DECK = "ILLEGAL_DECK"
    UNKNOWN_TYPE = "UNKNOWN_TYPE"
    STALE_ACTION = "STALE_ACTION"
    NOT_YOUR_PRIORITY = "NOT_YOUR_PRIORITY"
    ILLEGAL_ACTION = "ILLEGAL_ACTION"
    ILLEGAL_TARGET = "ILLEGAL_TARGET"
    TRIGGER_ORDER_INVALID = "TRIGGER_ORDER_INVALID"
    TRIGGER_CHOICE_INVALID = "TRIGGER_CHOICE_INVALID"
    INSUFFICIENT_MANA = "INSUFFICIENT_MANA"
    WRONG_PHASE = "WRONG_PHASE"
    DUPLICATE_ID = "DUPLICATE_ID"


# ── Game Phase / Step Strings (RFC §10.2.4) ──────────────────────────────

# The 14 in-game phases and steps in turn order.
IN_GAME_PHASES: list[str] = [
    "UNTAP",
    "UPKEEP",
    "DRAW",
    "PRECOMBAT_MAIN",
    "BEGIN_COMBAT",
    "DECLARE_ATTACKERS",
    "DECLARE_BLOCKERS",
    "ASSIGN_DAMAGE_ORDER",
    "FIRST_STRIKE_DAMAGE",
    "COMBAT_DAMAGE",
    "END_OF_COMBAT",
    "POSTCOMBAT_MAIN",
    "END_STEP",
    "CLEANUP",
]

# Lifecycle states (not part of the turn-phase cycle).
LIFECYCLE_PHASES: list[str] = [
    "LOBBY",
    "GAME_SETUP",
    "MULLIGAN",
    "GAME_OVER",
]

# Convenience union of all valid phase/step strings.
ALL_PHASES: list[str] = LIFECYCLE_PHASES + IN_GAME_PHASES

# ── PDU Type Strings (RFC §10) ──────────────────────────────────────────────

# All 25 client-to-server PDU type strings.
C2S_PDU_TYPES: frozenset[str] = frozenset({
    "PLAYER_READY",
    "MULLIGAN_CHOICE",
    "PRIORITY_PASS",
    "CAST_SPELL",
    "ACTIVATE_ABILITY",
    "PLAY_LAND",
    "DECLARE_ATTACKERS",
    "DECLARE_BLOCKERS",
    "ASSIGN_DAMAGE_ORDER",
    "DISCARD",
    "TRIGGER_ORDER_RESPONSE",
    "TRIGGER_CHOICE_RESPONSE",
    "CONCEDE",
    "PING",
})

# All 11 server-to-client PDU type strings.
S2C_PDU_TYPES: frozenset[str] = frozenset({
    "GAME_STATE_UPDATE",
    "PHASE_TRANSITION",
    "PRIORITY_GRANT",
    "STACK_PUSH",
    "STACK_RESOLVE",
    "TRIGGER_ORDER",
    "TRIGGER_CHOICE",
    "COMBAT_DAMAGE_RESULT",
    "GAME_OVER",
    "ERROR",
    "PONG",
})

# Union of ALL known PDU type strings.
ALL_PDU_TYPES: frozenset[str] = frozenset(C2S_PDU_TYPES | S2C_PDU_TYPES)

# Priority-bearing C2S types (those that MUST echo a PRIORITY_GRANT seq_num).
PRIORITY_BEARING_TYPES: frozenset[str] = frozenset({
    "MULLIGAN_CHOICE",
    "PRIORITY_PASS",
    "CAST_SPELL",
    "ACTIVATE_ABILITY",
    "PLAY_LAND",
    "DECLARE_ATTACKERS",
    "DECLARE_BLOCKERS",
    "ASSIGN_DAMAGE_ORDER",
    "DISCARD",
    "TRIGGER_ORDER_RESPONSE",
    "TRIGGER_CHOICE_RESPONSE",
})
