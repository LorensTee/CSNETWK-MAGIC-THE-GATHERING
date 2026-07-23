# CONTRIBUTING.md — Coding Rules for the LLM

> **Audience:** A code-generating LLM with a limited context window.
>
> **How to use:** Every coding prompt includes `00_architecture_master.md` + ONE module file (`01_network_protocol.md`, `02_server_engine.md`, or `03_client_app.md`). The LLM writes code ONLY for the files listed in that module. This document defines strict rules that apply to ALL modules.

---

## Rule 1: Stay in your module

- **You MAY import from `shared/`** — it is the only cross-module dependency.
- **You MUST NOT import from `server/` when working on `client/`** and vice versa.
- **You MUST NOT invent new files** outside the `"Files in this module"` list in your module document.
- If you genuinely need a new shared utility, document it in a comment and flag it for manual review — do NOT create the file unless it is in the approved list.

## Rule 2: Naming conventions

| Thing | Convention | Example |
|-------|-----------|---------|
| Modules / files | `snake_case.py` | `game_state.py`, `card_loader.py` |
| Classes | `PascalCase` | `GameState`, `PriorityManager` |
| Functions / methods | `snake_case` | `handle_cast_spell()`, `build_visible_state()` |
| Constants | `UPPER_SNAKE_CASE` | `DEFAULT_PORT`, `MAX_PDU_BYTES` |
| Variables | `snake_case` | `player_id`, `stack_item`, `life_totals` |
| Private helpers | `_leading_underscore` | `_validate_mana()`, `_next_stack_id()` |
| PDU factory functions | `create_<pdu_type>()` | `create_priority_pass(seq_num)` |
| PDU validator functions | `validate_<pdu_type>()` | `validate_cast_spell(pdu)` |
| Handler methods (server lifecycle) | `handle_<pdu_type>()` | `handle_player_ready(player_id, pdu)` |

## Rule 3: Modularity

1. **One class per file** where practical. A file may contain a primary class + closely related helper dataclasses.
2. **Dataclasses for data, classes for behavior.** Use `@dataclass` for pure data structures (e.g., `GameState`, `CardDef`, `Permanent`). Use regular classes for objects with significant behavior (e.g., `TurnEngine`, `StackManager`).
3. **No global mutable state.** Configuration is passed via constructor injection. The `GameState` object is passed explicitly to methods that mutate it.
4. **Factory functions, not subclassing**, for PDU creation. There are 25 PDU types — use `shared/pdus.py` factory functions rather than a class hierarchy.
5. **Dispatch tables, not long if-else chains.** Route incoming PDUs via a `dict[str, callable]` mapping type string → handler.

## Rule 4: Error handling

1. **Every `recv_pdu()` call** MUST handle:
   - `ProtocolError` (framing failure / invalid JSON) — log and continue; the server sends `ERROR(INVALID_JSON)`.
   - `ConnectionError` / `EOFError` — clean up and exit.
2. **Every `send_pdu()` call** MUST be inside `try/except ConnectionError` — log the failure and clean up.
3. **Server-side action handlers** MUST validate before mutating state. If validation fails, send `ERROR` with the appropriate code and leave game state unchanged.
4. **Never crash on bad client input.** A malformed PDU from a client is an `ERROR`, not a server crash.
5. **Timeouts on `PRIORITY_GRANT`** MUST be enforced with `asyncio.wait_for`. On timeout, broadcast `GAME_OVER(DISCONNECT)`.
6. **TCP drops** (connection lost mid-game) → the server MUST transition to `GAME_OVER(DISCONNECT)` for the surviving player and return to `LOBBY`. The surviving player's connection is retained.
7. **Reconnect:** If a disconnected player reconnects (same TCP connection re-established) within `disconnect_timeout_s`, the game resumes. If the timeout expires, `GAME_OVER(DISCONNECT)` is broadcast.

## Rule 5: Comments

1. **File-level docstring** at the top of every `.py` file describing its purpose and the module it belongs to.

   ```python
   """
   server/stack.py — Stack Manager (Module 02: Server Engine)

   Maintains the LIFO stack of spells and abilities. Pushes items
   on CAST_SPELL / ACTIVATE_ABILITY, resolves the top item when
   both players pass priority consecutively.
   """
   ```

2. **Function-level docstrings** for every public function/method. Use the format:

   ```python
   def resolve_top(self, state: GameState) -> list[dict]:
       """Pop and resolve the top item of the stack.

       Args:
           state: The current authoritative GameState (mutated in place).

       Returns:
           A list of state_change dicts describing what happened.
       """
   ```

