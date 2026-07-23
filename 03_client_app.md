# 03 — Client App Module

> **Module scope:** Client state machine, terminal-based rendering, player input capture, heartbeat (PING/PONG).
>
> **Paired with:** `00_architecture_master.md` (always include the master as context).
>
> **Dependencies:** `shared/framing.py`, `shared/pdus.py`, `shared/constants.py`, `shared/verbose.py` (from Module 1). The client also imports `client/connection.py` (from Module 1) for the server read/write loops.

---

## Files in this module

| File | Purpose |
|------|---------|
| `client/main.py` | Entry point: `argparse`, connect, start client event loop |
| `client/config.py` | `ClientConfig` dataclass: server host, port, player_id, verbose flag |
| `client/client.py` | `GameClient` class: top-level state machine, PDU routing |
| `client/dispatcher.py` | Route incoming server PDUs → renderer updates or input prompts |
| `client/renderer.py` | Terminal-based Visible State display |
| `client/input_handler.py` | Async keyboard input → PDU creation and queuing |
| `client/heartbeat.py` | 30 s PING timer + 10 s PONG timeout watcher |
| `client/verbose.py` | Client-side verbose PDU logging |

---

## 1. Entry point (`client/main.py`)

```python
import argparse
import asyncio
from client.config import ClientConfig
from client.client import GameClient

def main():
    parser = argparse.ArgumentParser(description='MTGNP Player Client')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=4444)
    parser.add_argument('--player-id', required=True, help='Unique player identifier')
    parser.add_argument('--deck-file', default=None,
                        help='Path to a text file listing card IDs (one per line). '
                             'If not provided, the client prompts interactively.')
    parser.add_argument('--verbose', action='store_true', default=False)
    args = parser.parse_args()

    config = ClientConfig(
        host=args.host,
        port=args.port,
        player_id=args.player_id,
        deck_file=args.deck_file,
        verbose=args.verbose,
    )
    asyncio.run(GameClient(config).run())

if __name__ == '__main__':
    main()
```

---

## 2. Client State Machine (`client/client.py`)

### 2.1 States (client-side perspective)

```
DISCONNECTED → CONNECTING → LOBBY → GAME_SETUP → MULLIGAN → IN_GAME → GAME_OVER
                                ↑                                      │
                                └──────────────────────────────────────┘
```

The client mirrors the server's lifecycle but does NOT make autonomous transitions — it changes state only in response to server PDUs.

### 2.2 `GameClient.run()`

```python
class GameClient:
    def __init__(self, config: ClientConfig):
        self.config = config
        self.connection: ClientConnection | None = None
        self.state = "DISCONNECTED"
        self.visible_state: dict = {}       # Last received GAME_STATE_UPDATE state dict
        self.outgoing_queue: asyncio.Queue = asyncio.Queue()

    async def run(self):
        # 1. Connect
        self.state = "CONNECTING"
        self.connection = await ClientConnection.connect(self.config.host, self.config.port)

        # 2. Start concurrent tasks
        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._read_loop())
            tg.create_task(self._write_loop())
            tg.create_task(self._render_loop())
            tg.create_task(self._heartbeat_loop())
            tg.create_task(self._input_loop())

    async def _read_loop(self):
        """Read PDUs from server, dispatch to handlers."""
        while True:
            try:
                pdu = await self.connection.recv_pdu()
            except (ConnectionError, ProtocolError):
                print("Disconnected from server.")
                self.state = "DISCONNECTED"
                return
            if self.config.verbose:
                print(f"[S→C] {format_pdu_received(pdu)}", file=sys.stderr)
            await self.dispatcher.dispatch(pdu)

    async def _write_loop(self):
        """Drain outgoing queue and send PDUs to server."""
        while True:
            pdu = await self.outgoing_queue.get()
            if self.config.verbose:
                print(f"[C→S] {format_pdu_sent(pdu)}", file=sys.stderr)
            await self.connection.send_pdu(pdu)

    async def _render_loop(self):
        """Periodically redraw the screen (debounced on state change)."""
        while True:
            await self._render_event.wait()
            self._render_event.clear()
            self.renderer.draw(self.state, self.visible_state)

    async def _heartbeat_loop(self):
        """Send PING every 30s, check PONG timeout."""
        # Implemented in client/heartbeat.py
        await heartbeat.run(self)

    async def _input_loop(self):
        """Capture player input, create PDUs, enqueue for sending."""
        # Implemented in client/input_handler.py
        await input_handler.run(self)
```

