"""
tests/test_framing.py — Unit tests for message framing (Module 01).

Tests encode_frame / decode_frame round-trip, max-size rejection,
and ProtocolError on invalid input.

All async decoding is exercised via ``asyncio.run()`` with the reader
created inside the coroutine so an event loop is available.
"""

import asyncio
import json
import struct

import pytest

from shared.framing import ProtocolError, encode_frame, decode_frame


class TestEncodeFrame:

    def test_encodes_valid_pdu(self):
        pdu = {"type": "PING", "seq_num": 1, "timestamp": 1000}
        frame = encode_frame(pdu)
        assert len(frame) > 4
        length = struct.unpack(">I", frame[:4])[0]
        payload = frame[4:]
        assert length == len(payload)
        decoded = json.loads(payload)
        assert decoded["type"] == "PING"

    def test_rejects_oversized_pdu(self):
        large = {"type": "PING", "seq_num": 1,
                 "data": "x" * 70000}
        with pytest.raises(ValueError):
            encode_frame(large)

    def test_ensure_ascii_false(self):
        pdu = {"type": "PING", "seq_num": 1, "label": "café"}
        frame = encode_frame(pdu)
        payload = json.loads(frame[4:])
        assert payload["label"] == "café"


# Helper: run decode_frame with a frame bytes payload.
def _decode_frame_sync(frame_bytes: bytes) -> dict:
    """Feed *frame_bytes* into a StreamReader and run decode_frame."""
    async def decode():
        reader = asyncio.StreamReader()
        reader.feed_data(frame_bytes)
        reader.feed_eof()
        return await decode_frame(reader)
    return asyncio.run(decode())


def _decode_frame_raises(frame_bytes: bytes, expected_code: str):
    """Assert that decoding *frame_bytes* raises ProtocolError(code)."""
    async def decode():
        reader = asyncio.StreamReader()
        reader.feed_data(frame_bytes)
        reader.feed_eof()
        with pytest.raises(ProtocolError) as exc:
            await decode_frame(reader)
        assert exc.value.code == expected_code
    asyncio.run(decode())


class TestDecodeFrame:

    def test_decodes_valid_frame(self):
        pdu = {"type": "PONG", "seq_num": 5, "timestamp": 2000}
        frame = encode_frame(pdu)
        result = _decode_frame_sync(frame)
        assert result["type"] == "PONG"
        assert result["seq_num"] == 5

    def test_raises_on_invalid_json(self):
        payload = b"not json at all"
        frame = struct.pack(">I", len(payload)) + payload
        _decode_frame_raises(frame, "INVALID_JSON")

    def test_raises_on_oversized_length(self):
        length = 100000  # > 65535
        frame = struct.pack(">I", length) + b"x" * 100
        _decode_frame_raises(frame, "INVALID_JSON")

    def test_raises_on_incomplete_read(self):
        frame = struct.pack(">I", 100) + b"short"  # only 5 bytes of 100
        async def decode():
            reader = asyncio.StreamReader()
            reader.feed_data(frame)
            reader.feed_eof()
            with pytest.raises(asyncio.IncompleteReadError):
                await decode_frame(reader)
        asyncio.run(decode())


class TestRoundTrip:

    def test_round_trip(self):
        original = {"type": "CAST_SPELL", "seq_num": 7,
                    "card_id": "lightning_bolt_001",
                    "targets": ["player_2"],
                    "mana_payment": {"R": 1}}
        frame = encode_frame(original)
        decoded = _decode_frame_sync(frame)
        assert decoded == original

    def test_empty_pdu_round_trip(self):
        pdu = {}
        frame = encode_frame(pdu)
        decoded = _decode_frame_sync(frame)
        assert decoded == {}
