"""
tests/test_dispatcher.py — Unit tests for the PDU dispatcher (Module 02).

Tests STALE_ACTION rejection + PRIORITY_GRANT re-issue (RFC §11.3),
the priority-wait future path, and out-of-window NOT_YOUR_PRIORITY
behaviour (RFC §11).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from server.dispatcher import dispatch
from shared.pdus import create_error, create_priority_grant


class FakeConn:
    """Minimal ServerConnection stand-in mirroring real seq semantics."""

    def __init__(self, player_id: str | None, seq_num: int = 0) -> None:
        self.player_id = player_id
        self.seq_num = seq_num
        self.sent: list[dict[str, Any]] = []
        self.verbose = False

    async def send_pdu(self, pdu: dict[str, Any]) -> None:
        # Mirrors ServerConnection.send_pdu: increment first, then assign.
        self.seq_num += 1
        pdu["seq_num"] = self.seq_num
        self.sent.append(dict(pdu))

    async def send_pdu_explicit(self, pdu: dict[str, Any], seq_num: int) -> None:
        # Mirrors the fixed-seq helper: no counter consumption.
        pdu["seq_num"] = seq_num
        self.sent.append(dict(pdu))


class FakeLifecycle:
    """Lightweight GameLifecycle stand-in exposing the dispatcher's needs."""

    def __init__(self) -> None:
        self._pending_pdu: dict[str, asyncio.Future] = {}
        self.gs = SimpleNamespace()
        self.config = SimpleNamespace(time_limit_ms=60000)
        self.errors: list[tuple[str, str, dict]] = []
        self.handled: list[tuple[str, dict]] = []

    async def send_error(self, conn, code, message, rejected_action):
        self.errors.append((code, message, rejected_action))
        pdu = create_error(
            seq_num=0, code=code, message=message,
            rejected_action=rejected_action,
        )
        seq = rejected_action.get("seq_num") if isinstance(rejected_action, dict) else None
        if seq is not None:
            await conn.send_pdu_explicit(pdu, seq)
        else:
            await conn.send_pdu(pdu)

    def _opponent(self, pid: str) -> str | None:
        return "p2" if pid == "p1" else "p1"

    # Handler stubs (dispatcher routes unknown handlers here).
    async def handle_cast_spell(self, conn, pdu):
        self.handled.append(("CAST_SPELL", pdu))


class TestStaleAction:

    def test_stale_action_reissues_grant_with_same_seq(self):
        async def scenario():
            lc = FakeLifecycle()
            # Last sent = PRIORITY_GRANT seq 16; the granted token is
            # recorded on the connection (the dispatcher compares actions
            # against the token, not the moving seq_num counter).
            conn = FakeConn("p1", seq_num=16)
            conn.grant_token = 16
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            lc._pending_pdu["p1"] = future

            stale = {"type": "CAST_SPELL", "seq_num": 14,
                     "card_id": "lightning_bolt_001", "targets": [],
                     "mana_payment": {}}
            await dispatch(lc, conn, stale)

            # ERROR(STALE_ACTION) sent, echoing the rejected action's seq (§10.2.23).
            assert lc.errors and lc.errors[-1][0] == "STALE_ACTION"
            err = conn.sent[-2]
            assert err["type"] == "ERROR" and err["seq_num"] == 14

            # PRIORITY_GRANT re-issued with the SAME token seq (§11.3).
            grant = conn.sent[-1]
            assert grant["type"] == "PRIORITY_GRANT"
            assert grant["seq_num"] == 16
            assert grant["player_id"] == "p1"

            # The pending future is NOT consumed — a correct echo resolves it.
            assert not future.done()
            good = {"type": "CAST_SPELL", "seq_num": 16,
                    "card_id": "lightning_bolt_001", "targets": [],
                    "mana_payment": {}}
            await dispatch(lc, conn, good)
            assert future.done()
            assert future.result()["seq_num"] == 16

        asyncio.run(scenario())

    def test_retry_with_grant_token_passes_after_interleaved_broadcast(self):
        """RFC §11.3 retry: the granted token is stable across
        interleaved server broadcasts — a retry echoing the token must
        resolve the pending future even though conn.seq_num has advanced
        (previously the < conn.seq_num pre-filter ping-ponged the retry
        into a PriorityTimeout lockout)."""
        async def scenario():
            lc = FakeLifecycle()
            conn = FakeConn("p1", seq_num=18)  # interleaved broadcasts
            conn.grant_token = 16              # ...but the token is 16
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            lc._pending_pdu["p1"] = future

            retry = {"type": "PRIORITY_PASS", "seq_num": 16,
                     "player_id": "p1"}
            await dispatch(lc, conn, retry)

            assert lc.errors == []   # not rejected as stale
            assert future.done()     # priority wait resolved
            assert future.result() is retry

        asyncio.run(scenario())

    def test_valid_echo_resolves_future(self):
        async def scenario():
            lc = FakeLifecycle()
            conn = FakeConn("p1", seq_num=16)
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            lc._pending_pdu["p1"] = future

            good = {"type": "PRIORITY_PASS", "seq_num": 16}
            await dispatch(lc, conn, good)

            assert future.done()
            assert future.result()["type"] == "PRIORITY_PASS"
            assert conn.sent == []  # No error or grant sent on the happy path.

        asyncio.run(scenario())


class TestNotYourPriority:

    def test_action_without_pending_window_gets_error(self):
        async def scenario():
            lc = FakeLifecycle()
            conn = FakeConn("p1", seq_num=10)

            action = {"type": "CAST_SPELL", "seq_num": 10,
                      "card_id": "lightning_bolt_001", "targets": [],
                      "mana_payment": {}}
            await dispatch(lc, conn, action)

            # Out-of-window action → NOT_YOUR_PRIORITY, game state untouched.
            assert lc.errors and lc.errors[-1][0] == "NOT_YOUR_PRIORITY"
            assert lc.handled == []

        asyncio.run(scenario())

    def test_ping_without_window_is_not_an_error(self):
        async def scenario():
            lc = FakeLifecycle()
            conn = FakeConn("p1", seq_num=10)

            await dispatch(lc, conn, {"type": "PING", "seq_num": 99})
            # PING is exempt; it must not raise NOT_YOUR_PRIORITY.
            assert not any(c == "NOT_YOUR_PRIORITY" for c, _, _ in lc.errors)

        asyncio.run(scenario())


def test_create_grant_has_required_fields():
    grant = create_priority_grant(seq_num=7, player_id="p1", time_limit_ms=60000)
    assert grant["type"] == "PRIORITY_GRANT"
    assert grant["player_id"] == "p1"
    assert grant["time_limit_ms"] == 60000
