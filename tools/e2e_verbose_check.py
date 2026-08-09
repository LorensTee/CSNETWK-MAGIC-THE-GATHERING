#!/usr/bin/env python3
"""
tools/e2e_verbose_check.py — Live end-to-end verbose-mode verification.

Starts the REAL server with --verbose, drives two raw-socket clients
through a scripted game (ready → mulligan keep → land → concede →
GAME_OVER → re-ready → second mulligan), then asserts the verbose log
contains direction-labelled [S→C / [C→S PDU lines and that the game
re-enters LOBBY on the same connections.

Exit code 0 = PASS, 1 = FAIL.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import struct
import subprocess
import sys
import threading
import time

HOST = "127.0.0.1"
PORT = 4555

# 40-card legal deck (≤ 50, all legal instance ids).
DECK = (
    [f"mountain_{i:03d}" for i in range(1, 11)]
    + [f"forest_{i:03d}" for i in range(1, 11)]
    + [f"goblin_guide_{i:03d}" for i in range(1, 5)]
    + [f"grizzly_bears_{i:03d}" for i in range(1, 5)]
    + [f"giant_growth_{i:03d}" for i in range(1, 5)]
    + [f"lightning_bolt_{i:03d}" for i in range(1, 5)]
)

REQUIRED_IN_LOG = ("[S→C", "[C→S", "GAME_OVER", "PRIORITY_GRANT")


class MiniClient:
    """Tiny protocol client: framed PDUs + seq echo tracking."""

    def __init__(self, player_id: str) -> None:
        self.player_id = player_id
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.ready_seq = 0  # client's own READY/PING counter
        self.echo_seq = 0   # last server seq to echo in actions
        self._queue: asyncio.Queue = asyncio.Queue()
        self._pump: asyncio.Task | None = None

    async def connect(self) -> None:
        self.reader, self.writer = await asyncio.open_connection(HOST, PORT)
        self._queue = asyncio.Queue()
        # ONE reader per stream: the pump feeds the queue; the protocol
        # logic consumes queue items (safe to cancel queue gets, unlike
        # mid-read readexactly cancellations).
        self._pump = asyncio.create_task(self._pump_loop())

    async def _pump_loop(self) -> None:
        while True:
            try:
                # No timeout here: the pump is the stream's only reader and
                # must survive quiet periods (e.g. while the server waits
                # for the OTHER player's response).  A timed-out read would
                # kill the pump and starve the queue permanently.
                pdu = await self._recv_raw(timeout=None)
            except (ConnectionError, EOFError, OSError,
                    asyncio.IncompleteReadError, asyncio.TimeoutError):
                return
            await self._queue.put(pdu)

    async def connect_with_retry(self) -> None:
        """Connect, retrying while the server cold-starts (CSV loading)."""
        for _ in range(50):
            try:
                await self.connect()
                return
            except OSError:
                await asyncio.sleep(0.2)
        await self.connect()  # let the last error propagate

    async def send(self, pdu: dict) -> None:
        payload = json.dumps(pdu).encode("utf-8")
        frame = struct.pack(">I", len(payload)) + payload
        self.writer.write(frame)
        await self.writer.drain()
        print(f"  {self.player_id} --> {pdu.get('type')} seq={pdu.get('seq_num')}",
              flush=True)

    async def _recv_raw(self, timeout: float = 10.0) -> dict:
        assert self.reader is not None
        size_b = await asyncio.wait_for(self.reader.readexactly(4), timeout)
        (size,) = struct.unpack(">I", size_b)
        payload = await asyncio.wait_for(
            self.reader.readexactly(size), timeout
        )
        pdu = json.loads(payload)
        seq = pdu.get("seq_num")
        if isinstance(seq, int):
            self.echo_seq = seq
        pdu_type = pdu.get("type", "?")
        if pdu_type == "GAME_STATE_UPDATE":
            print(f"  {self.player_id} <-- GSU phase="
                  f"{pdu['state'].get('phase')}", flush=True)
        else:
            print(f"  {self.player_id} <-- {pdu_type}", flush=True)
        return pdu

    async def recv(self, timeout: float = 10.0) -> dict:
        """Next PDU from this client's stream (via the pump queue)."""
        return await asyncio.wait_for(self._queue.get(), timeout)

    def close(self) -> None:
        if self._pump is not None:
            self._pump.cancel()
        if self.writer is not None:
            self.writer.close()

    async def wait_until_phase(self, phase: str) -> dict:
        """Receive PDUs until a GAME_STATE_UPDATE for *phase* arrives."""
        while True:
            pdu = await self.recv()
            if pdu["type"] == "GAME_STATE_UPDATE" \
                    and pdu["state"].get("phase") == phase:
                return pdu

    async def wait_until_transition(self, to_phase: str) -> dict:
        """Receive PDUs until a PHASE_TRANSITION to *to_phase* arrives."""
        while True:
            pdu = await self.recv()
            if pdu["type"] == "PHASE_TRANSITION" \
                    and pdu.get("to_phase") == to_phase:
                return pdu

    async def wait_until_type(self, pdu_type: str, timeout: float = 10.0) -> dict:
        """Receive PDUs until one of *pdu_type* arrives."""
        while True:
            pdu = await self.recv(timeout)
            if pdu.get("type") == pdu_type:
                return pdu

    async def send_action(self, pdu: dict) -> None:
        pdu["seq_num"] = self.echo_seq
        await self.send(pdu)

    def close(self) -> None:
        if self.writer is not None:
            self.writer.close()


