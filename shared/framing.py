"""
shared/framing.py — Message Framing (Module 01: Network Protocol)

Implements the MTGNP wire format (RFC §5.2):
  4-byte big-endian unsigned integer length prefix  +  UTF-8 JSON payload.

This module is the ONLY place where raw bytes are read from or written to
the TCP stream.  Both server and client import these functions.
"""

import asyncio
import json
import struct


class ProtocolError(Exception):
    """Raised when a framing or PDU-level protocol rule is violated.

    The ``code`` attribute carries an MTGNP error-code string such as
    ``"INVALID_JSON"`` or ``"UNKNOWN_TYPE"`` so that callers can map it
    directly to an ERROR PDU if needed.
    """

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        self.message = message or code
        super().__init__(self.message)


# ── Encoding ─────────────────────────────────────────────────────────────────


def encode_frame(pdu_dict: dict) -> bytes:
    """Serialize *pdu_dict* to the on-wire framing format.

    1. JSON-encode the dict to UTF-8 bytes.
    2. Prepend a 4-byte big-endian unsigned integer length.

    Returns
        The complete framed message (length prefix + payload).

    Raises
        ValueError if the payload exceeds *MAX_PDU_BYTES* (65 535).
    """
    payload = json.dumps(pdu_dict, ensure_ascii=False).encode("utf-8")
    if len(payload) > 65535:
        raise ValueError(
            f"PDU payload is {len(payload)} bytes; maximum allowed is 65535."
        )
    return struct.pack(">I", len(payload)) + payload


# ── Decoding ─────────────────────────────────────────────────────────────────


async def decode_frame(reader: asyncio.StreamReader) -> dict:
    """Read exactly one framed PDU from *reader*.

    Blocks until the full message has been received (or the stream ends).

    Returns
        The parsed JSON dict.

    Raises
        ProtocolError
            - ``"INVALID_JSON"`` if the payload is not valid UTF-8 JSON.
            - Payload exceeds *MAX_PDU_BYTES*.
        asyncio.IncompleteReadError
            If the connection is closed before the full frame arrives.
    """
    length_bytes = await reader.readexactly(4)
    length: int = struct.unpack(">I", length_bytes)[0]

    if length > 65535:
        raise ProtocolError(
            "INVALID_JSON",
            f"PDU length prefix is {length}; maximum allowed is 65535.",
        )

    payload_bytes = await reader.readexactly(length)
    try:
        return json.loads(payload_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProtocolError("INVALID_JSON", str(exc)) from exc
