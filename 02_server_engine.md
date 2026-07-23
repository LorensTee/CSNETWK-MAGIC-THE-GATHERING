# 02 — Server Engine Module

> **Module scope:** Game lifecycle FSM, turn/phase engine, priority/stack resolution, combat system, card effects, mana, validation.
>
> **Paired with:** `00_architecture_master.md` (always include the master as context).
>
> **Dependencies:** `shared/framing.py`, `shared/pdus.py`, `shared/constants.py`, `shared/verbose.py` (from Module 1). The server also imports `server/connection.py` (from Module 1) for the per-client read/write loops.

---

## Files in this module

| File | Purpose |
|------|---------|
| `server/main.py` | Entry point: `argparse`, start the asyncio event loop, wire up `GameServer` |
| `server/config.py` | `ServerConfig` dataclass: port, host, verbose flag, timeout values |
| `server/server.py` | `GameServer` class: accept connections, instantiate `GameLifecycle`, orchestrate top-level flow |
| `server/dispatcher.py` | Route an incoming PDU dict + `player_id` → call the correct handler method on the lifecycle |
| `server/game_state.py` | `GameState` dataclass: all zones (library, hand, battlefield, graveyard, stack), life totals, turn, phase, priority holder |
| `server/game_lifecycle.py` | `GameLifecycle` class: LOBBY → GAME_SETUP → MULLIGAN → IN_GAME → GAME_OVER FSM, `handle_<pdu_type>()` methods |
| `server/mulligan.py` | London Mulligan logic: draw 7, bottom N cards |
| `server/turn_engine.py` | `TurnEngine` class: phase/step sequencing, auto-advance through UNTAP/DRAW/CLEANUP, priority window management |
| `server/priority.py` | `PriorityManager` class: grant priority to AP then NAP, enforce `time_limit_ms`, detect consecutive passes |
| `server/stack.py` | `StackManager` class: push items (spells/abilities/triggers), resolve top item, detect fizzle, produce `STACK_PUSH` / `STACK_RESOLVE` |
| `server/combat.py` | `CombatManager` class: declare attackers/blockers, assign damage order, compute first-strike + regular damage, produce `COMBAT_DAMAGE_RESULT` |
| `server/card_loader.py` | `CardLoader` class: parse `data/mtgnp_master_card_list.csv` + `data/mtgnp_card_instances.csv` into `CardDef` and `CardInstance` objects |
| `server/card_effects.py` | `resolve_effect(card_def, game_state, ...)` — execute every card's simplified effect |
| `server/mana.py` | `ManaPool` class: track floating mana, validate mana payments against costs, handle generic mana |
| `server/validators.py` | `validate_cast()`, `validate_attack()`, `validate_block()`, `validate_target()`, etc. — return `(ok: bool, error_code: str, message: str)` |
| `server/verbose.py` | Server-side verbose logging helpers |

---

## 1. Entry point (`server/main.py`)

```python
import argparse
import asyncio
from server.config import ServerConfig
from server.server import GameServer

def main():
    parser = argparse.ArgumentParser(description='MTGNP Game Server')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=4444)
    parser.add_argument('--verbose', action='store_true', default=False)
    parser.add_argument('--time-limit-ms', type=int, default=60000,
                        help='Default priority time limit in ms')
    parser.add_argument('--disconnect-timeout-s', type=int, default=30,
                        help='Seconds before a disconnected player is considered gone')
    args = parser.parse_args()

    config = ServerConfig(
        host=args.host,
        port=args.port,
        verbose=args.verbose,
        time_limit_ms=args.time_limit_ms,
        disconnect_timeout_s=args.disconnect_timeout_s,
    )
    asyncio.run(GameServer(config).run())

if __name__ == '__main__':
    main()
```

---

## 2. Game Lifecycle State Machine (`server/game_lifecycle.py`)

### 2.1 States

```
LOBBY ──→ GAME_SETUP ──→ MULLIGAN ──→ IN_GAME ──→ GAME_OVER
  ↑                                                    │
  └────────────────────────────────────────────────────┘
```

### 2.2 LOBBY state

**Entry:** On server startup, and after every `GAME_OVER` broadcast.

**Behavior:**
1. Wait for a valid `PLAYER_READY` from each connected client.
2. On receiving `PLAYER_READY`:
   - Validate `player_id` is non-empty and not already claimed by the other player → else `ERROR` code `DUPLICATE_ID`.
   - Validate `deck_list`: 1–50 cards, every card ID is in the legal set → else `ERROR` code `ILLEGAL_DECK`.
   - Store the deck list for that player.
   - Send a personalized `GAME_STATE_UPDATE` (lobby variant) to the player.
