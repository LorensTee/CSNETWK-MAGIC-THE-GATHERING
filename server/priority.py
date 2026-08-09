"""
server/priority.py — Priority Manager (Module 02: Server Engine)

Implements the MTGNP priority system (RFC §7.3).  The ``PriorityManager``
issues ``PRIORITY_GRANT`` PDUs to players, enforces the time limit, and
determines when both players have passed priority consecutively.

**Important:** This manager does NOT call ``conn.recv_pdu()`` directly.
Instead ``grant_priority`` accepts an optional ``read_pdu`` async callback.
The caller (``GameLifecycle``) provides ``wait_for_pdu`` which uses a
dispatcher-resolved future, keeping the TCP stream single-reader safe.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine

from server.config import ServerConfig
from server.connection import ServerConnection
from shared.framing import ProtocolError
from shared.pdus import create_error, create_priority_grant, validate_seq_num


class PriorityTimeout(Exception):
    """Raised when a player fails to respond within the time limit.

    The caller should treat this as a disconnection and broadcast
    ``GAME_OVER(DISCONNECT)``.
    """

    def __init__(self, player_id: str) -> None:
        self.player_id = player_id
        super().__init__(f"Player '{player_id}' timed out on priority.")


class ConnectionLost(Exception):
    """Raised when a player's TCP connection is lost during priority.

    The caller should treat this as a disconnection and broadcast
    ``GAME_OVER(DISCONNECT)``.
    """

    def __init__(self, player_id: str) -> None:
        self.player_id = player_id
        super().__init__(f"Connection lost for player '{player_id}'.")


ReadPduCb = Callable[[str, float], Coroutine[Any, Any, dict[str, Any]]]
"""Signature: ``async cb(player_id: str, timeout_s: float) -> dict``."""


class PriorityManager:
    """Manages priority grants and the pass/resolve cycle.

    Parameters
    ----------
    config :
        Server configuration (used for *time_limit_ms*).
    """

    def __init__(self, config: ServerConfig) -> None:
        self.config = config

    async def grant_priority(
        self,
        conn: ServerConnection,
        player_id: str,
        *,
        read_pdu: ReadPduCb | None = None,
        timeout_ms: int | None = None,
    ) -> dict[str, Any] | None:
        """Grant priority to *player_id* and wait for their response.

        Parameters
        ----------
        conn :
            The player's ``ServerConnection`` (used to send the grant).
        player_id :
            Player ID.
        read_pdu :
            Optional async callback to read the next PDU.  If provided,
            used instead of ``conn.recv_pdu()`` (avoids race conditions
            with the read-loop / dispatcher).
        timeout_ms :
            Override for *config.time_limit_ms*.

        Returns
        -------
        The action PDU, or ``None`` if the player passed.

        Raises
        ------
        PriorityTimeout
        ConnectionError
        """
        timeout = timeout_ms if timeout_ms is not None else self.config.time_limit_ms
        timeout_s = timeout / 1000.0

        # Register the response waiter BEFORE sending the grant: with the
        # real (yielding) connection, the read task starts during the
        # grant's drain() — so a fast client reply can never race the
        # pending-future registration.
        read_task: asyncio.Task | None = None
        if read_pdu is not None:
            read_task = asyncio.create_task(read_pdu(player_id, timeout_s))

        try:
            # Send the initial PRIORITY_GRANT (send_pdu sets seq_num automatically).
            grant_pdu = create_priority_grant(
                seq_num=0,  # placeholder — send_pdu overwrites this.
                player_id=player_id,
                time_limit_ms=timeout,
            )
            await conn.send_pdu(grant_pdu)
            expected_seq = conn.seq_num
            # Record the granted token on the connection so the
            # dispatcher's stale pre-filter and priority.py's strict
            # consumption check compare against the SAME value (an
            # interleaved server broadcast bumps conn.seq_num and would
            # otherwise ping-pong the retry into a lockout).
            conn.grant_token = expected_seq

            while True:
                # (Re-)register the waiter: initially before the grant, and
                # again after a stale response consumed it (RFC §11.3 retry).
                if read_task is None and read_pdu is not None:
                    read_task = asyncio.create_task(read_pdu(player_id, timeout_s))

                # Wait for response with timeout.
                try:
                    if read_task is not None:
                        response = await asyncio.wait_for(
                            asyncio.shield(read_task), timeout=timeout_s
                        )
                    else:
                        response = await asyncio.wait_for(
                            conn.recv_pdu(), timeout=timeout_s
                        )
                except asyncio.TimeoutError:
                    raise PriorityTimeout(player_id) from None
                except (ProtocolError, ConnectionError, EOFError, OSError) as exc:
                    raise ConnectionLost(player_id) from exc

                # Validate seq_num.
                actual = response.get("seq_num", -1)
                if not validate_seq_num(expected_seq, actual):
                    # RFC §11.3: reject with STALE_ACTION and re-issue the SAME
                    # token (same seq_num, no counter consumption) so the player
                    # can retry.  The ERROR echoes the rejected action's seq_num
                    # per RFC §10.2.23.  The consumed waiter is re-registered
                    # on the next iteration (fresh read for the retry).
                    err_pdu = create_error(
                        seq_num=actual,
                        code="STALE_ACTION",
                        message=(
                            f"Priority token mismatch. "
                            f"Expected {expected_seq}, got {actual}."
                        ),
                        rejected_action=response,
                    )
                    await conn.send_pdu_explicit(err_pdu, actual)
                    grant_pdu = create_priority_grant(
                        seq_num=expected_seq,
                        player_id=player_id,
                        time_limit_ms=timeout,
                    )
                    await conn.send_pdu_explicit(grant_pdu, expected_seq)
                    read_task = None  # consumed; re-register for the retry
                    continue

                # Return the response.
                return response
        finally:
            if read_task is not None:
                if not read_task.done():
                    read_task.cancel()
                elif not read_task.cancelled():
                    try:
                        read_task.exception()  # retrieve any latent error
                    except asyncio.CancelledError:
                        pass

    async def run_priority_window(
        self,
        ap_conn: ServerConnection,
        nap_conn: ServerConnection,
        ap_id: str,
        nap_id: str,
        *,
        read_pdu: ReadPduCb | None = None,
        on_ap_pass_cb: Any = None,
    ) -> tuple[bool, dict[str, Any] | None]:
        """Run one full priority window (AP → NAP).

        Parameters
        ----------
        ap_conn, nap_conn :
            Player connections.
        ap_id, nap_id :
            Player IDs.
        read_pdu :
            Async callback to read the next PDU (see *grant_priority*).

        Returns
        -------
        ``(both_passed, action_pdu, actor_id)``.

        ``actor_id`` is the player who submitted the last action PDU
        (RFC §8.1.3: that player retains priority), or ``None`` when both
        players passed.

        Raises
        ------
        PriorityTimeout
        ConnectionError
        """
        ap_response = await self.grant_priority(
            ap_conn, ap_id, read_pdu=read_pdu,
        )
        if ap_response is not None and ap_response.get("type") != "PRIORITY_PASS":
            return False, ap_response, ap_id

        if on_ap_pass_cb:
            await on_ap_pass_cb()

        nap_response = await self.grant_priority(
            nap_conn, nap_id, read_pdu=read_pdu,
        )
        if nap_response is not None and nap_response.get("type") != "PRIORITY_PASS":
            return False, nap_response, nap_id

        return True, None, None
