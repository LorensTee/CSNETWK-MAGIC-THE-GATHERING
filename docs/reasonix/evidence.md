# MTGNP Verification Evidence Report

> Final verification evidence for the MTGNP implementation (CSNETWK T3 AY 2025–2026).
> Companion to `docs/reasonix/plans/2026-08-09-mtgnp-spec-verification.md`.
> Date: 2026-08-09 · Toolchain: Python 3.14, pytest, stdlib-only runtime.

---

## 1. Verification run summary

| Check | Command | Result |
|---|---|---|
| Unit test suite | `python -m pytest tests/ -q --tb=short` | **149 passed** (baseline at plan time: 55 passed / 1 failed) |
| Live end-to-end (3 scenarios, verbose) | `timeout 300 python tools/e2e_verbose_check.py` | **EXIT 0** — see §2 |
| Verbose-mode rubric prerequisite | same e2e, log analysis | **PASS** — 2,975 log lines; 135 `[S→C` and 26 `[C→S` labelled PDU lines |

## 2. Live end-to-end scenarios (tools/e2e_verbose_check.py)

Two real protocol clients (framed TCP + JSON, seq echo, pump/queue readers) play
against the real server in verbose mode, then the log is checked for the rubric
prerequisite markers.

1. **Game 1**: PLAYER_READY (36-card deck) → LOBBY/GAME_SETUP/MULLIGAN GSUs →
   both keep (echo seq) → turn 1 (PT UNTAP/UPKEEP, GSUs) → UPKEEP+DRAW priority
   windows (grant → pass both players) → PRECOMBAT_MAIN grant → PLAY_LAND
   (rejected with ERROR when attempted in the wrong phase; accepted in the main
   phase) → caster retains priority (§8.1.3) → pass → opponent CONCEDE →
   GAME_OVER `winner=… loser=… reason=CONCEDE` broadcast to both.
2. **Game 2**: re-READY on the **same connections** → full game again → GAME_OVER
   (verifies LOBBY return + session restart on the same TCP connections).
3. **Disconnect**: game 3 starts, one client is killed abruptly mid-priority →
   server watchdog (10 s) broadcasts **GAME_OVER reason=DISCONNECT** to the
   survivor via `_end_game` (seq-numbered `send_pdu`, verbose-visible); survivor
   socket is retained for the next LOBBY (RFC §6.6).

## 3. Rubric checklist (grading-rubric.md)