3. **Inline comments** for non-obvious logic:
   - RFC rule citations: `# RFC §7.4: AP draws 1 card during Draw Step (skip on turn 1 for first player)`
   - Magic: The Gathering rules: `# Lethal damage = toughness - marked damage; must be assigned before trample overflow`
   - Protocol quirks: `# seq_num echoes the PHASE_TRANSITION, not PRIORITY_GRANT, for combat steps`

4. **TODO markers** for stubs that need later implementation:

   ```python
   # TODO: Implement triggered ability for Gray Merchant of Asphodel (devotion count)
   ```

## Rule 6: Imports

1. **Standard library first, then shared, then same-module.**
2. **Use absolute imports** from the project root (the `mtgnp/` directory).
   ```python
   from shared.constants import DEFAULT_PORT, MAX_PDU_BYTES
   from shared.framing import encode_frame, decode_frame
   from server.game_state import GameState, Permanent
   ```
3. **Do not use `*` imports.**

## Rule 7: asyncio patterns

1. **All I/O is async.** Use `async def` for any function that reads/writes to a socket or queue.
2. **Use `asyncio.Lock`** for any shared mutable resource (e.g., the `GameState` object, the outgoing write queue).
3. **Use `asyncio.Queue`** for producer-consumer patterns (outgoing PDU queues, incoming PDU dispatch).
4. **Use `asyncio.TaskGroup`** (Python 3.11+) or `asyncio.gather()` to run concurrent tasks.
5. **Never call `time.sleep()`** — use `await asyncio.sleep()`.
6. **Use `asyncio.wait_for()`** for timeouts. Never use `socket.settimeout()`.

## Rule 8: JSON handling

1. **Always `ensure_ascii=False`** when encoding JSON (for readable logs).
2. **Always catch `json.JSONDecodeError`** and translate to a `ProtocolError("INVALID_JSON")`.
3. **Always catch `UnicodeDecodeError`** when decoding bytes to UTF-8.
4. **Field names are case-sensitive.** Use the exact strings from the RFC:
   - `seq_num` (not `seqNum` or `seq_num`)
   - `player_id` (not `playerId`)
   - `card_id` (not `cardId`)
   - `time_limit_ms` (not `timeLimitMs`)

## Rule 9: Sequence number discipline

1. **Server:** Maintain a single `int` counter. Increment before every `send_pdu()`. Never skip, never reset mid-game.
2. **Client priority-bearing PDUs:** Echo the `seq_num` from the most recent `PRIORITY_GRANT` or corresponding server request PDU.
3. **Client `PLAYER_READY`:** Use a client-maintained counter starting at 1.
4. **Client `PING`:** Use a client-maintained counter; server echoes it in `PONG` unchanged.
5. **Server validation:** Every incoming priority-bearing PDU is checked:
   ```python
   if pdu["seq_num"] != self._expected_seq_num:
       await self.send_error(player_id, "STALE_ACTION",
           f"Priority token mismatch. Expected {self._expected_seq_num}, got {pdu['seq_num']}.",
           pdu)
       await self.reissue_priority_grant(player_id)
       return
   ```

## Rule 10: Testing your code

Before claiming a file is done, verify:

1. **No syntax errors:** `python -m py_compile <file>.py`
2. **Imports resolve:** The file can be imported without `ImportError`.
3. **No cross-module imports:** Does NOT import from the other top-level module.
4. **All public functions have docstrings.**
5. **RFC citations** are present for non-obvious protocol behavior.

## Rule 11: Card effect implementation order

When implementing `server/card_effects.py`, implement effects in this priority order (for grading purposes):

1. **Lightning Bolt** — deal 3 damage (simplest effect; validates the effect pipeline)
2. **Counterspell** — counter target spell (validates stack interaction)
3. **Giant Growth** — +3/+3 until end of turn (validates pump/temporary effects)
4. **Dark Ritual** — add {B}{B}{B} (validates mana system)
5. **Terror / Doom Blade** — destroy target creature (validates destruction + targeting restrictions)
6. (bonus) All remaining 53 cards

## Rule 12: No external libraries

The project MUST run with only the Python 3.10+ standard library. Do NOT add dependencies on:
- `pydantic`
- `requests`
- `aiohttp`
- `websockets`
- Any other PyPI package

The only allowed external dependency is `pytest` for testing, and that is optional.