### 2.3 PDU dispatch (`client/dispatcher.py`)

```python
class ClientDispatcher:
    def __init__(self, client: GameClient):
        self.client = client

    async def dispatch(self, pdu: dict):
        pdu_type = pdu["type"]
        handler = {
            "GAME_STATE_UPDATE":    self._handle_game_state_update,
            "PHASE_TRANSITION":     self._handle_phase_transition,
            "PRIORITY_GRANT":       self._handle_priority_grant,
            "STACK_PUSH":           self._handle_stack_push,
            "STACK_RESOLVE":        self._handle_stack_resolve,
            "TRIGGER_ORDER":        self._handle_trigger_order,
            "TRIGGER_CHOICE":       self._handle_trigger_choice,
            "COMBAT_DAMAGE_RESULT": self._handle_combat_damage_result,
            "GAME_OVER":            self._handle_game_over,
            "ERROR":                self._handle_error,
            "PONG":                 self._handle_pong,
        }.get(pdu_type)
        if handler:
            await handler(pdu)
```

Each handler updates `self.client.visible_state` and triggers a re-render.

### 2.4 State-specific behavior

| Client state | Allowed outbound PDUs | On receiving `GAME_STATE_UPDATE` |
|---|---|---|
| LOBBY | `PLAYER_READY`, `PING` | Show lobby info, prompt for deck if not sent |
| GAME_SETUP | `PING` only (server does everything automatically) | Update `visible_state`, transition to `MULLIGAN` when phase changes |
| MULLIGAN | `MULLIGAN_CHOICE`, `PING` | Show opening hand, prompt keep/mulligan |
| IN_GAME | All priority-bearing PDUs (when holding priority), `CONCEDE`, `PING` | Update visible state, show current phase, prompt for action when holding priority |
| GAME_OVER | `PLAYER_READY`, `PING` | Show game result, prompt for new deck |

---

## 3. Renderer (`client/renderer.py`)

### 3.1 Design philosophy

The renderer is a **terminal-based display** that redraws the screen on each state change. It MUST **never compute game outcomes** — it only formats the `visible_state` dict received from the server.

### 3.2 Layout (80-column terminal)

