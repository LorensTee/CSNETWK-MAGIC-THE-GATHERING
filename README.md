# MTGNP 1.0 — Magic: The Gathering Multiplayer Network Protocol

A networked two-player implementation of Magic: The Gathering using the
MTGNP 1.0 protocol (RFC 0001, CSNETWK).  Built with Python 3.10+ using
only the standard library.

---

## Build & Run

### Prerequisites

- Python 3.10 or later
- No external dependencies required (standard library only)

### Starting the Server

```bash
python -m server.main --port 4444 --verbose
```

Optional flags:

| Flag | Default | Description |
|------|---------|-------------|
| `--host` | `0.0.0.0` | IP address to bind |
| `--port` | `4444` | TCP port |
| `--verbose` | off | Print all PDUs sent/received to stderr |
| `--time-limit-ms` | `60000` | Priority time limit in milliseconds |
| `--disconnect-timeout-s` | `30` | Seconds before a disconnected player times out |

### Starting a Client

```bash
python -m client.main --player-id player_1 --host 127.0.0.1 --port 4444 --verbose
```

Required flags:

| Flag | Description |
|------|-------------|
| `--player-id` | Unique player identifier (choose any non-empty string) |

Optional flags:

| Flag | Default | Description |
|------|---------|-------------|
| `--host` | `127.0.0.1` | Server hostname or IP |
| `--port` | `4444` | Server TCP port |
| `--deck-file` | (prompt) | Path to text file with one card ID per line |
| `--verbose` | off | Print all PDUs sent/received to stderr |

### Deck File Format

Create a plain text file with one card ID per line (lines starting with `#`
are ignored):

```
# My burn deck
lightning_bolt_001
lightning_bolt_002
lightning_bolt_003
lightning_bolt_004
mountain_001
mountain_002
mountain_003
# ... up to 50 cards
```

Then start the client with: `--deck-file my_deck.txt`

### Verbose Mode

Both the server and client support a `--verbose` flag.  When enabled, every
PDU sent and received is printed to stderr in a readable, labelled format:

```
[S→C player_1] PRIORITY_GRANT seq=50 player=player_1 timeout=60000ms
[C→S player_1] CAST_SPELL seq=50 card=lightning_bolt_001 →['player_2']
[S→C player_1] STACK_PUSH seq=51 id=stk_01 source=lightning_bolt_001
```

Verbose mode is **required** during the MP demo — the MP will not be checked
without it.

### Running Tests

```bash
python -m pytest tests/ -v
```

Or without pytest:

```bash
python -m unittest discover tests -v
```

---

## Work Distribution Matrix

| Task / Feature | Member 1 | Member 2 | Member 3 | Member 4 |
|---|---|---|---|---|
| TCP Server: connection handling, framing, dispatch | | | | |
| Game lifecycle: LOBBY, GAME_SETUP, MULLIGAN logic | | | | |
| Turn & phase engine (all phases/steps, transitions) | | | | |
| Priority & Stack logic, spell/ability resolution | | | | |
| Combat system (attackers, blockers, damage) | | | | |
| Client implementation & state rendering | | | | |
| PDU serialisation/deserialisation (all 25 PDU types) | | | | |
| Error handling, PING/PONG heartbeat, disconnect logic | | | | |
| Verbose mode (client + server PDU logging) | | | | |
| Testing & interoperability | | | | |
| README / documentation / AI disclosure | | | | |

---

## AI Usage

The following AI tools were used during the development of this project:

| Tool | How it was used |
|------|-----------------|
| **Reasonix / Claude** | System architecture design, code generation for all modules (shared, server, client), test file generation, specification auditing, and bug fixing. |
| | All AI-generated code was reviewed, tested, and verified before inclusion. |

### AI Usage Policy Compliance

- All AI-generated code has been **reviewed and tested** before submission.
- Every team member can explain all parts of the code, including AI-generated sections.
- No AI-generated code was blindly copied without comprehension or testing.
- No AI-generated content was shared between groups.
- This section documents every AI tool used and how it was used.

---

## Known Limitations

1. **Cleanup discard not fully automatic**: When a player's hand exceeds 7
   cards during the Cleanup step, the `_cleanup_discard_for` flag is set
   but the server does not currently open a priority window to prompt the
   player for discard.  The discard validator (`handle_discard`) exists
   and will process DISCARD PDUs if sent, but the server does not request
   them automatically.

2. **Assign Damage Order auto-assigned**: When multiple blockers are assigned
   to a single attacker, the damage order is set to the natural blocker order
   rather than prompting the attacking player to choose.  The `ASSIGN_DAMAGE_ORDER`
   PDU format is implemented and ready for this feature.

3. **Activated abilities (tap abilities)**: Activated abilities (e.g.,
   Llanowar Elves' `{T}: Add {G}`, Prodigal Sorcerer's `{T}: deal 1 damage`)
   are recognised in card data but the server processing logic is a stub.
   The `ACTIVATE_ABILITY` endpoint accepts PDUs but currently performs no
   game-state changes.

4. **Triggered abilities**: A static `TRIGGER_REGISTRY` identifies which
   cards produce triggers (Goblin Guide, Gray Merchant, Gravedigger, etc.)
   and the `check_triggers()` function detects them.  However, the full
   trigger-stack lifecycle (ordering, choices, resolution) is not wired
   into the game loop.

5. **Test coverage**: Unit tests cover framing, PDU definitions, game state,
   the stack, and combat.  Integration tests and end-to-end protocol tests
   are not included.

6. **First-turn draw skip**: Per the MTGNP spec, the first player does not
   draw a card on Turn 1.  This is correctly implemented in
   `server/turn_engine.py`.

---

## Project Structure

```
mtgnp/
├── 00_architecture_master.md   # System architecture and module index
├── 01_network_protocol.md      # Module 1 — PDU definitions, framing
├── 02_server_engine.md         # Module 2 — server game logic
├── 03_client_app.md            # Module 3 — client app
├── CONTRIBUTING.md             # Coding rules
├── setup_project.sh            # Project initialisation script
├── data/                       # Card catalog CSVs
├── shared/                     # Code shared by server and client
├── server/                     # Game server implementation
├── client/                     # Player client implementation
└── tests/                      # Unit tests
```
