"""
tests/test_config.py — Server configuration defaults (Module 02).

The priority window advertises time_limit_ms in PRIORITY_GRANT; the spec
walkthrough uses 60 000 ms, and a one-hour default would stall a game for
an hour after a disconnect during priority.
"""

from server.config import ServerConfig


class TestServerConfig:

    def test_priority_timeout_default_is_60_seconds(self):
        cfg = ServerConfig()
        assert cfg.time_limit_ms == 60_000

    def test_port_default_is_4444(self):
        cfg = ServerConfig()
        assert cfg.port == 4444

    def test_disconnect_timeout_default(self):
        cfg = ServerConfig()
        assert cfg.disconnect_timeout_s > 0