```
╔══════════════════════════════════════════════════════════════════════════════╗
║  MTGNP 1.0 — You are: player_1                          Turn: 3  Phase: PRECOMBAT_MAIN
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  ┌─── Opponent (player_2) ───────────────────────────────────────────────┐  ║
║  │  Life: ████████████████░░░░  16         Hand: 5 cards                  │  ║
║  │  Library: 8 cards                                                      │  ║
║  │  Battlefield:                                                          │  ║
║  │    [T] swamp_001  [ ] island_001  [ ] gray_merchant_001 (2/4, no dmg) │  ║
║  │  Graveyard: [shock_003, doom_blade_002]                                │  ║
║  └────────────────────────────────────────────────────────────────────────┘  ║
║                                                                              ║
║  ┌─── Your Hand ─────────────────────────────────────────────────────────┐  ║
║  │  [1] lightning_bolt_002  [2] mountain_004  [3] goblin_guide_001       │  ║
║  │  [4] shock_001           [5] incinerate_001                            │  ║
║  └────────────────────────────────────────────────────────────────────────┘  ║
║                                                                              ║
║  ┌─── Your Board (player_1) ──────────────────────────────────────────────┐  ║
║  │  Life: ████████████████████░░  18         Library: 5 cards              │  ║
║  │  Battlefield:                                                          │  ║
║  │    [T] mountain_001  [T] mountain_002  [ ] mountain_003                │  ║
║  │    [ ] goblin_guide_001 (2/2, no dmg, HASTE)                           │  ║
║  │  Graveyard: [lightning_bolt_001]                                       │  ║
║  └────────────────────────────────────────────────────────────────────────┘  ║
║                                                                              ║
║  ┌─── Stack ──────────────────────────────────────────────────────────────┐  ║
║  │  (empty)                                                                │  ║
║  └────────────────────────────────────────────────────────────────────────┘  ║
║                                                                              ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  [YOU HAVE PRIORITY]  Time remaining: 58s                                   ║
║  Commands: cast <N> [target], land <N>, pass, concede, help                ║
║  > _                                                                        ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

### 3.3 Rendering rules

1. **Hand:** Show each card with its copy number in brackets. The player's own hand is shown as a list of card names (from the `hand` array in `visible_state`). The opponent's hand is shown only as a count (from `hand_counts`).

2. **Battlefield:** Show each permanent:
   - `[T]` if tapped, `[ ]` if untapped
   - Card name (from `card_id` — strip the `_NNN` suffix, or keep the full ID for exact identification)
   - Creatures additionally show: `(power/toughness, N dmg)` plus keyword abilities in abbreviated form: `H` (haste), `F` (flying), `FS` (first strike), `T` (trample), `D` (defender), `V` (vigilance), `X` (hexproof)

3. **Stack:** List stack items from bottom to top. Each line: `[stack_item_id] card_name (controller) → targets`. Use `← TOP` to mark the top of the stack.

4. **Life:** Visual bar: 20 filled blocks minus life lost. `Life: ████████████████░░░░  16`

5. **Status line:** Show whether the player holds priority and remaining time (if known).

### 3.4 Phase-specific prompts

| Phase | Prompt |
|-------|--------|
| LOBBY | "Waiting for opponent..." or "Enter deck list (card IDs separated by commas):" |
| MULLIGAN | "Keep this hand? (keep/mulligan)" then if keeping after mulligans: "Select N cards to bottom:" |
| PRECOMBAT_MAIN / POSTCOMBAT_MAIN | "Commands: cast <N>, land <N>, activate <source> <ability>, pass, concede" |
| DECLARE_ATTACKERS | "Declare attackers: attack <N> [target], or 'no attacks' / pass" |
| DECLARE_BLOCKERS | "Declare blockers: block <N> <attacker_N>, or 'no blocks' / pass" |
| Other phases | "Commands: cast <N>, pass, concede" (only instants/abilities at instant speed) |
| GAME_OVER | "Game over! Winner: X. Reason: Y. Send new deck to play again." |

---

## 4. Input Handler (`client/input_handler.py`)

### 4.1 Async input

The input handler runs an `asyncio` task that reads from `sys.stdin`. Because `sys.stdin.readline()` is blocking, use `asyncio.get_event_loop().run_in_executor(None, sys.stdin.readline)` to avoid blocking the event loop.

```python
async def read_line() -> str:
    loop = asyncio.get_event_loop()
    return (await loop.run_in_executor(None, sys.stdin.readline)).strip()
```

### 4.2 Command parser

```python
COMMANDS = {
    "cast":    _cmd_cast,
    "land":    _cmd_play_land,
    "pass":    _cmd_priority_pass,
    "attack":  _cmd_declare_attackers,
    "block":   _cmd_declare_blockers,
    "keep":    _cmd_mulligan_keep,
    "mulligan":_cmd_mulligan_redraw,
    "bottom":  _cmd_bottom_cards,
    "concede": _cmd_concede,
    "activate":_cmd_activate_ability,
    "order":   _cmd_damage_order,
    "discard": _cmd_discard,
    "help":    _cmd_help,
    "state":   _cmd_dump_state,    # debug: dump raw visible_state
    "deck":    _cmd_deck_list,     # show current deck
}
```

### 4.3 Command examples

```
> cast 1 player_2              ← casts card at index [1] targeting player_2
> cast 3 wall_of_stone_004     ← casts card at index [3] targeting a creature
> land 2                       ← plays land at index [2]
> attack 4 player_2            ← declares creature at index [4] attacking player_2
> block 1 4                    ← blocks attacker index [4] with creature index [1]
> pass                         ← passes priority
> keep                         ← keeps hand (mulligan)
> mulligan                     ← takes a mulligan
> bottom 3 7                   ← bottoms cards at indices 3 and 7 (after mulligan)
> activate 5 0                 ← activates ability index [0] on permanent index [5]
> order 4 1 2                  ← damage order for attacker index [4]: blocker 1 then 2
> concede                      ← concedes the game
> help                         ← shows available commands
```

### 4.4 PDU construction

Each command handler builds the appropriate PDU dict using the factory functions from `shared/pdus.py` and enqueues it on `client.outgoing_queue`.

---

## 5. Heartbeat (`client/heartbeat.py`)

### 5.1 PING/PONG protocol

Per RFC §4.3:
- Client sends `PING` every 30 seconds.
- Server echoes `PONG` with the same `seq_num` and `timestamp`.
- If the client does not receive `PONG` within 10 seconds of sending `PING`, it considers the server unreachable and disconnects.

### 5.2 Implementation

```python
class HeartbeatManager:
    def __init__(self, client: GameClient):
        self.client = client
        self._ping_seq = 0
        self._last_ping_time: float | None = None
        self._pong_received = asyncio.Event()
        self._pong_received.set()  # start ready

    async def run(self):
        while True:
            await asyncio.sleep(30)
            # Send PING
            self._ping_seq += 1
            ping_pdu = create_ping(self._ping_seq, int(time.time() * 1000))
            await self.client.outgoing_queue.put(ping_pdu)
            self._last_ping_time = time.monotonic()
            self._pong_received.clear()

            # Wait for PONG or timeout
            try:
                await asyncio.wait_for(self._pong_received.wait(), timeout=10)
            except asyncio.TimeoutError:
                print("Server unreachable (PONG timeout). Disconnecting.")
                self.client.state = "DISCONNECTED"
                return

    def on_pong(self, pdu: dict):
        if pdu["seq_num"] == self._ping_seq:
            self._pong_received.set()