async def run_game_phase(c1: MiniClient, c2: MiniClient,
                         log_lines: list[str]) -> None:
    """Ready → mulligan keep → land → concede → GAME_OVER → re-ready."""
    for c in (c1, c2):
        c.ready_seq += 1
        await c.send({
            "type": "PLAYER_READY",
            "seq_num": c.ready_seq,
            "player_id": c.player_id,
            "deck_list": DECK,
        })

    # Both players reach MULLIGAN (LOBBY + GAME_SETUP GSUs come first).
    gsu1 = await c1.wait_until_phase("MULLIGAN")
    await c2.wait_until_phase("MULLIGAN")
    assert gsu1["type"] == "GAME_STATE_UPDATE"

    # Keep for both (echo the MULLIGAN GSU seq).
    for c in (c1, c2):
        await c.send({
            "type": "MULLIGAN_CHOICE",
            "seq_num": c.echo_seq,
            "player_id": c.player_id,
            "keep": True,
            "cards_to_bottom": [],
        })

    # Turn 1 begins — determine the first player from the first grant
    # (the server coin-flips the first player in GAME_SETUP).  Race the
    # two streams via their pump queues (queue gets are safe to cancel).
    async def recv_either() -> dict:
        get1 = asyncio.create_task(c1.recv())
        get2 = asyncio.create_task(c2.recv())
        done, pending = await asyncio.wait(
            {get1, get2}, return_when=asyncio.FIRST_COMPLETED,
        )
        for p in pending:
            p.cancel()
        return done.pop().result()

    first = None
    for _ in range(50):
        pdu = await recv_either()
        if pdu["type"] == "PRIORITY_GRANT":
            first = c1 if pdu["player_id"] == c1.player_id else c2
            break
    assert first is not None, "no PRIORITY_GRANT received"
    second = c2 if first is c1 else c1

    # The first grant (consumed by the waiter, UPKEEP window) needs an
    # answer right away — pass.  Then keep passing non-main phases and
    # play a land in PRECOMBAT_MAIN (caster retains priority — §8.1.3).
    # After a pass, priority moves to the other player — track the holder.
    await first.send_action({
        "type": "PRIORITY_PASS", "player_id": first.player_id,
    })
    holder = second  # the pass moves priority to the other player
    current_phase = None
    land_played = False
    passed_after_land = False
    while not passed_after_land:
        pdu = await holder.recv()
        if pdu["type"] == "GAME_STATE_UPDATE":
            current_phase = pdu["state"].get("phase")
        elif pdu["type"] == "PRIORITY_GRANT":
            if current_phase == "PRECOMBAT_MAIN" and not land_played:
                await holder.send_action({
                    "type": "PLAY_LAND", "player_id": holder.player_id,
                    "card_id": "mountain_001",
                })
                land_played = True
                # Caster retains priority (§8.1.3) — holder unchanged.
            else:
                await holder.send_action({
                    "type": "PRIORITY_PASS", "player_id": holder.player_id,
                })
                if land_played:
                    passed_after_land = True
                holder = second if holder is first else first

    # The second player concedes during its priority window.
    while True:
        pdu = await second.recv()
        if pdu["type"] == "PRIORITY_GRANT":
            break
    await second.send_action({
        "type": "CONCEDE", "player_id": second.player_id,
    })

    # GAME_OVER broadcast to both.
    go1 = await c1.wait_until_type("GAME_OVER")
    await c2.wait_until_type("GAME_OVER")
    assert go1["reason"] in ("WIN", "LOSS", "CONCEDE", "DISCONNECT")