3. If a player sends another `PLAYER_READY` before both are ready, replace the earlier submission.
4. When **both** players have sent valid `PLAYER_READY` → transition to `GAME_SETUP`.

### 2.3 GAME_SETUP state

**Entry:** Both players ready.

**Behavior (fully automatic — no client input):**
1. Validate both deck lists server-side (double-check legality).
2. Set both players' life totals to 20.
3. Shuffle each deck (use `random.shuffle`).
4. Draw 7 cards per player.
5. Determine first player via random coin flip (`random.choice`).
6. Send **personalized** `GAME_STATE_UPDATE` to each player (their hand visible, opponent's hand hidden as `hand_counts`).
7. Transition to `MULLIGAN`.

### 2.4 MULLIGAN state

**Entry:** After GAME_SETUP.

**London Mulligan rule:**
- Each player independently sends `MULLIGAN_CHOICE` with `keep: true` or `keep: false`.
- If `keep: false` → server shuffles that player's hand back into library, draws a fresh 7-card hand, increments that player's mulligan count, sends a personalized `GAME_STATE_UPDATE` with the new hand.
- If `keep: true` → player must include `cards_to_bottom` with exactly N card IDs (N = number of mulligans taken). Server moves those cards to the bottom of their library.
- The two players decide independently — Player 1 keeping does not block Player 2 from mulliganing.
- When **both** players have sent `keep: true` → transition to `IN_GAME`.

### 2.5 IN_GAME state

The bulk of the game logic. Delegates to:
- `TurnEngine` for phase/step sequencing
- `PriorityManager` for priority windows
- `StackManager` for spell/ability resolution
- `CombatManager` for combat steps
- `validators.py` for action legality
- `card_effects.py` for effect resolution

When a win/loss condition is detected → transition to `GAME_OVER`.

### 2.6 GAME_OVER state

1. Broadcast `GAME_OVER` with `winner_id`, `loser_id`, `reason`.
2. Reset all game state.
3. Transition back to `LOBBY` (TCP connections are preserved).
4. Do NOT broadcast `PHASE_TRANSITION` — `GAME_OVER` itself signals the return to LOBBY.

### 2.7 Immediate GAME_OVER triggers

Any state can transition to `GAME_OVER` if:
- A player's life total ≤ 0 (`LIFE_ZERO`)
- A player attempts to draw from an empty library (`DECK_EMPTY`)
- A player sends `CONCEDE`
- A player disconnects and the reconnect timer expires (`DISCONNECT`)

---

## 3. Game State (`server/game_state.py`)

### 3.1 Data structures

```python
@dataclass
class Permanent:
    id: str                # card instance ID, e.g. "goblin_guide_001"
    card_def_id: str       # base card ID, e.g. "goblin_guide"
    controller: str        # player_id
    tapped: bool = False
    damage: int = 0        # creature only
    summoning_sick: bool = True  # creature only
    enchanted_by: list[str] = field(default_factory=list)  # aura card IDs

@dataclass
class StackItem:
    stack_item_id: str     # "stk_01", "stk_02", ...
    item_type: str         # "SPELL" | "ABILITY" | "TRIGGER_ABILITY"
    source: str            # card_id
    controller: str        # player_id
    targets: list[str]     # player_ids or permanent ids
    # Internal fields (not sent to clients):
    card_def: Any = None   # CardDef reference for resolution

@dataclass
class GameState:
    phase: str                       # current phase (LOBBY, MULLIGAN, UNTAP, etc.)
    turn: int                        # turn number (0 during MULLIGAN, 1+ during IN_GAME)
    active_player: str | None        # player_id of AP
    priority_holder: str | None      # player_id, or None during UNTAP/CLEANUP
    life_totals: dict[str, int]      # player_id → life
    libraries: dict[str, list[str]]  # player_id → ordered list of card_ids (top = index 0)
    hands: dict[str, list[str]]      # player_id → list of card_ids
    battlefield: dict[str, list[Permanent]]  # player_id → permanents
    graveyards: dict[str, list[str]]         # player_id → ordered list (first buried at [0])
    stack: list[StackItem]                  # index -1 = top of stack
    land_played_this_turn: bool = False
    mulligan_counts: dict[str, int] = field(default_factory=lambda: {"player_1": 0, "player_2": 0})
    players_ready: int = 0           # LOBBY: count of players who submitted PLAYER_READY
    waiting_for: list[str] = field(default_factory=list)  # LOBBY: player_ids not yet ready
    stack_counter: int = 0           # auto-increment for stack_item_id generation
```

### 3.2 Visible State filtering

When sending `GAME_STATE_UPDATE` to player P:

```python
def build_visible_state(game_state: GameState, for_player: str, card_defs: dict) -> dict:
    opponent = "player_2" if for_player == "player_1" else "player_1"
    return {
        "turn": game_state.turn,
        "active_player": game_state.active_player,
        "phase": game_state.phase,
        "priority_holder": game_state.priority_holder,
        "life_totals": dict(game_state.life_totals),
        "stack": [serialize_stack_item(si) for si in game_state.stack],
        "battlefield": {
            for_player: [serialize_permanent(p) for p in game_state.battlefield[for_player]],
            opponent: [serialize_permanent(p) for p in game_state.battlefield[opponent]],
        },
        "graveyard": {
            for_player: list(game_state.graveyards[for_player]),
            opponent: list(game_state.graveyards[opponent]),
        },
        "hand": list(game_state.hands[for_player]),        # FULL hand visible
        "hand_counts": {opponent: len(game_state.hands[opponent])},  # OPPONENT: count only
        "library_counts": {
            for_player: len(game_state.libraries[for_player]),
            opponent: len(game_state.libraries[opponent]),
        },
        "land_played_this_turn": game_state.land_played_this_turn,
    }
```

Creature permanents include `power`, `toughness`, `damage`, `summoning_sick`, and `abilities` (list of ability indices). Non-creature permanents include only `id` and `tapped`.

---

## 4. Turn Engine (`server/turn_engine.py`)

### 4.1 Phase sequence

```python
TURN_PHASES = [
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
```

### 4.2 Step-by-step

| Phase | Auto-advance? | Priority window? | Special rules |
|-------|:---:|:---:|---|
| UNTAP | Yes | No | Untap all AP's permanents; reset `land_played_this_turn`; advance immediately |
| UPKEEP | No | Yes (AP then NAP) | Standard priority |
| DRAW | Partial | Yes | AP draws 1 card (skip on turn 1 for the player going first); then priority |
| PRECOMBAT_MAIN | No | Yes (AP then NAP) | Land drop allowed; sorcery-speed actions allowed for AP |
| BEGIN_COMBAT | No | Yes | |
| DECLARE_ATTACKERS | No | Special | AP sends `DECLARE_ATTACKERS`; if no attackers declared, skip to `END_OF_COMBAT` |
| DECLARE_BLOCKERS | No | Special | NAP sends `DECLARE_BLOCKERS` |
| ASSIGN_DAMAGE_ORDER | Conditional | Special | Only if multiple blockers assigned to one attacker; AP sends `ASSIGN_DAMAGE_ORDER` |
| FIRST_STRIKE_DAMAGE | Conditional | No | Only if any creature has first strike; auto-computed |
| COMBAT_DAMAGE | Conditional | No | Auto-computed; broadcast `COMBAT_DAMAGE_RESULT` |
| END_OF_COMBAT | No | Yes | Standard priority |
| POSTCOMBAT_MAIN | No | Yes (AP then NAP) | Second main phase; AP may play a land if not already played |
| END_STEP | No | Yes | "At the beginning of the end step" triggers |
| CLEANUP | Yes | No | Discard to 7 if needed; clear damage; remove "until end of turn" effects; advance turn |

### 4.3 Advancing

The server broadcasts `PHASE_TRANSITION(from_phase, to_phase, active_player, turn)` before entering each phase/step. After a phase where both players pass priority with an empty stack, the server advances to the next phase.

After CLEANUP:
- `turn += 1`
- Swap active player
- Loop back to UNTAP

---

## 5. Priority Manager (`server/priority.py`)

### 5.1 Priority pattern

For every priority window:

```
1. S→AP  PRIORITY_GRANT(seq=N, time_limit_ms=T)
2. Wait for AP's response (or timeout → GAME_OVER DISCONNECT)
3. S→NAP PRIORITY_GRANT(seq=N+1, time_limit_ms=T)
4. Wait for NAP's response
5. If both passed and stack is empty → advance phase
6. If both passed and stack is non-empty → resolve top of stack, then re-open priority to AP
7. If a player took an action (cast spell, activate ability) → re-open priority to that player
```

### 5.2 Time limit enforcement

Use `asyncio.wait_for` with the `time_limit_ms` value. On timeout:
1. Broadcast `GAME_OVER` with reason `DISCONNECT`.
2. Return to LOBBY.
3. The timed-out player's connection is still alive (per spec).

### 5.3 seq_num tracking

The server maintains a single monotonically increasing counter. Every server-issued PDU increments it before sending. Client actions must echo the `seq_num` from the most recent `PRIORITY_GRANT` (or corresponding request PDU for mulligan/discard/combat steps). Actions with wrong seq_num → `ERROR` code `STALE_ACTION`, then re-issue the current `PRIORITY_GRANT`.

---

## 6. Stack Manager (`server/stack.py`)

### 6.1 Pushing

When a spell is cast or ability is activated:
1. Validate the action (costs, targets, timing) via `validators.py`.
2. Deduct mana / tap permanents / move card from hand to stack.
3. Create a `StackItem` with a unique `stack_item_id` (`stk_01`, `stk_02`, ...).
4. Push onto the stack (append to list).
5. Broadcast `STACK_PUSH` to ALL.
6. Re-open priority to the caster.

### 6.2 Resolving

When both players pass priority consecutively with a non-empty stack:
1. Pop the top item.
2. If it fizzled (all targets became illegal) → broadcast `STACK_RESOLVE(result="FIZZLE")`.
3. If it resolves → execute the effect via `card_effects.py`, collect state changes.
4. Broadcast `STACK_RESOLVE(result="RESOLVED", state_changes=[...])`.
5. Send `GAME_STATE_UPDATE` to reflect new state.
6. Re-open priority to AP.

### 6.3 State change types

| `change_type` | Meaning | Extra fields |
|---------------|---------|-------------|
| `DAMAGE` | Deal damage | `target`, `amount` |
| `LIFE_GAIN` | Gain life | `target`, `amount` |
| `DESTROY` | Destroy permanent | `target` |
| `PERMANENT_ENTERS` | Creature/artifact/enchantment enters | `card_id`, `controller`, `tapped` |
| `EXILE` | Exile permanent | `target` |
| `COUNTER` | Spell is countered | `target` (stack_item_id) |
| `DRAW` | Player draws cards | `player`, `count` |
| `DISCARD` | Player discards | `player`, `card_ids` |
| `RETURN_TO_HAND` | Bounce permanent | `target` |
| `TAP` | Tap permanent | `target` |
| `UNTAP` | Untap permanent | `target` |
| `LIFE_LOSS` | Lose life (not damage) | `target`, `amount` |

---

## 7. Combat System (`server/combat.py`)

### 7.1 Declare Attackers

1. AP sends `DECLARE_ATTACKERS` with a list of `{creature_id, target}`.
2. Validate:
   - Each `creature_id` is on AP's battlefield, untapped, and a creature.
   - No summoning sickness (unless haste).
   - Each `target` is the opponent's `player_id`.
3. Tap each declared attacker.
4. Broadcast updated `GAME_STATE_UPDATE`.
5. Advance to `DECLARE_BLOCKERS`.

If `attackers` is empty → skip to `END_OF_COMBAT`.

### 7.2 Declare Blockers

1. NAP sends `DECLARE_BLOCKERS` with a list of `{creature_id, blocking_id}`.
2. Validate:
   - Each `creature_id` is on NAP's battlefield, untapped, and a creature.
   - Each `blocking_id` is an attacking creature.
   - No creature blocks more than one attacker (unless specified by an ability).
3. Record blocking assignments.
4. Advance to `ASSIGN_DAMAGE_ORDER` (only if any attacker has multiple blockers), else skip to `COMBAT_DAMAGE`.

### 7.3 Assign Damage Order

If an attacker is blocked by multiple creatures, AP sends `ASSIGN_DAMAGE_ORDER(attacker_id, blocker_order)` specifying the order in which the attacker deals damage to blockers. Damage must be lethal to each blocker before moving to the next.

### 7.4 Damage Steps

1. **First Strike Damage**: Only creatures with first strike deal damage in this step. If no first-strike creatures exist, skip.
2. **Combat Damage**: All surviving creatures deal damage simultaneously.
   - Attacking creatures deal damage equal to their power to the defending player (if unblocked) or to their blockers (in order, lethal required before overflow).
   - Blocking creatures deal damage equal to their power to the attacker they're blocking.
3. After damage:
   - Creatures with damage ≥ toughness are destroyed (moved to graveyard).
   - Damage remains marked until CLEANUP.
4. Broadcast `COMBAT_DAMAGE_RESULT` with `damage_events`, `life_totals`, `creatures_died`.

---

## 8. Card Loader (`server/card_loader.py`)

### 8.1 Input files

- `data/mtgnp_master_card_list.csv` — 58 unique card types with costs, P/T, effects
- `data/mtgnp_card_instances.csv` — 312 individual card IDs

### 8.2 Output data structures

```python
@dataclass
class CardDef:
    card_id_base: str       # e.g. "lightning_bolt"
    name: str               # e.g. "Lightning Bolt"
    card_type: str          # "Instant", "Sorcery", "Creature", "Land", "Enchantment", "Artifact", "Artifact Creature"
    subtype: str            # e.g. "Goblin Scout" or ""
    color: str              # "W", "U", "B", "R", "G", "C"
    cmc: int                # converted mana cost
    mana_cost: dict[str, int]  # e.g. {"R": 1, "X": 0}
    power: int | None       # creature only
    toughness: int | None   # creature only
    copies_in_set: int      # 4 or 20
    simplified_effect: str  # human-readable effect text
    abilities: list[dict]   # parsed ability descriptors (for game logic)
```

The `CardLoader` parses the CSV, builds `CardDef` objects keyed by `card_id_base`, and also builds a set of all valid instance IDs for deck validation.

---

## 9. Card Effects (`server/card_effects.py`)

### 9.1 Ability keywords

The server MUST implement these keyword abilities:

| Keyword | Effect |
|---------|--------|
| **Haste** | Creature ignores summoning sickness for attacking and tap abilities |
| **Flying** | Can only be blocked by creatures with flying or reach |
| **First Strike** | Deals damage in the first strike damage step |
| **Trample** | Excess damage beyond lethal to blockers is dealt to defending player |
| **Defender** | Cannot attack |
| **Vigilance** | Attacking does not cause this creature to tap |
| **Hexproof** | Cannot be the target of spells or abilities controlled by opponents |
| **Protection from {color}** | Cannot be targeted/damaged/enchanted/blocked by that color |
| **Prowess** | +1/+1 until end of turn whenever you cast a noncreature spell |

### 9.2 Card-specific effects

For each of the 58 cards, `resolve_effect(card_def, game_state, controller, targets, ...)` applies the effect. The implementation uses a dispatch table keyed by `card_id_base`:

```python
EFFECT_HANDLERS = {
    "lightning_bolt":   lambda gs, ctrl, tgt, ...: deal_damage(gs, tgt[0], 3),
    "shock":            lambda gs, ctrl, tgt, ...: deal_damage(gs, tgt[0], 2),
    "counterspell":     lambda gs, ctrl, tgt, ...: counter_target(gs, tgt[0]),
    "giant_growth":     lambda gs, ctrl, tgt, ...: pump_until_eot(gs, tgt[0], 3, 3),
    "dark_ritual":      lambda gs, ctrl, tgt, ...: add_mana(gs, ctrl, {"B": 3}),
    # ... all 58 cards
}
```

Each handler returns a list of `state_changes` dicts.

### 9.3 Minimum card effects for grading (5+ required per rubric)

The rubric requires **at least 5 card effects** for full marks on priority/stack. However, all 58 cards should be stubbed out; the effect handlers can be filled in incrementally. Cards with no special effect (e.g., Grizzly Bears) resolve with just `PERMANENT_ENTERS`.

### 9.4 Triggered abilities

Some cards have triggered abilities (e.g., Gray Merchant "When ~ enters, each opponent loses X life..."). The server must:

1. Detect the trigger event (e.g., PERMANENT_ENTERS).
2. Create a trigger item.
3. If multiple triggers need ordering → send `TRIGGER_ORDER` to the appropriate player.
4. If a trigger requires a choice → send `TRIGGER_CHOICE`.
5. Push the trigger ability onto the stack once ordered/choices are made.

---

## 10. Mana System (`server/mana.py`)

### 10.1 Mana pool

```python
@dataclass
class ManaPool:
    W: int = 0  # White
    U: int = 0  # Blue
    B: int = 0  # Black
    R: int = 0  # Red
    G: int = 0  # Green
    C: int = 0  # Colorless
```

### 10.2 Tapping for mana

When a player taps a land (or mana dork), add the appropriate color to their mana pool. The mana pool empties at the end of each step/phase.

### 10.3 Validating mana payment

```python
def can_pay(mana_payment: dict[str, int], mana_cost: dict[str, int], mana_pool: ManaPool) -> bool:
    """
    mana_payment: {"R": 1, "X": 2} — what the player is offering
    mana_cost:    {"R": 1, "X": 2} — what the spell costs
    mana_pool:    available floating mana
    Generic cost (key "X") can be paid with any color.
    """
```

The server must also validate that the tapped lands/permanents are actually available (untapped, under the player's control).

---

## 11. Validators (`server/validators.py`)

Central place for all action legality checks. Each validator returns `(ok: bool, error_code: str | None, message: str)`.

Key validators:

| Function | Checks |
|----------|--------|
| `validate_cast_spell(state, player, card_id, targets, mana_payment)` | Card in hand, mana payable, legal targets, timing (sorcery vs instant), phase restrictions |
| `validate_play_land(state, player, card_id)` | Card is a land, in hand, land not already played this turn, player is AP in main phase |
| `validate_activate_ability(state, player, source_id, ability_index, targets, cost_payment)` | Source is on battlefield, controller is player, not summoning-sick (if tap ability), costs payable, legal targets |
| `validate_attack(state, player, attackers)` | Each creature untapped, no summoning sickness (unless haste), valid target |
| `validate_block(state, player, blockers)` | Each creature untapped, blocking a declared attacker |
| `validate_mulligan(state, player, keep, cards_to_bottom)` | Keep=true → len(cards_to_bottom) == mulligan count; Keep=false → cards_to_bottom empty |
| `validate_discard(state, player, card_ids)` | Cards in hand, correct count (hand size - 7) |
| `validate_target(state, target_id, legal_targets)` | Target is in the legal targets list |

---

## 12. Server orchestration (`server/server.py` + `server/dispatcher.py`)

### 12.1 `GameServer.run()`

```python
class GameServer:
    async def run(self):
        server = await asyncio.start_server(
            self._handle_client, self.config.host, self.config.port
        )
        # Main loop: continuously run game sessions
        while True:
            await self._wait_for_two_connections()
            lifecycle = GameLifecycle(self.config, self.card_loader)
            # Run the full lifecycle (LOBBY → … → GAME_OVER)
            await lifecycle.run(self.connections)
            # After GAME_OVER, loop back — connections are reused
```

### 12.2 `dispatcher.py`

```python
async def dispatch(lifecycle: GameLifecycle, player_id: str, pdu: dict):
    pdu_type = pdu["type"]
    handler = {
        "PLAYER_READY":          lifecycle.handle_player_ready,
        "MULLIGAN_CHOICE":       lifecycle.handle_mulligan_choice,
        "PRIORITY_PASS":         lifecycle.handle_priority_pass,
        "CAST_SPELL":            lifecycle.handle_cast_spell,
        "ACTIVATE_ABILITY":      lifecycle.handle_activate_ability,
        "PLAY_LAND":             lifecycle.handle_play_land,
        "DECLARE_ATTACKERS":     lifecycle.handle_declare_attackers,
        "DECLARE_BLOCKERS":      lifecycle.handle_declare_blockers,
        "ASSIGN_DAMAGE_ORDER":   lifecycle.handle_assign_damage_order,
        "DISCARD":               lifecycle.handle_discard,
        "TRIGGER_ORDER_RESPONSE":lifecycle.handle_trigger_order_response,
        "TRIGGER_CHOICE_RESPONSE":lifecycle.handle_trigger_choice_response,
        "CONCEDE":               lifecycle.handle_concede,
        "PING":                  lifecycle.handle_ping,
    }.get(pdu_type)
    if handler is None:
        await lifecycle.send_error(player_id, "UNKNOWN_TYPE", f"Unknown PDU type: {pdu_type}", pdu)
        return
    await handler(player_id, pdu)
```

---

## 13. Verbose mode (`server/verbose.py`)

When `config.verbose` is `True`:

- Log every PDU received: `[C→S player_1] PRIORITY_PASS seq=49`
- Log every PDU sent: `[S→C player_1] PRIORITY_GRANT seq=50 player=player_1 timeout=60000`
- Log state transitions: `[STATE] LOBBY → GAME_SETUP`
- Log phase transitions: `[PHASE] Turn 3: PRECOMBAT_MAIN → BEGIN_COMBAT`
- Log stack actions: `[STACK] PUSH stk_03: lightning_bolt_001 targeting player_2`
- Log combat: `[COMBAT] goblin_guide_001 deals 2 damage to player_2`

All verbose output goes to `stderr` (so `stdout` can be redirected for other purposes).
