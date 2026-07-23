# 00 — Master Architecture

> **MTGNP 1.0 Implementation · CSNETWK T3 AY 2025–2026**
>
> This is the master index. Every coding prompt MUST include this file as context, together with exactly ONE module file (01 / 02 / 03). The code-generating LLM must NOT reference files outside the module it is currently working on unless the dependency is explicitly listed in that module's *"Files in this module"* section.

---

## 1. Tech Stack Recommendation

| Layer | Choice | Rationale |
|-------|--------|-----------|
| **Language** | **Python 3.10+** | Ubiquitous in academic settings; excellent standard library; no external dependencies required. |
| **Concurrency** | **`asyncio`** | Native async I/O with `asyncio.start_server` / `asyncio.open_connection` — ideal for a TCP server handling two concurrent client connections plus heartbeat timers. |
| **Serialization** | **`json` (stdlib)** + **`struct` (stdlib)** | JSON for PDU payloads (per RFC §5.3); `struct.pack('>I', …)` / `struct.unpack('>I', …)` for the 4-byte big-endian length prefix (per RFC §5.2). |
| **Card data** | **`csv` (stdlib)** | The card catalog ships as three CSV files; `csv.DictReader` loads them at startup. |
| **CLI** | **`argparse` (stdlib)** | `--verbose` flag on both client and server; `--host` / `--port` on the client; `--port` on the server. |
| **Testing** | **`pytest`** (optional dev dependency) | Recommended for unit tests but not required at runtime. |

**Why not Node.js?**  Node is equally capable but forces callback/promise patterns that can obscure the sequential nature of a turn-based protocol. Python's `async`/`await` reads more like the blocking pseudocode a student would sketch from the RFC.

**Why not C/C++?**  Manual memory management and the lack of a built-in JSON parser would bloat the codebase with boilerplate unrelated to the networking learning goals.

**Total external dependencies: zero.**  Everything runs on a standard Python 3.10+ installation.

---

## 2. High-Level System Design

```
 ┌─────────────────┐                          ┌─────────────────┐
 │   Player A      │                          │   Player B      │
 │   (client.py)   │                          │   (client.py)   │
 │                 │◄──────── TCP ────────────►│                 │
 │  Thin client:   │     port 4444            │  Thin client:   │
 │  - render state │                          │  - render state │
 │  - capture input│     ┌──────────────┐     │  - capture input│
 │  - heartbeat    │     │  Game Server  │     │  - heartbeat    │
 └─────────────────┘     │  (server.py)  │     └─────────────────┘
                         │               │
                         │ Authoritative │
                         │  game state   │
                         │               │
                         │ Lifecycle:    │
                         │ LOBBY→SETUP→  │
                         │ MULLIGAN→     │
                         │ IN_GAME→      │
                         │ GAME_OVER     │
                         └──────────────┘
```

### 2.1 Server responsibilities (sole source of truth)

- Accept exactly 2 TCP connections; refuse extras.
- Frame/unframe every PDU with 4-byte big-endian length prefix + UTF-8 JSON.
- Maintain the **single authoritative Game State** (all zones, life totals, turn, phase).
- Validate every client action; reject illegal actions with `ERROR` PDUs.
- Manage the game lifecycle state machine (LOBBY → GAME_SETUP → MULLIGAN → IN_GAME → GAME_OVER → LOBBY).
- Drive all turn phases/steps; broadcast `PHASE_TRANSITION`.
- Issue `PRIORITY_GRANT`; enforce `time_limit_ms`.
- Resolve the Stack (LIFO); broadcast `STACK_PUSH` / `STACK_RESOLVE`.
- Compute combat damage; broadcast `COMBAT_DAMAGE_RESULT`.
- Filter hidden information (hands) per-player in `GAME_STATE_UPDATE`.
- Respond to `PING` with `PONG`.

### 2.2 Client responsibilities (thin, never authoritative)