| # | Criterion | Status | Evidence |
|---|---|---|---|
| — | **PREREQUISITE: Verbose mode** (client+server, toggleable, all PDUs sent/received printed, clearly labelled) | ✅ | `--verbose` flag on `server/main.py` + `client/main.py`; `shared/verbose.py` (`format_pdu_sent`/`format_pdu_received`, `[S→C …]`/`[C→S …]` labels); e2e log check PASS (135 `[S→C`, 26 `[C→S`) |
| 1 | TCP server on port 4444, accept exactly 2, refuse extras, disconnect/reconnect within timeout | ✅ | `server/server.py` (`_on_client_connected` refuses extra connections, line 185; watchdog sweep with `disconnect_timeout_s`; `[RECONNECT]` path); `tests/test_server_watchdog.py` |
| 2 | Message framing: 4-byte BE length prefix + UTF-8 JSON, exact byte count, ≤65,535 bytes | ✅ | `shared/framing.py` (`encode_frame`/`decode_frame`); `tests/test_framing.py` |
| 3 | PDU structure & seq_num; stale rejection with STALE_ACTION | ✅ | `shared/pdus.py` (`parse_and_validate`, required fields); stale path in `server/dispatcher.py` + `server/priority.py` (re-issues same-token grant, RFC §11.3); `tests/test_pdus.py`, `tests/test_dispatcher.py` |
| 4 | LOBBY & PLAYER_READY: non-empty id, unique ids (DUPLICATE_ID), deck 1–50 from fixed set (ILLEGAL_DECK), re-ready | ✅ | `handle_player_ready` (`server/game_lifecycle.py`); `tests/test_illegal_deck.py`, `tests/test_game_flow.py` |
| 5 | GAME_SETUP & MULLIGAN: life 20, shuffle, draw 7, coin flip, London mulligan (redraw + bottom N) | ✅ | `_run_setup`/`_run_mulligan` (`server/game_lifecycle.py`); keep-wait fixed to require **all** players (RFC §6.2); `tests/test_game_flow.py` |
| 6 | IN_GAME phase & step transitions (14 phases), phase-specific rules (land only in main, etc.) | ✅ | `server/turn_engine.py` (IN_GAME_PHASES, skip logic); e2e observed land rejection outside the main phase; `tests/test_turn_engine.py` |
| 7 | GAME_OVER & session restart on same connections | ✅ | `_end_game` (broadcast → ready-state reset → LOBBY); win/loss (life ≤ 0, draw loss, CONCEDE, DISCONNECT); e2e games 1–2 reuse the same sockets; `tests/test_lifecycle_disconnect.py` |
| 8 | Authoritative Game State, personalised GSUs, hidden info (hands hidden) | ✅ | `build_visible_state` (`server/game_state.py` — opponent hand shown only as count); `tests/test_game_state.py` |
| 9 | Priority & LIFO stack; STACK_PUSH/STACK_RESOLVE; ≥5 card effects | ✅ | `server/priority.py`, `server/stack.py`; effect handlers incl. Lightning Bolt, Counterspell, Giant Growth, Dark Ritual, Doom Blade, Swords to Plowshares, Mind Rot, Naturalize, Rampant Growth, Gray Merchant, + triggers (RFC §8.6: Goblin Guide, Monastery Swiftspear); `tests/test_priority.py`, `tests/test_stack.py`, `tests/test_card_effects.py`, `tests/test_triggers.py` |
| 10 | Combat: attackers/blockers/damage order/first-strike/combat damage; summoning sickness; COMBAT_DAMAGE_RESULT | ✅ | `server/combat.py`; no-attacker skip to END_OF_COMBAT; cleanup per §7.8; e2e observed COMBAT_DAMAGE_RESULT broadcast; `tests/test_combat.py` |
| 11 | Client sends all required PDU types; renders GAME_STATE_UPDATE authoritatively | ✅ | `client/input_handler.py`, `client/dispatcher.py`, `client/renderer.py`; `tests/test_client.py` |
| 12 | PING/PONG heartbeat (30 s / 10 s) | ✅ | `client/heartbeat.py` (restart-safe after GAME_OVER, stdin via add_reader); server `handle_ping` → PONG; `tests/test_client.py` |
| 13 | ERROR PDU handling (codes per RFC; client handles gracefully) | ✅ | `send_error` (echoes rejected seq, no counter consumption); client `_handle_error`; `tests/test_dispatcher.py` |
| 14 | Readability & comments | ✅ | CONTRIBUTING.md, docstrings throughout, named modules per architecture docs |
| B1 | **Bonus — full card effects (58 cards)** | ⚠️ Partial | 5+ priority effects + ~25 more implemented; several card effects remain generic no-ops (see §4) |
| B2 | **Bonus — creative extras** | ⚠️ Partial | Triggered-ability registry with stack pushes (RFC §8.6) implemented; no spectator/graphical UI |

## 4. Known limitations / deviations (for README "known limitations" section)

- Not all 58 cards have bespoke effects; unimplemented abilities resolve as
  harmless no-ops (the ≥5-effect criterion and trigger registry are fully met).
- TRIGGER_ORDER/TRIGGER_CHOICE round-trips exist as PDUs, but no card currently
  needs multi-trigger ordering; simultaneous triggers use server order.
- Floating-mana emptying happens at each phase boundary (per MTG), matching
  program-states.md.
- Reconnect: the watchdog + RECONNECT path retain the winner's socket and accept
  a reconnecting client for the next LOBBY; mid-game reconnection resumes only
  to the next game (not mid-game state) — per RFC §6.6 intent.

## 5. Key fixes delivered this session (git log)

- `648a5b6` style: imports + traceback logging for fire-and-forget task failures
- `581d5cb` mulligan guard at the real race point + best-effort per-connection broadcast
- `40ff706` atomic `_end_game` claim + mulligan guard placement
- `768dbcb` review findings: `_end_game` robustness, watchdog both-dead reset,
  mulligan race guards (three review passes; final verdict "ship as-is")
- `6ecee24` game-flow concurrency overhaul — mulligan waits for all keeps,
  graceful game-over unwind (GameOverInterrupt), read loops once per lifecycle,
  ready-state reset in `_end_game` (broadcast-then-reset), temp-id clearing.
- `4324f46` multi-game lifecycle — zones populated at setup (reset can no longer
  wipe the next game's decks); watchdog routes GAME_OVER through `_end_game`.
- Earlier: SBA graveyard fix, per-player mana pools (Dark Ritual), STALE_ACTION
  re-issue, NOT_YOUR_PRIORITY, combat fixes (trample, no-attacker skip,
  first/double strike, cleanup, caster-priority), card-effect fixes, 60 s
  priority timeout, library draw from top, ILLEGAL_DECK as ERROR, priority
  future registration order, verbose-mode tests, client heartbeat/shutdown,
  auto-ready seq bump, triggered abilities.
