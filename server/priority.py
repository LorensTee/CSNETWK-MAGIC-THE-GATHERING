from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine

from server.config import ServerConfig
from server.connection import ServerConnection
from shared.framing import ProtocolError
from shared.pdus import create_error, create_priority_grant, validate_seq_num


class PriorityTimeout(Exception):
    """raised when player fails to respond within the time limit"""

    def __init__(self, player_id: str) -> None:
        self.player_id = player_id
        super().__init__(f"Player '{player_id}' timed out on priority.")


class ConnectionLost(Exception):
    """raised when a player's connection is lost during priority"""

    def __init__(self, player_id: str) -> None:
        self.player_id = player_id
        super().__init__(f"Connection lost for player '{player_id}'.")


ReadPduCb = Callable[[str, float], Coroutine[Any, Any, dict[str, Any]]]

class PriorityManager:
    """manages priority grants and the pass/resolve cycle"""
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
        """grant priority to player and wait for their response"""
        timeout = timeout_ms if timeout_ms is not None else self.config.time_limit_ms
        timeout_s = timeout / 1000.0

        while True:
            # send PRIORITY_GRANT
            grant_pdu = create_priority_grant(
                seq_num=0,
                player_id=player_id,
                time_limit_ms=timeout,
            )
            await conn.send_pdu(grant_pdu)
            expected_seq = conn.seq_num

            # wait for response w/ timeout
            try:
                if read_pdu is not None:
                    response = await asyncio.wait_for(
                        read_pdu(player_id, timeout_s), timeout=timeout_s
                    )
                else:
                    response = await asyncio.wait_for(
                        conn.recv_pdu(), timeout=timeout_s
                    )
            except asyncio.TimeoutError:
                raise PriorityTimeout(player_id) from None
            except (ProtocolError, ConnectionError, EOFError, OSError) as exc:
                raise ConnectionLost(player_id) from exc

            # validate seq_num
            actual = response.get("seq_num", -1)
            if not validate_seq_num(expected_seq, actual):
                err_pdu = create_error(
                    seq_num=expected_seq,
                    code="STALE_ACTION",
                    message=(
                        f"Priority token mismatch. "
                        f"Expected {expected_seq}, got {actual}."
                    ),
                    rejected_action=response,
                )
                await conn.send_pdu(err_pdu)
                continue

            # return the response
            return response

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
        """run 1 full priority"""
        ap_response = await self.grant_priority(
            ap_conn, ap_id, read_pdu=read_pdu,
        )
        if ap_response is not None and ap_response.get("type") != "PRIORITY_PASS":
            return False, ap_response

        if on_ap_pass_cb:
            await on_ap_pass_cb()

        nap_response = await self.grant_priority(
            nap_conn, nap_id, read_pdu=read_pdu,
        )
        if nap_response is not None and nap_response.get("type") != "PRIORITY_PASS":
            return False, nap_response

        return True, None