async def run_disconnect_phase(c1: MiniClient, c2: MiniClient) -> None:
    """Game 3: mulligan + first grant, then kill one player abruptly and
    verify the survivor receives GAME_OVER(DISCONNECT) from the watchdog."""
    for c in (c1, c2):
        await c.send({
            "type": "PLAYER_READY",
            "seq_num": c.ready_seq,
            "player_id": c.player_id,
            "deck_list": DECK,
        })
    await c1.wait_until_phase("MULLIGAN")
    await c2.wait_until_phase("MULLIGAN")

    # Keep for both (echo the MULLIGAN GSU seq).
    for c in (c1, c2):
        await c.send_action({
            "type": "MULLIGAN_CHOICE", "player_id": c.player_id,
            "keep": True, "cards_to_bottom": [],
        })
    await c1.wait_until_phase("MULLIGAN")
    await c2.wait_until_phase("MULLIGAN")

    # Wait for the first PRIORITY_GRANT (game in progress), then kill p2.
    for _ in range(50):
        g1 = asyncio.create_task(c1.recv())
        g2 = asyncio.create_task(c2.recv())
        done, pending = await asyncio.wait(
            {g1, g2}, return_when=asyncio.FIRST_COMPLETED,
        )
        for p in pending:
            p.cancel()
        pdu = done.pop().result()
        if pdu["type"] == "PRIORITY_GRANT":
            break
    c2.close()  # abrupt disconnect — no CONCEDE

    # The watchdog (10 s disconnect timeout) ends the game for the survivor.
    go = await c1.wait_until_type("GAME_OVER", timeout=15)
    assert go["reason"] == "DISCONNECT", go.get("reason")
    print(f"  OK: survivor got GAME_OVER reason={go['reason']} "
          f"winner={go.get('winner_id')}", flush=True)


async def main() -> int:
    server = subprocess.Popen(
        [sys.executable, "-m", "server.main",
         "--host", HOST, "--port", str(PORT), "--verbose"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    log_lines: list[str] = []

    def _reader() -> None:
        assert server.stdout is not None
        for line in server.stdout:
            log_lines.append(line.rstrip("\n"))

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()
    try:
        c1, c2 = MiniClient("p1"), MiniClient("p2")
        await c1.connect_with_retry()
        await c2.connect_with_retry()

        # Game 1.
        await run_game_phase(c1, c2, log_lines)

        # Re-ready on the SAME connections → second game's mulligan.
        await run_game_phase(c1, c2, log_lines)

        # Game 3: disconnect scenario — watchdog GAME_OVER(DISCONNECT).
        await run_disconnect_phase(c1, c2)

        c1.close()
        c2.close()
    except BaseException as exc:
        print(f"FAILED: {exc!r}", flush=True)
        print("=== SERVER LOG (FULL) ===", flush=True)
        print("\n".join(log_lines), flush=True)
        raise
    finally:
        server.send_signal(signal.SIGINT)
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
        reader.join(timeout=5)

    text = "\n".join(log_lines)
    ok = all(req in text for req in REQUIRED_IN_LOG)
    # Both games reached GAME_OVER (2 occurrences).
    ok = ok and text.count("GAME_OVER") >= 2
    print("=== VERBOSE LOG (sample) ===")
    for line in log_lines:
        if any(req in line for req in ("[S→C", "[C→S", "GAME_OVER")):
            print(line)
    print("============================")
    if not ok:
        print("FAIL: missing required log markers:", [
            req for req in REQUIRED_IN_LOG if req not in text
        ])
        return 1
    print(f"PASS: verbose log OK ({len(log_lines)} lines, "
          f"[S→C={text.count('[S→C')}, [C→S={text.count('[C→S')})")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
