# 01 — Network Protocol Module

> **Module scope:** TCP framing, PDU definitions, seq_num rules, error codes, heartbeat, connection I/O loops.
>
> **Paired with:** `00_architecture_master.md` (always include the master as context).

---

## Files in this module

| File | Purpose |
|------|---------|
| `shared/constants.py` | Protocol constants — port, max PDU size, phase enum, error codes, type strings |
| `shared/framing.py` | `encode_frame(payload: bytes) -> bytes` and `decode_frame(reader: StreamReader) -> bytes` |
| `shared/pdus.py` | PDU type registry: `create_*(...)` factory functions, `parse_pdu(raw: dict) -> Pdu` validator, `validate_seq_num(...)` |
| `shared/verbose.py` | `format_pdu_sent(label, pdu)` / `format_pdu_received(label, pdu)` — shared formatting |
| `server/connection.py` | `ServerConnection` class: per-client `read_loop()` / `write_loop()`, framed I/O, seq_num tracking |
| `client/connection.py` | `ClientConnection` class: connect, `read_loop()` / `write_loop()`, framed I/O, seq_num tracking |

---

## 1. TCP Transport (RFC §5.1)

```
Default port:  4444
Protocol:      TCP (IPv4 or IPv6)
Connections:   Exactly 2 (server refuses additional connections)
Reconnect:     After GAME_OVER, same TCP connections are reused — no reconnection needed
```

### Server-side connection management

1. Create a listening socket on `0.0.0.0:4444`.
2. `accept()` up to 2 connections. Track them as `player_1` and `player_2` (order of connection).
3. Additional `accept()` attempts → close the new socket immediately (no PDU sent; the spec says "MUST be refused").
4. If either connection drops (TCP RST / EOF / heartbeat timeout), the server transitions to `GAME_OVER` with reason `DISCONNECT`, retains the surviving connection, and returns to `LOBBY`.

### Client-side connection management

1. `connect()` to the server host:port.
2. On connect, the client may immediately send `PLAYER_READY`.
3. Send `PING` every 30 seconds.
4. If no `PONG` received within 10 seconds of sending `PING`, consider the server unreachable — close the socket and exit.

---

## 2. Message Framing (RFC §5.2)

Every PDU on the wire follows this binary layout:

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                    Message Length (32 bits)                   |  ← big-endian unsigned int
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                  JSON Payload (variable length)               |
|                           ...                                 |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

**Encoding** (`shared/framing.py` — `encode_frame`):

```python
import struct
import json

def encode_frame(pdu_dict: dict) -> bytes:
    """Serialize a PDU dict to wire format: 4-byte len + UTF-8 JSON."""
    payload = json.dumps(pdu_dict, ensure_ascii=False).encode('utf-8')
    if len(payload) > 65535:
        raise ValueError("PDU exceeds 65,535 bytes")
    return struct.pack('>I', len(payload)) + payload
```

**Decoding** (`shared/framing.py` — `decode_frame`):

```python
async def decode_frame(reader: asyncio.StreamReader) -> dict:
    """Read one framed PDU from a stream. Returns the parsed dict."""
    length_bytes = await reader.readexactly(4)
    length = struct.unpack('>I', length_bytes)[0]
    if length > 65535:
        raise ProtocolError("PDU exceeds 65,535 bytes")
    payload_bytes = await reader.readexactly(length)
    try:
        return json.loads(payload_bytes.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ProtocolError("INVALID_JSON") from e
```

**Rules:**
- Receiver MUST read exactly `length` bytes before attempting JSON parse.
- PDU MUST NOT exceed 65,535 bytes.
- JSON MUST be valid UTF-8.
- Field names are case-sensitive and MUST match the exact names in Section 3 below.

---

## 3. PDU Definitions (RFC §10)

### 3.1 Common fields

Every PDU is a JSON object with two REQUIRED fields:

```json
{
  "type":    "<MESSAGE_TYPE>",
  "seq_num": <integer>
}
```

### 3.2 seq_num rules

