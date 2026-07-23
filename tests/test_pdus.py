"""
tests/test_pdus.py — Unit tests for PDU definitions (Module 01).

Tests factory functions, type registry, required-field validation,
and seq_num validation.
"""

import pytest

from shared.pdus import (
    PDU_TYPES,
    create_priority_pass,
    create_cast_spell,
    create_ping,
    create_game_over,
    create_error,
    validate_pdu_type,
    validate_required_fields,
    parse_and_validate,
    validate_seq_num,
)


class TestPduTypeRegistry:

    def test_all_25_types_present(self):
        assert len(PDU_TYPES) == 25
        # Spot-check a few
        assert "PRIORITY_PASS" in PDU_TYPES
        assert "CAST_SPELL" in PDU_TYPES
        assert "GAME_STATE_UPDATE" in PDU_TYPES
        assert "GAME_OVER" in PDU_TYPES

    def test_c2s_types_have_correct_direction(self):
        c2s_types = {"PLAYER_READY", "MULLIGAN_CHOICE", "PRIORITY_PASS",
                     "CAST_SPELL", "ACTIVATE_ABILITY", "PLAY_LAND",
                     "DECLARE_ATTACKERS", "DECLARE_BLOCKERS",
                     "ASSIGN_DAMAGE_ORDER", "DISCARD",
                     "TRIGGER_ORDER_RESPONSE", "TRIGGER_CHOICE_RESPONSE",
                     "CONCEDE", "PING"}
        for t in c2s_types:
            assert PDU_TYPES[t]["direction"] == "C2S", f"{t} should be C2S"

    def test_s2c_types_have_correct_direction(self):
        s2c_types = {"GAME_STATE_UPDATE", "PHASE_TRANSITION",
                     "PRIORITY_GRANT", "STACK_PUSH", "STACK_RESOLVE",
                     "TRIGGER_ORDER", "TRIGGER_CHOICE",
                     "COMBAT_DAMAGE_RESULT", "GAME_OVER", "ERROR", "PONG"}
        for t in s2c_types:
            assert PDU_TYPES[t]["direction"] == "S2C", f"{t} should be S2C"

    def test_priority_bearing_flag(self):
        assert PDU_TYPES["PRIORITY_PASS"]["priority_bearing"] is True
        assert PDU_TYPES["CAST_SPELL"]["priority_bearing"] is True
        assert PDU_TYPES["PING"]["priority_bearing"] is False
        assert PDU_TYPES["PLAYER_READY"]["priority_bearing"] is False


class TestFactoryFunctions:

    def test_create_priority_pass(self):
        pdu = create_priority_pass(seq_num=8)
        assert pdu["type"] == "PRIORITY_PASS"
        assert pdu["seq_num"] == 8

    def test_create_cast_spell(self):
        pdu = create_cast_spell(
            seq_num=10,
            card_id="lightning_bolt_001",
            targets=["player_2"],
            mana_payment={"R": 1},
        )
        assert pdu["type"] == "CAST_SPELL"
        assert pdu["seq_num"] == 10
        assert pdu["card_id"] == "lightning_bolt_001"
        assert pdu["targets"] == ["player_2"]
        assert pdu["mana_payment"] == {"R": 1}

    def test_create_ping(self):
        pdu = create_ping(seq_num=1, timestamp=1745000000000)
        assert pdu["type"] == "PING"
        assert pdu["seq_num"] == 1
        assert pdu["timestamp"] == 1745000000000

    def test_create_game_over(self):
        pdu = create_game_over(
            seq_num=100, winner_id="player_1",
            loser_id="player_2", reason="LIFE_ZERO",
        )
        assert pdu["type"] == "GAME_OVER"
        assert pdu["winner_id"] == "player_1"
        assert pdu["reason"] == "LIFE_ZERO"

    def test_create_error(self):
        pdu = create_error(
            seq_num=15, code="STALE_ACTION",
            message="Priority mismatch.",
            rejected_action={"type": "CAST_SPELL", "seq_num": 14},
        )
        assert pdu["code"] == "STALE_ACTION"
        assert pdu["rejected_action"]["seq_num"] == 14


class TestValidation:

    def test_validate_pdu_type_known(self):
        errors = validate_pdu_type({"type": "PRIORITY_PASS"})
        assert len(errors) == 0

    def test_validate_pdu_type_unknown(self):
        errors = validate_pdu_type({"type": "FOOBAR"})
        assert len(errors) > 0

    def test_validate_required_fields_pass(self):
        pdu = create_priority_pass(seq_num=8)
        errors = validate_required_fields(pdu)
        assert len(errors) == 0

    def test_validate_required_fields_missing(self):
        pdu = {"type": "CAST_SPELL", "seq_num": 10}
        # Missing card_id, targets, mana_payment
        errors = validate_required_fields(pdu)
        assert len(errors) > 0

    def test_parse_and_validate_success(self):
        pdu = create_priority_pass(seq_num=8)
        parsed, err = parse_and_validate(pdu)
        assert parsed is not None
        assert err is None

    def test_parse_and_validate_unknown_type(self):
        parsed, err = parse_and_validate({"type": "UNKNOWN"})
        assert parsed is None
        assert "UNKNOWN_TYPE" in err

    def test_validate_seq_num(self):
        assert validate_seq_num(42, 42) is True
        assert validate_seq_num(42, 43) is False