```

---

## 6. Verbose mode (`client/verbose.py`)

When `config.verbose` is `True`:
- Log every PDU sent: `[C→S] PRIORITY_PASS seq=49`
- Log every PDU received: `[S→C] PRIORITY_GRANT seq=50 player=player_1 timeout=60000`
- Log state transitions: `[CLIENT] LOBBY → MULLIGAN`
- Log connection events: `[CONN] Connected to 127.0.0.1:4444`

All verbose output goes to `stderr`.

---

## 7. Deck file format (`--deck-file`)

If `--deck-file` is provided, the client reads card IDs from a plain text file (one per line):

```
lightning_bolt_001
lightning_bolt_002
lightning_bolt_003
lightning_bolt_004
shock_001
shock_002
goblin_guide_001
mountain_001
mountain_002
mountain_003
mountain_004
mountain_005
mountain_006
mountain_007
mountain_008
mountain_009
mountain_010
mountain_011
mountain_012
mountain_013
```

Empty lines and lines starting with `#` are ignored. The client sends this as the `deck_list` in `PLAYER_READY`.

If no `--deck-file` is provided, the client prompts the user to enter card IDs interactively in the LOBBY state.

---

## 8. Error handling (client-side)

On receiving an `ERROR` PDU:
1. Print the error message prominently: `[ERROR] STALE_ACTION: Priority token mismatch.`
2. Do NOT crash or disconnect.
3. Wait for the server to re-issue `PRIORITY_GRANT` (if applicable) — the server always does this per RFC §11.

On receiving an `UNKNOWN_TYPE` error (meaning the server couldn't parse the client's PDU):
1. Print the error.
2. The game continues — the server discards the illegal action.

On `GAME_OVER`:
1. Display the result prominently.
2. Transition to LOBBY state.
3. Prompt the player to send a new `PLAYER_READY` (same connection).
4. If the player wants to quit, they can Ctrl+C.

---

## 9. Edge cases

| Scenario | Client behavior |
|----------|----------------|
| Server sends `GAME_STATE_UPDATE` while client is waiting for input | Update `visible_state`, re-render, continue waiting for input |
| Player is in the middle of typing a command when the state changes | The renderer clears and redraws; the partially typed input is preserved in the input buffer |
| Player holds priority but takes no action until timeout | Server sends `GAME_OVER(DISCONNECT)`; client displays the result |
| Player sends a spell but it gets countered before resolution | Server broadcasts `STACK_RESOLVE(result="FIZZLE")`; the renderer shows the stack emptying |
| Multiple `GAME_STATE_UPDATE` PDUs arrive rapidly | Each replaces `visible_state`; only the latest is rendered |
| TCP connection drops mid-game | The read loop catches `ConnectionError`; client prints "Disconnected" and exits |
