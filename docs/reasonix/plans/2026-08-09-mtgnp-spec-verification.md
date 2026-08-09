# MTGNP Spec-Audit & TDD Verification Plan

> **For agentic workers:** implement this plan task-by-task with strict TDD (failing test → minimal fix → green → commit). Each task names the test file, the RED test, the expected failure, and the spec citation. This plan was produced from a four-track parallel audit (protocol, lifecycle, cards, client/rubric) with every CRITICAL claim verified against source before planning.

**Goal:** Bring the MTGNP implementation into spec compliance (magic-the-gathering-multiplayer-specifications.md §5–§12, program-states.md walkthrough, grading-rubric.md) via test-driven fixes, then prove compliance with the full suite plus a live verbose-mode client↔server session.

**Architecture:** Python 3.10+ asyncio TCP client/server; server-authoritative game state; single read_loop per connection feeding a dispatcher that resolves per-player pending futures (no stream race in current code). All fixes land in `server/`, `client/`, `shared/`, with tests in `tests/`.

**Tech Stack:** Python stdlib only (asyncio, json, struct, csv); pytest for tests.

**Baseline (evidence):** `python -m pytest tests/ -q` → **55 passed, 1 failed** (`test_combat.py::TestDeclareAttackers::test_set_attackers`, summoning-sickness rejection — test-model mismatch, the model is spec-correct per spec §3).

---

## Phase 1 — Protocol & crash fixes (CRITICAL)

### Task 1.1: Fix state-based-actions crash — `gs.graveyard` → `gs.graveyards`

**Files:** Modify `server/stack.py:66` (and `:54-76` region); Test: `tests/test_stack.py`

- [ ] **Step 1: RED** — `test_sba_death_moves_instance_to_graveyard`: resolve a spell that deals lethal damage to a creature (or call `check_state_based_actions` directly after setting `damage >= toughness`), assert the creature's **instance id** is in `gs.graveyards[owner]` and removed from `gs.battlefield[owner]`.
- [ ] **Step 2: Verify RED** — `pytest tests/test_stack.py -q` → fails with `AttributeError: 'GameState' object has no attribute 'graveyard'` (stack.py:66).
- [ ] **Step 3: GREEN** — replace `gs.graveyard[player_id].append(base_id)` with `gs.graveyards.setdefault(player_id, []).append(dead_instance_id)`; use the permanent's instance id, not the stripped base id (fixes F26 identity loss). Verify the surrounding `_find_permanent`/owner logic uses the right id.
- [ ] **Step 4: Verify GREEN** — test passes; full suite still green.
- [ ] **Step 5: Commit** — `fix: SBA deaths move instance ids to gs.graveyards (no AttributeError)`

### Task 1.2: Fix Dark Ritual crash — `gs.mana_pools` → per-player mana pool

**Files:** Modify `server/game_state.py:143`, `server/card_effects.py:167-173`, `server/game_lifecycle.py:318,626,759-762`, `server/validators.py` (pool call sites); Test: `tests/test_card_effects.py` (new), `tests/test_game_state.py`

**Root cause (verified):** GameState has ONE shared `mana_pool: ManaPool`; `card_effects._apply_add_mana` reads nonexistent `gs.mana_pools` → AttributeError during Dark Ritual resolution (crashes the session). Also a shared pool lets the NAP spend the AP's floating mana.

