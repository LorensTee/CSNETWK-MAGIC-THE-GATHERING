"""
tests/test_priority.py — Unit tests for the priority manager (Module 02).

Tests grant_priority: token issuance, STALE_ACTION re-issue with the
same seq_num (RFC §11.3), and timeout → PriorityTimeout.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from server.priority import PriorityManager, PriorityTimeout


class FakeConn:
    """Minimal connection mirroring real seq semantics."""

    def __init__(self) -> None:
        self.seq_num = 0
        self.sent: list[dict[str, Any]] = []
        self.verbose = False

    async def send_pdu(self, pdu: dict[str, Any]) -> None:
        self.seq_num += 1
        pdu["seq_num"] = self.seq_num
        self.sent.append(dict(pdu))

    async def send_pdu_explicit(self, pdu: dict[str, Any], seq_num: int) -> None:
        pdu["seq_num"] = seq_num
        self.sent.append(dict(pdu))

    async def recv_pdu(self) -> dict[str, Any]:
        raise AssertionError("recv_pdu should not be used when read_pdu is given")


def _make_manager() -> PriorityManager:
    return PriorityManager(SimpleNamespace(time_limit_ms=60000))


class TestGrantPriority:

    def test_returns_action_with_matching_seq(self):
        conn = FakeConn()
        pm = _make_manager()

        async def read_pdu(pid, timeout):
            grant = [p for p in conn.sent if p["type"] == "PRIORITY_GRANT"][-1]
            return {"type": "CAST_SPELL", "seq_num": grant["seq_num"]}

        response = asyncio.run(pm.grant_priority(conn, "p1", read_pdu=read_pdu))
        assert response["type"] == "CAST_SPELL"

    def test_stale_action_reissues_grant_with_same_seq(self):
        conn = FakeConn()
        pm = _make_manager()
        state = {"stale_sent": False}

        async def read_pdu(pid, timeout):
            grant = [p for p in conn.sent if p["type"] == "PRIORITY_GRANT"][-1]
            if not state["stale_sent"]:
                state["stale_sent"] = True
                return {"type": "CAST_SPELL", "seq_num": grant["seq_num"] - 1}
            return {"type": "PRIORITY_PASS", "seq_num": grant["seq_num"]}

        response = asyncio.run(pm.grant_priority(conn, "p1", read_pdu=read_pdu))
        assert response["type"] == "PRIORITY_PASS"

        grants = [p for p in conn.sent if p["type"] == "PRIORITY_GRANT"]
        assert len(grants) == 2
        # Re-issued grant carries the SAME token seq_num (§11.3).
        assert grants[0]["seq_num"] == grants[1]["seq_num"]

        errors = [p for p in conn.sent if p["type"] == "ERROR"]
        assert len(errors) == 1
        assert errors[0]["code"] == "STALE_ACTION"
        # ERROR echoes the rejected action's seq_num (§10.2.23).
        assert errors[0]["seq_num"] == grants[0]["seq_num"] - 1

    def test_timeout_raises_priority_timeout(self):
        conn = FakeConn()
        pm = _make_manager()

        async def read_pdu(pid, timeout):
            await asyncio.sleep(timeout + 1)
            return {"type": "PRIORITY_PASS", "seq_num": 1}

        with pytest.raises(PriorityTimeout):
            asyncio.run(pm.grant_priority(
                conn, "p1", read_pdu=read_pdu, timeout_ms=50,
            ))