| Direction | Rule |
|-----------|------|
| **Server → Client** | Server increments its own counter with each PDU it sends. Client MAY use for ordering/duplicate detection. |
| **Client → Server (priority-bearing)** | MUST echo the seq_num from the most recent `PRIORITY_GRANT` or corresponding server request PDU. Server rejects mismatches with `ERROR` code `STALE_ACTION`. |
| **Client → Server (CONCEDE)** | MAY be sent anytime; echoes seq_num from most recently received server PDU of any type. |
| **Client → Server (PING)** | Client-maintained counter; server echoes it unchanged in `PONG`. Not validated against priority token. |
| **Client → Server (PLAYER_READY)** | Client-maintained counter starting at 1; server does not validate against priority token. |

### 3.3 Complete PDU catalog (25 types)

#### C→S PDUs (client to server)

| Type | Priority-bearing? | Key fields | RFC § |
|------|-------------------|------------|-------|
| `PLAYER_READY` | No | `player_id`, `deck_list` | 10.2.1 |
| `MULLIGAN_CHOICE` | Yes | `keep`, `cards_to_bottom` | 10.2.3 |
| `PRIORITY_PASS` | Yes | (none beyond type/seq_num) | 10.2.6 |
| `CAST_SPELL` | Yes | `card_id`, `targets`, `mana_payment` | 10.2.7 |
| `ACTIVATE_ABILITY` | Yes | `source_id`, `ability_index`, `targets`, `cost_payment` | 10.2.8 |
| `PLAY_LAND` | Yes | `card_id` | 10.2.19 |
| `DECLARE_ATTACKERS` | Yes | `attackers` (list of `{creature_id, target}`) | 10.2.15 |
| `DECLARE_BLOCKERS` | Yes | `blockers` (list of `{creature_id, blocking_id}`) | 10.2.16 |
| `ASSIGN_DAMAGE_ORDER` | Yes | `attacker_id`, `blocker_order` | 10.2.17 |
| `DISCARD` | Yes | `card_ids` | 10.2.20 |
| `TRIGGER_ORDER_RESPONSE` | Yes | `ordered_trigger_ids` | 10.2.11 |
| `TRIGGER_CHOICE_RESPONSE` | Yes | `trigger_id`, `accept`, `chosen_target` | 10.2.13 |
| `CONCEDE` | Special | `player_id` | 10.2.21 |
| `PING` | No | `timestamp` | 10.2.24 |

#### S→C PDUs (server to client)

| Type | Broadcast? | Key fields | RFC § |
|------|-----------|------------|-------|
| `GAME_STATE_UPDATE` | Per-player (personalized) | `state` object | 10.2.2 |
| `PHASE_TRANSITION` | ALL | `from_phase`, `to_phase`, `active_player`, `turn` | 10.2.4 |
| `PRIORITY_GRANT` | Single player | `player_id`, `time_limit_ms` | 10.2.5 |
| `STACK_PUSH` | ALL | `stack_item_id`, `item_type`, `source`, `targets`, `controller` | 10.2.9 |
| `STACK_RESOLVE` | ALL | `stack_item_id`, `result`, `state_changes` | 10.2.14 |
| `TRIGGER_ORDER` | Single player | `player_id`, `trigger_ids` | 10.2.10 |
| `TRIGGER_CHOICE` | Single player | `trigger_id`, `source_id`, `effect_summary`, `requires_target`, `legal_targets` | 10.2.12 |
| `COMBAT_DAMAGE_RESULT` | ALL | `damage_events`, `life_totals`, `creatures_died` | 10.2.18 |
| `GAME_OVER` | ALL | `winner_id`, `loser_id`, `reason` | 10.2.22 |
| `ERROR` | Single player | `code`, `message`, `rejected_action` | 10.2.23 |
| `PONG` | Single player | `timestamp` | 10.2.25 |

### 3.4 Error codes (RFC §11)

| Code | Meaning |
|------|---------|
| `INVALID_JSON` | Payload could not be parsed as valid UTF-8 JSON |
| `ILLEGAL_DECK` | Deck list empty, >50 cards, or contains illegal card IDs |
| `UNKNOWN_TYPE` | `type` field does not match any known PDU type |
| `STALE_ACTION` | seq_num does not match current priority token |
| `NOT_YOUR_PRIORITY` | Client submitted an action while not holding priority |
| `ILLEGAL_ACTION` | Syntactically valid but violates game rules |
| `ILLEGAL_TARGET` | One or more targets are not legal |
| `TRIGGER_ORDER_INVALID` | Response does not contain exactly the expected trigger IDs |
| `TRIGGER_CHOICE_INVALID` | Unknown trigger_id or missing required target |
| `INSUFFICIENT_MANA` | Mana payment does not satisfy the cost |
| `WRONG_PHASE` | Action not legal in current phase (e.g. sorcery outside Main Phase) |
| `DUPLICATE_ID` | player_id already claimed by the other player in this lobby |