- [ ] **Step 1: RED** — `test_dark_ritual_adds_black_mana`: build GameState with `mana_pools` per player, call `_effect_dark_ritual`, assert caster's pool B == 3 and the NAP's pool unchanged. (Also `test_deduct_uses_casters_pool`.)
- [ ] **Step 2: Verify RED** — AttributeError (`mana_pools` missing) or AssertionError.
- [ ] **Step 3: GREEN** — change `GameState.mana_pool` → `mana_pools: dict[str, ManaPool]`; update: `_apply_add_mana` (add to `gs.mana_pools[player_id]`), land-tap mana (lifecycle ~759: add to the **land controller's** pool), `deduct_mana` call at lifecycle:626 (deduct from the caster's pool), untap reset (lifecycle:318 — empty every player's pool, or at least the AP's per MTG), `can_pay` call sites in validators (pass the acting player's pool). Update any `build_visible_state` rendering of the pool.
- [ ] **Step 4: Verify GREEN** — new tests + full suite pass.
- [ ] **Step 5: Commit** — `fix: per-player mana pools; Dark Ritual no longer crashes`

### Task 1.3: Fix STALE_ACTION lockout — ERROR + re-issued PRIORITY_GRANT (same seq)

**Files:** Modify `server/dispatcher.py:104-119`, `server/priority.py:125-138`, `server/connection.py` (add fixed-seq send helper), `server/game_lifecycle.py:988-1000` (send_error echo); Test: `tests/test_dispatcher.py` (new)

**Root cause (verified):** stale PDU → `send_error` → `send_pdu` increments `conn.seq_num`; no grant re-issue; future stays pending → every retry with the original token is now "stale" → lockout until PriorityTimeout (GAME_OVER DISCONNECT). Spec §11.3: reject + re-issue PRIORITY_GRANT with the same seq_num; §10.2.23: ERROR seq echoes the rejected action's seq.

- [ ] **Step 1: RED** — `test_stale_action_reissues_grant_with_same_seq`: register a pending future, dispatch a PDU with seq < token → assert ERROR(STALE_ACTION) emitted, assert a PRIORITY_GRANT with the **original token seq** is sent afterwards, assert the pending future is **not** consumed; then dispatch the correct-echo PDU → future resolves.
- [ ] **Step 2: Verify RED** — currently only ERROR is sent; no grant re-issue; future never resolves.
- [ ] **Step 3: GREEN** — in dispatcher stale branch: capture `token_seq = conn.seq_num`; `send_error` (ERROR seq = rejected action's seq per §10.2.23 via fixed-seq send, no counter consumption); re-send PRIORITY_GRANT with seq=token_seq via the fixed-seq helper; leave future pending. Same treatment in `priority.py`'s own mismatch branch (re-issue with `expected_seq`). Add `send_pdu_explicit(pdu, seq_num)` to `server/connection.py` (sets seq, does NOT increment; still verbose-logs).
- [ ] **Step 4: Verify GREEN** — new tests pass; existing game-flow tests still green.
- [ ] **Step 5: Commit** — `fix: STALE_ACTION re-issues PRIORITY_GRANT with same token; ERROR echoes rejected seq`

### Task 1.4: Watchdog GAME_OVER must go through send_pdu (verbose prerequisite)

**Files:** Modify `server/server.py:96-109`; Test: `tests/test_server_watchdog.py` (new)

**Root cause (verified):** disconnect-timeout GAME_OVER is written with raw `struct.pack` + `writer.write` + `writer.close()` → invisible in verbose mode (automatic-zero prerequisite), wrong seq (no increment), no write lock/drain, and closes the winner's socket (spec §6.6: connections are retained; close only on TCP error/heartbeat timeout).

- [ ] **Step 1: RED** — `test_watchdog_game_over_via_send_pdu`: instantiate GameServer with two fake ServerConnections (one `_closed=True`), run the watchdog sweep with a zero/negative timeout, assert `winner.send_pdu` was called with GAME_OVER(DISCONNECT) and the winner's writer was **not** closed.
- [ ] **Step 2: Verify RED** — current code writes raw to writer and closes it.
- [ ] **Step 3: GREEN** — replace raw write with `await winner_conn.send_pdu(go_pdu)`; drop `writer.close()` (keep connection for next LOBBY). Keep `lifecycle._game_over.set()`.
- [ ] **Step 4: Verify GREEN** — test passes; suite green.
- [ ] **Step 5: Commit** — `fix: watchdog GAME_OVER via send_pdu (verbose-logged, seq-correct, socket retained)`

### Task 1.5: NOT_YOUR_PRIORITY for out-of-window actions; stop silent drops

**Files:** Modify `server/dispatcher.py` (priority-wait section); Test: `tests/test_dispatcher.py`

**Root cause (verified):** a priority-bearing action PDU arriving when the sender has no pending future falls through to handler stubs (no-ops) — silently dropped with no ERROR. Spec §11: every invalid/illegal PDU MUST produce an ERROR; NOT_YOUR_PRIORITY exists for this case.

- [ ] **Step 1: RED** — `test_action_without_priority_gets_error`: dispatch CAST_SPELL (with valid seq) for a player with no pending future → assert ERROR(NOT_YOUR_PRIORITY) and game state unchanged.
- [ ] **Step 2: Verify RED** — no ERROR today (silent drop).
- [ ] **Step 3: GREEN** — in the dispatcher, after the CONCEDE/PING/PONG special cases: if the PDU type is in the priority-bearing set (CAST_SPELL, ACTIVATE_ABILITY, PRIORITY_PASS, DECLARE_ATTACKERS, DECLARE_BLOCKERS, ASSIGN_DAMAGE_ORDER, PLAY_LAND, DISCARD, TRIGGER_ORDER_RESPONSE, TRIGGER_CHOICE_RESPONSE) and `pid not in _pending_pdu` → `send_error(NOT_YOUR_PRIORITY)`. Keep PLAYER_READY/MULLIGAN_CHOICE/CONCEDE/PING on their handler paths.
- [ ] **Step 4: Verify GREEN** — test passes; no regressions in game-flow tests (they send actions only during windows).
- [ ] **Step 5: Commit** — `fix: out-of-window actions answered with NOT_YOUR_PRIORITY`

---

## Phase 2 — Combat & turn-engine compliance

### Task 2.1: Trample must not exist in MTGNP 1.0 (§9.7)

**Files:** Modify `server/combat.py:220-226`; Test: `tests/test_combat.py`

**Spec (§9.7, verbatim):** "MTGNP 1.0 does not implement trample. A blocked attacker deals its full combat damage to its blocker(s) only, never to the defending player."

- [ ] **Step 1: RED** — rewrite `test_trample_overflow` → `test_blocked_attacker_deals_no_overflow_to_player`: trampler (4/4) blocked by wall (0/2): assert NO damage event targeting the player; assert wall takes the full 4 (blocker takes full combat damage when it's the only blocker). Also `test_trample_keyword_is_inert`.
- [ ] **Step 2: Verify RED** — current code emits 2 damage to the player (overflow).
- [ ] **Step 3: GREEN** — delete the `remaining > 0 and self._has_trample` branch in `_compute_damage`; leftover damage is simply not assigned.
- [ ] **Step 4: Verify GREEN** — updated tests pass; suite green.
- [ ] **Step 5: Commit** — `fix: remove trample overflow (spec §9.7: MTGNP 1.0 has no trample)`

### Task 2.2: Empty attack skips to END_OF_COMBAT (program-states step 18)

**Files:** Modify `server/game_lifecycle.py` (combat sequence, ~349-417); Test: `tests/test_game_flow.py`

**Spec (program-states.md step 18):** "With no attackers declared, the server skips Declare Blockers, Assign Damage Order, and Combat Damage, advancing directly to End of Combat."

- [ ] **Step 1: RED** — `test_empty_attack_skips_to_end_of_combat`: drive the combat sequence with DECLARE_ATTACKERS `[]` → assert the next broadcast PHASE_TRANSITION is to END_OF_COMBAT (no DECLARE_BLOCKERS window, no COMBAT_DAMAGE_RESULT).
- [ ] **Step 2: Verify RED** — current code proceeds through the remaining combat steps.
- [ ] **Step 3: GREEN** — after attackers are declared empty (or all rejected), jump to END_OF_COMBAT.
- [ ] **Step 4: Verify GREEN** — pass.
- [ ] **Step 5: Commit** — `fix: no-attacker combat skips to END_OF_COMBAT per program-states step 18`

### Task 2.3: First-strike step optional; double strike deals damage in both steps (§9.6/§9.7)

**Files:** Modify `server/combat.py` (`_has_first_strike`, `_compute_damage`), `server/game_lifecycle.py` (FS step gate); Test: `tests/test_combat.py`

- [ ] **Step 1: RED** — `test_double_strike_deals_damage_in_both_steps`: double-strike attacker vs blocker → FS step produces damage, normal step also produces damage. `test_fs_step_skipped_without_fs`: no first/double strike creatures → no FIRST_STRIKE_DAMAGE transition.
- [ ] **Step 2: Verify RED** — double strike currently deals only in the normal step; FS step runs unconditionally.
- [ ] **Step 3: GREEN** — helper `_deals_first_strike(perm)` = first_strike OR double_strike; FS step: participants = `_deals_first_strike`; normal step: participants = NOT `_deals_first_strike` OR double_strike (i.e., pure-FS creatures are excluded; double-strike creatures participate in both). Gate the FS step in lifecycle on `any(_deals_first_strike(a) for attackers/blockers)`.
- [ ] **Step 4: Verify GREEN** — pass; suite green.
- [ ] **Step 5: Commit** — `fix: double strike both steps; FS step optional`

### Task 2.4: Illegal attackers/blockers → ERROR ILLEGAL_ACTION (not silent skip); defender enforced

**Files:** Modify `server/combat.py:66-102` (return rejections), `server/game_lifecycle.py` (attacker/blocker windows → error on rejection), `server/validators.py:268-318` (defender); Test: `tests/test_combat.py`, `tests/test_validators.py` (new)

**Spec:** §3 summoning sickness "MUST NOT be declared as an attacker… server enforces automatically"; §11: attacking with a tapped creature is the canonical ILLEGAL_ACTION example; every illegal PDU → ERROR.

- [ ] **Step 1: RED** — fix `test_set_attackers` setup: `goblin = Permanent(..., summoning_sick=False)` (code is spec-correct; test was wrong). Add `test_tapped_attacker_rejected`, `test_summoning_sick_attacker_rejected` asserting `set_attackers` reports the rejection reason; add `test_wall_of_stone_cannot_attack` (defender keyword blocks attacking).
- [ ] **Step 2: Verify RED** — `test_set_attackers` currently fails with the summoning-sickness rejection; new rejection tests fail (no error surfaced; defender not enforced).
- [ ] **Step 3: GREEN** — `set_attackers` returns rejection reasons (e.g. list of `{creature_id, reason}`); lifecycle attacker window: if any attacker was rejected and none accepted → send ERROR ILLEGAL_ACTION (and still re-grant per §11.3); defender check in the attack validator (`defender` keyword → cannot attack).
- [ ] **Step 4: Verify GREEN** — all combat/validator tests pass; suite green.
- [ ] **Step 5: Commit** — `fix: attacker rejections surfaced as ILLEGAL_ACTION; defender keyword enforced`

### Task 2.5: Cleanup step per §7.8 (discard loop, no priority window, damage reset)

**Files:** Modify `server/game_lifecycle.py` (cleanup handling), `server/turn_engine.py:146` area; Test: `tests/test_turn_engine.py` (new)

**Spec (§7.8):** cleanup: if AP hand > 7 → send GAME_STATE_UPDATE, await DISCARD (echo that GSU's seq), ILLEGAL_ACTION on invalid card_ids, repeat until ≤ 7; then remove damage, clear until-end-of-turn, GSU to both, no priority, increment turn, switch AP, begin Untap. (Current code opens a priority window for the discard — deviation.)

- [ ] **Step 1: RED** — `test_cleanup_discard_no_priority_window`: AP with 8 cards at cleanup → assert GAME_STATE_UPDATE (not PRIORITY_GRANT) precedes the DISCARD wait; assert DISCARD of a card not in hand → ERROR ILLEGAL_ACTION; after valid discard to ≤7 → damage cleared + GSU + next turn UNTAP transition.
- [ ] **Step 2: Verify RED** — current behavior deviates (priority window used).
- [ ] **Step 3: GREEN** — implement the §7.8 discard loop (wait_for_pdu with expected-seq check against the GSU, exactly like the mulligan flow), then the damage/effect reset + turn advance.
- [ ] **Step 4: Verify GREEN** — pass.
- [ ] **Step 5: Commit** — `fix: cleanup discard loop per §7.8 (no priority window, ILLEGAL_ACTION on bad discard)`

### Task 2.6: Caster retains priority after casting (§8.1.3)

**Files:** Modify `server/game_lifecycle.py` (`_run_priority_loop`); Test: `tests/test_game_flow.py`

**Spec (§8.1.3):** "When a player casts a spell or activates an ability, the item is placed on the Stack and **that player retains priority**."

- [ ] **Step 1: RED** — `test_caster_retains_priority_after_cast`: during a priority window, respond with CAST_SPELL → assert the next PRIORITY_GRANT goes to the **caster** (not the AP). (Audit F14: current loop re-grants AP first after any action.)
- [ ] **Step 2: Verify RED** — next grant goes to the AP.
- [ ] **Step 3: GREEN** — after a successful cast/activation, grant priority to the acting player first.
- [ ] **Step 4: Verify GREEN** — pass; suite green.
- [ ] **Step 5: Commit** — `fix: caster retains priority after casting (§8.1.3)`

---

## Phase 3 — Card effects fidelity

### Task 3.1: Gray Merchant & Gravedigger enter the battlefield

**Files:** Modify `server/card_effects.py:585-623`; Test: `tests/test_card_effects.py`

- [ ] **Step 1: RED** — `test_gray_merchant_enters_and_drains`: resolve Gray Merchant → assert a permanent with the instance id is on the battlefield AND life-loss/drain applied. `test_gravedigger_enters_and_returns`.
- [ ] **Step 2: Verify RED** — creature vanishes after resolution (no spawn).
- [ ] **Step 3: GREEN** — add `_apply_spawn_permanent(gs, controller, card_def_id, instance_id)` calls in both handlers (before/after their ETB effects).
- [ ] **Step 4: Verify GREEN** — pass.
- [ ] **Step 5: Commit** — `fix: Gray Merchant and Gravedigger spawn their permanent`

### Task 3.2: Mind Rot actually discards; Swords to Plowshares life = power to creature's controller

**Files:** Modify `server/card_effects.py:570-582` + lifecycle FORCE_DISCARD consumer (or effect-side), `server/card_effects.py` (Plowshares); Test: `tests/test_card_effects.py`

- [ ] **Step 1: RED** — `test_mind_rot_discards_two_from_target_hand`; `test_swords_to_plowshares_gains_power_to_controller` (creature's controller gains life equal to its power).
- [ ] **Step 2: Verify RED** — FORCE_DISCARD event never consumed (no-op); Plowshares behavior per current code (verify what it does first).
- [ ] **Step 3: GREEN** — Mind Rot: implement in-effect (choose N random cards from target's hand, move to graveyard, broadcast change events). Swords: fix life-gain target/amount.
- [ ] **Step 4: Verify GREEN** — pass.
- [ ] **Step 5: Commit** — `fix: Mind Rot discards; Swords to Plowshares life gain correct`

### Task 3.3: Target-type enforcement (Naturalize / Terror / Doom Blade / Negate) + Raise Dead ids

**Files:** Modify `server/validators.py` (target validation), `server/card_effects.py:122,157,195` (zone moves keep instance ids); Test: `tests/test_validators.py`, `tests/test_card_effects.py`

- [ ] **Step 1: RED** — `test_naturalize_rejects_non_enchantment` (target must be artifact/enchantment), `test_terror_rejects_black_creature`, `test_doom_blade_rejects_black_creature`, `test_negate_rejects_noncreature_spell` (target must be a creature spell on the stack), `test_raise_dead_targets_graveyard_instance` (instance ids, not base names).
- [ ] **Step 2: Verify RED** — current validation is permissive/absent.
- [ ] **Step 3: GREEN** — extend the target-validator to interpret effect text (existing pattern at validators.py:125+): artifact/enchantment/creature-type and colour checks against `CardDef`; zone moves use the instance id.
- [ ] **Step 4: Verify GREEN** — pass.
- [ ] **Step 5: Commit** — `fix: target-type and colour validation for removal spells; zone moves keep instance ids`

### Task 3.4: Keyword abilities reach permanents (vigilance/first strike/trample/defender functional)

**Files:** Modify `server/card_effects.py:216-247` (`_apply_spawn_permanent`) + lifecycle PLAY_LAND path; Test: `tests/test_card_effects.py`, `tests/test_combat.py`

**Root cause (verified):** `Permanent.abilities` is never populated → `combat._has_first_strike/_has_trample/_has_vigilance` always return False in live play (Serra Angel taps; White Knight has no first strike).

- [ ] **Step 1: RED** — `test_spawned_creature_carries_keyword_abilities` (serra_angel → vigilance in `.abilities`); `test_vigilant_attacker_not_tapped` (combat-level).
- [ ] **Step 2: Verify RED** — abilities list empty.
- [ ] **Step 3: GREEN** — populate `Permanent.abilities` from `CardDef.abilities` at spawn and land-play.
- [ ] **Step 4: Verify GREEN** — pass; suite green.
- [ ] **Step 5: Commit** — `fix: permanents carry keyword abilities; vigilance/first strike/trample functional`

### Task 3.5: Sol Ring produces {C}{C}; explicit no-op guard for unimplemented tap abilities

**Files:** Modify `server/card_effects.py`/`server/validators.py` (Sol Ring), lifecycle ACTIVATE_ABILITY path; Test: `tests/test_card_effects.py`

- [ ] **Step 1: RED** — `test_sol_ring_produces_two_colourless`.
- [ ] **Step 2: Verify RED** — current behavior (verify first; likely wrong amount or no-op).
- [ ] **Step 3: GREEN** — fix Sol Ring effect; for tap-abilities not implemented (Prodigal Sorcerer, Royal Assassin, Merfolk Looter, Mother of Runes, Millstone, Rod of Ruin): ACTIVATE_ABILITY on them → explicit ERROR (code ILLEGAL_ACTION with message "ability not implemented") instead of silent no-op.
- [ ] **Step 4: Verify GREEN** — pass.
- [ ] **Step 5: Commit** — `fix: Sol Ring mana; unimplemented tap abilities answer explicitly`

---

## Phase 4 — Lifecycle robustness

### Task 4.1: Disconnect handling — no hang in LOBBY/MULLIGAN, pending futures cancelled, `_mulligan_expected_seq` cleared

**Files:** Modify `server/game_lifecycle.py` (run/_run_mulligan/_run_game_over, ~150-184, 440-470, 830-870), `server/connection.py` (read_loop EOF → cancel pending future); Test: `tests/test_game_flow.py`

**Root cause (verified):** disconnect during LOBBY/MULLIGAN leaves awaits pending forever (no watchdog); priority waits stall up to 1 h (watchdog `_game_over` never cancels pending futures); `_mulligan_expected_seq` survives GAME_OVER → next game's mulligan rejects valid echoes.

- [ ] **Step 1: RED** — `test_disconnect_during_mulligan_ends_game`; `test_pending_future_cancelled_on_disconnect` (close a fake conn mid-priority → the priority wait raises promptly, GAME_OVER DISCONNECT broadcast); `test_mulligan_seq_reset_between_games`.
- [ ] **Step 2: Verify RED** — hangs/stalls or stale seq rejection.
- [ ] **Step 3: GREEN** — on `_closed` (read_loop exit), resolve/cancel that player's pending future (ConnectionLost) and set `_game_over`; cancel pending futures in `_end_game`; clear `_mulligan_expected_seq` (and `_mulligan_kept`, `_deck_lists`) in `_run_game_over`; make the LOBBY/MULLIGAN waits observe `_game_over` (e.g., shield the awaits with the game-over event).
- [ ] **Step 4: Verify GREEN** — pass; suite green.
- [ ] **Step 5: Commit** — `fix: disconnect escapes LOBBY/MULLIGAN/priority waits; per-game state reset`

### Task 4.2: Priority timeout 60 s default; PRIORITY_GRANT advertises it

**Files:** Modify `server/config.py:35`; Test: `tests/test_config.py` (new)

- [ ] **Step 1: RED** — `test_default_priority_timeout_is_60s` (spec examples use `time_limit_ms: 60000`).
- [ ] **Step 2: Verify RED** — default is 3 600 000 ms.
- [ ] **Step 3: GREEN** — `time_limit_ms = 60_000`.
- [ ] **Step 4: Verify GREEN** — pass.
- [ ] **Step 5: Commit** — `fix: 60 s priority timeout default`

### Task 4.3: Library draw consistency — top is index 0 everywhere

**Files:** Modify `server/card_effects.py:136` (`_apply_draw`); Test: `tests/test_card_effects.py`

- [ ] **Step 1: RED** — `test_spell_draw_pulls_from_top` (put known cards in library, draw via effect → top card (index 0) is drawn; the draw-step path pops index 0 per turn_engine.py:146).
- [ ] **Step 2: Verify RED** — `_apply_draw` pops the end.
- [ ] **Step 3: GREEN** — `pop(0)`.
- [ ] **Step 4: Verify GREEN** — pass.
- [ ] **Step 5: Commit** — `fix: spell draws pull from library top (index 0)`

### Task 4.4: GAME_OVER only for the 4 spec reasons; ILLEGAL_DECK is an ERROR

**Files:** Modify `server/game_lifecycle.py` (setup path); Test: `tests/test_game_flow.py`

- [ ] **Step 1: RED** — `test_illegal_deck_sends_error_not_game_over` (both players ready, one illegal deck → ERROR ILLEGAL_DECK to that player; game does not end).
- [ ] **Step 2: Verify RED** — current code broadcasts GAME_OVER with reason ILLEGAL_DECK (verify first; audit F18).
- [ ] **Step 3: GREEN** — route illegal-deck to `send_error(ILLEGAL_DECK)`; keep waiting for a valid PLAYER_READY.
- [ ] **Step 4: Verify GREEN** — pass.
- [ ] **Step 5: Commit** — `fix: ILLEGAL_DECK is an ERROR, not a GAME_OVER reason`

### Task 4.5: Register pending future before sending PRIORITY_GRANT (window race)

**Files:** Modify `server/priority.py:78-95`; Test: `tests/test_priority.py` (new)

- [ ] **Step 1: RED** — `test_future_registered_before_grant_sent`: fake read_pdu that asserts the future is registered when it first runs; order probe.
- [ ] **Step 2: Verify RED** — grant is sent before `read_pdu` is invoked.
- [ ] **Step 3: GREEN** — create the read task (`asyncio.create_task(read_pdu(...))`) before sending the grant; await it after.
- [ ] **Step 4: Verify GREEN** — pass.
- [ ] **Step 5: Commit** — `fix: register priority-wait future before sending the grant`

---

## Phase 5 — Client & verbose completeness

### Task 5.1: Verbose-mode regression tests (both sides)

**Files:** Test: `tests/test_verbose.py` (new); fix any gaps found

- [ ] **Step 1: RED** — `test_client_send_prints_verbose` (ClientConnection with verbose=True → send_pdu prints `[C→S` + type); `test_server_send_prints_verbose`; `test_watchdog_game_over_is_verbose` (via Task 1.4 fake); `test_verbose_off_prints_nothing`.
- [ ] **Step 2: Verify RED** — no verbose tests exist.
- [ ] **Step 3: GREEN** — implement tests against existing hooks; fix any call site that bypasses them (Task 1.4 covers the known one).
- [ ] **Step 4: Verify GREEN** — pass.
- [ ] **Step 5: Commit** — `test: verbose-mode PDU logging coverage (client+server)`

### Task 5.2: Client heartbeat restart after GAME_OVER; prompt shutdown on PONG timeout/Ctrl+C

**Files:** Modify `client/client.py:125-130,178-181`, `client/heartbeat.py:48`, `client/input_handler.py:71`; Test: `tests/test_client.py` (new, asyncio)

- [ ] **Step 1: RED** — `test_heartbeat_restarts_after_game_over`; `test_pong_timeout_exits` (fake conn that stops responding → client exits within timeout); `test_ctrl_c_no_hang` (client with stdin mocked → cancel → process exits promptly).
- [ ] **Step 2: Verify RED** — heartbeat task is cancelled after GAME_OVER and not restarted; stdin thread join hangs.
- [ ] **Step 3: GREEN** — restart heartbeat loop after GAME_OVER; on PONG timeout raise → teardown; add SIGINT handler or `loop.add_signal_handler` + stdin-thread gate so shutdown is prompt.
- [ ] **Step 4: Verify GREEN** — pass.
- [ ] **Step 5: Commit** — `fix: client heartbeat restart + prompt shutdown`

### Task 5.3: Client auto-PLAYER_READY seq bump (cosmetic)

**Files:** Modify `client/client.py:108-113`; Test: `tests/test_client.py`

- [ ] **Step 1: RED** — `test_auto_ready_increments_seq` (client seq counter bumps after auto PLAYER_READY so a manual ready isn't also seq 1).
- [ ] **Step 2: Verify GREEN** — pass.
- [ ] **Step 3: Commit** — `fix: client seq bump on auto PLAYER_READY`

---

## Phase 6 — Triggered abilities (§8.6) — bonus feature

### Task 6.1: Wire trigger registry into game events + TRIGGER_ORDER/TRIGGER_CHOICE flow

**Files:** Modify `server/game_lifecycle.py` (event hooks: ETB, attack declaration, noncreature cast), `server/card_effects.py` (registry + trigger effects), `server/stack.py` (trigger placement), `client/dispatcher.py:171-192` + `client/input_handler.py` (trigger prompts); Test: `tests/test_triggers.py` (new)

**Spec (§8.6):** triggers fire on events, go on the stack as TRIGGER_ABILITY, STACK_PUSH broadcast, target-requiring triggers use TRIGGER_CHOICE (no legal target → discarded), resolve like spells, then priority to AP. Cards: Goblin Guide (attack), Gray Merchant (ETB), Gravedigger (ETB), Goblin Bushwhacker (surge), Monastery Swiftspear (noncreature cast), Phantasmal Bear (illusion sac — registry gap).

- [ ] **Step 1: RED** — `test_goblin_guide_attack_trigger_fires` (attack declaration → TRIGGER_ORDER/STACK_PUSH with TRIGGER_ABILITY → resolution reveals a card); `test_etb_trigger_on_stack` (Gray Merchant cast → trigger pushed, then resolves); `test_trigger_no_legal_target_discarded`; `test_swiftspear_noncreature_cast_trigger`.
- [ ] **Step 2: Verify RED** — triggers never fire (registry not wired).
- [ ] **Step 3: GREEN** — wire `check_triggers` into the event points; implement trigger effects; TRIGGER_ORDER (ordering) minimal: single-trigger events skip the ORDER round-trip (multiple simultaneous triggers use TRIGGER_ORDER); TRIGGER_CHOICE for target-requiring triggers; client prompt for order/choice mapped to the response PDUs.
- [ ] **Step 4: Verify GREEN** — pass; full suite green.
- [ ] **Step 5: Commit** — `feat: triggered abilities per §8.6 (Goblin Guide, ETB triggers, Swiftspear)`

---

## Phase 7 — Final verification (verification-before-completion)

- [ ] **Step 1:** Full suite: `python -m pytest tests/ -v` → all pass (record count).
- [ ] **Step 2:** Live verbose session: start `python -m server.main --verbose` + two `python -m client.main --verbose` sessions (or a scripted two-socket driver against the real binaries) → play a scripted game (ready → mulligan keep → land → cast Goblin Guide → attack → cleanup) → capture logs; assert `[C→S`/`[S→C` verbose lines for every PDU incl. watchdog GAME_OVER; assert game reaches GAME_OVER and returns to LOBBY on the same connections.
- [ ] **Step 3:** Rubric checklist: map every rubric criterion (lines 122-139 of grading-rubric.md) to passing test/evidence; document any known limitation.
- [ ] **Step 4:** Final summary with actual logs (test output + verbose session excerpt) in the reply.
