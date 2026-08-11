import asyncio
import json
import struct


class ProtocolError(Exception):
    """raised when protocol is violated"""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        self.message = message or code
        super().__init__(self.message)




def encode_frame(pdu_dict: dict) -> bytes:
    payload = json.dumps(pdu_dict, ensure_ascii=False).encode("utf-8")
    if len(payload) > 65535:
        raise ValueError(
            f"PDU payload is {len(payload)} bytes; maximum allowed is 65535."
        )
    return struct.pack(">I", len(payload)) + payload

async def decode_frame(reader: asyncio.StreamReader) -> dict:
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