---

## 4. Phase enum (RFC §10.2.4)

Valid phase strings in turn order:

```
UNTAP → UPKEEP → DRAW → PRECOMBAT_MAIN → BEGIN_COMBAT →
DECLARE_ATTACKERS → DECLARE_BLOCKERS → ASSIGN_DAMAGE_ORDER →
FIRST_STRIKE_DAMAGE → COMBAT_DAMAGE → END_OF_COMBAT →
POSTCOMBAT_MAIN → END_STEP → CLEANUP
```

Plus lifecycle phases: `LOBBY`, `GAME_SETUP`, `MULLIGAN`, `GAME_OVER`.

---

## 5. Implementation guidelines

### 5.1 `shared/constants.py`

Define as module-level constants or a simple `enum.StrEnum`:

```python
DEFAULT_PORT = 4444
MAX_PDU_BYTES = 65535
MAX_DECK_SIZE = 50
STARTING_LIFE = 20
INITIAL_HAND_SIZE = 7
MAX_HAND_SIZE = 7
PING_INTERVAL_S = 30
PONG_TIMEOUT_S = 10
```

Define error codes as a `dict` or `StrEnum`. Define phase strings as an ordered list.

### 5.2 `shared/pdus.py`

For each of the 25 PDU types, provide:

- A **factory function** `create_<type>(**kwargs) -> dict` that builds a valid PDU dict with all required fields. Example:

```python
def create_priority_pass(seq_num: int) -> dict:
    return {"type": "PRIORITY_PASS", "seq_num": seq_num}
```

- A **validator function** `validate_<type>(pdu: dict) -> list[str]` that returns a list of validation error messages (empty = valid). Check: required fields present, field types correct, enum values valid, seq_num is int.

- A **type registry** `PDU_TYPES: dict[str, dict]` mapping type string to metadata: direction (`C2S` | `S2C`), priority_bearing (`bool`), required_fields (`list[str]`).

### 5.3 Connection I/O loops

Both `server/connection.py` and `client/connection.py` implement the same pattern:

```python
class BaseConnection:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self.seq_num = 0           # server: outgoing counter; client: last-received echo
        self._write_lock = asyncio.Lock()

    async def send_pdu(self, pdu: dict):
        """Thread-safe framed send."""
        async with self._write_lock:
            frame = encode_frame(pdu)
            self.writer.write(frame)
            await self.writer.drain()

    async def recv_pdu(self) -> dict:
        """Read one framed PDU. Raises ProtocolError on framing/JSON failure."""
        return await decode_frame(self.reader)
```

**Server-specific** (`server/connection.py`):
- Extends `BaseConnection`.
- `seq_num` is the server's monotonically increasing outgoing counter (increment before each send).
- Maintains a `player_id` (assigned from `PLAYER_READY`).
- `read_loop()`: forever read PDUs, dispatch to the server game engine.
- `write_loop()`: drain an `asyncio.Queue` of outgoing PDUs.

**Client-specific** (`client/connection.py`):
- Extends `BaseConnection`.
- `seq_num` tracks the last received server seq_num (for echo in priority-bearing PDUs).
- Maintains its own `client_seq_num` for PING/PLAYER_READY.
- `read_loop()`: forever read PDUs, dispatch to the client state handler.
- `write_loop()`: drain an `asyncio.Queue` of outgoing PDUs.

### 5.4 Verbose mode (`shared/verbose.py`)

When verbose mode is enabled, every PDU sent or received is printed to stderr in a readable format:

```
[S→C player_1] GAME_STATE_UPDATE seq=44 | phase=PRECOMBAT_MAIN life={p1:17,p2:12}
[C→S player_2] PRIORITY_PASS seq=49
```

The `format_pdu_sent(label, pdu)` / `format_pdu_received(label, pdu)` functions return a one-line string. Callers decide whether to print (checking their local `verbose` flag).