- Connect to the server via TCP.
- Frame/unframe every PDU identically to the server.
- Accept `GAME_STATE_UPDATE` as the authoritative state; discard local state that conflicts.
- Render the *Visible State* for the human player (hide opponent's hand).
- Send PING every 30 s; disconnect if no PONG within 10 s.
- Capture player input and translate it into valid PDUs.
- Exit or reconnect on `GAME_OVER`.

### 2.3 Data flow invariant

> **All game logic lives on the server.** A client that tries to validate actions locally risks displaying inconsistent state. The client only sends *intent* PDUs (CAST_SPELL, PLAY_LAND, etc.) and the server responds with the updated state.

---

## 3. Directory Tree

```
mtgnp/
│
├── README.md                          # Build/run instructions, work distribution, AI disclosure
├── 00_architecture_master.md          # ← THIS FILE
├── 01_network_protocol.md             # Module 1 reference
├── 02_server_engine.md                # Module 2 reference
├── 03_client_app.md                   # Module 3 reference
├── CONTRIBUTING.md                    # Coding rules for the LLM
├── setup_project.sh                   # One-shot directory + file creator
│
├── data/                              # Static card catalog (pre-loaded, never written)
│   ├── mtgnp_master_card_list.csv     # 58 unique card types with costs, P/T, effects
│   ├── mtgnp_color_summary.csv        # Color summary / counts
│   └── mtgnp_card_instances.csv       # Every valid card_id (312 instances)
│
├── shared/                            # Code used by BOTH server and client
│   ├── __init__.py
│   ├── framing.py                     # 4-byte big-endian length prefix encode/decode
│   ├── pdus.py                        # PDU type registry, create/parse/validate helpers
│   ├── constants.py                   # Port, max PDU size, phase enum, error codes
│   └── verbose.py                     # Shared verbose-mode formatting utilities
│
├── server/                            # Game Server (Module 2)
│   ├── __init__.py
│   ├── main.py                        # Entry point: argparse, start server
│   ├── config.py                      # Server configuration dataclass
│   ├── server.py                      # Top-level asyncio server orchestration
│   ├── connection.py                  # Per-client read/write loops with framing
│   ├── dispatcher.py                  # Route incoming PDUs to handlers by type
│   ├── game_state.py                  # Authoritative GameState data structures
│   ├── game_lifecycle.py              # LOBBY→SETUP→MULLIGAN→IN_GAME→GAME_OVER FSM
│   ├── mulligan.py                    # London Mulligan logic
│   ├── turn_engine.py                 # Phase/step transitions & turn sequencing
│   ├── priority.py                    # Priority token, time-limit enforcement
│   ├── stack.py                       # Stack push/pop/resolve with state changes
│   ├── combat.py                      # Attackers, blockers, damage assignment, results
│   ├── card_loader.py                 # Parse CSV files into CardDef / CardInstance dicts
│   ├── card_effects.py                # Implement every card ability/effect
│   ├── mana.py                        # Mana pool, cost payment, tapping validation
│   ├── validators.py                  # Action legality checks (targets, timing, costs)
│   └── verbose.py                     # Server-side verbose PDU logging
│
├── client/                            # Player Client (Module 3)
│   ├── __init__.py
│   ├── main.py                        # Entry point: argparse, connect
│   ├── config.py                      # Client configuration dataclass
│   ├── client.py                      # Client state machine (connect, play, reconnect)
│   ├── connection.py                  # Server read/write loops with framing
│   ├── dispatcher.py                  # Route incoming PDUs to handlers by type
│   ├── renderer.py                    # Terminal-based Visible State rendering
│   ├── input_handler.py               # Player keyboard input → PDU creation
│   ├── heartbeat.py                   # PING/PONG 30 s timer + 10 s timeout
│   └── verbose.py                     # Client-side verbose PDU logging
│
└── tests/                             # (Optional) pytest test suite
    ├── __init__.py
    ├── test_framing.py
    ├── test_pdus.py
    ├── test_game_state.py
    ├── test_stack.py
    └── test_combat.py
```

---

## 4. Module Index

When the code-generating LLM works on a specific module, it receives this master file PLUS the module file below. It MUST NOT reference files from other modules directly. All inter-module communication goes through `shared/`.

| Module file | What it covers | Source files |
|---|---|---|
| **`01_network_protocol.md`** | TCP framing, PDU definitions, seq_num rules, error codes, heartbeat | `shared/framing.py`, `shared/pdus.py`, `shared/constants.py`, `shared/verbose.py`, `server/connection.py`, `client/connection.py` |
| **`02_server_engine.md`** | Game lifecycle FSM, turn/phase engine, priority, stack, combat, card effects, mana, validation | All `server/*.py` (except `connection.py` — that belongs to Module 1) |
| **`03_client_app.md`** | Client state machine, terminal rendering, input capture, heartbeat | All `client/*.py` (except `connection.py` — that belongs to Module 1) |

### Dependency graph

```
shared/   ←  (no internal dependencies)
  ↑
  ├── server/   ←  imports shared/* only
  │
  └── client/   ←  imports shared/* only
```

The server and client NEVER import each other. They communicate exclusively via TCP PDUs.

---

## 5. Build & Run (quick reference)

```bash
# Install (zero dependencies beyond Python 3.10+)
cd mtgnp

# Start server (verbose mode)
python -m server.main --verbose

# Start client A (verbose mode, connects to localhost:4444)
python -m client.main --verbose --player-id player_1

# Start client B (verbose mode)
python -m client.main --verbose --player-id player_2
```

The server accepts connections on TCP port 4444. Both clients must connect before the game begins. After `GAME_OVER`, the same connections are reused — clients send a fresh `PLAYER_READY` to start a new game.
