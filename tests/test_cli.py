"""CLI tests for ``atomno-mcp-rosreestr``.

Covers ``--help``, ``--version``, transport/log-level validation, parser defaults,
and loud-fail on invalid env — without starting the MCP stdio loop.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest
import respx

from mcp_rosreestr import __version__
from mcp_rosreestr.constants import ENV_LOG_LEVEL
from mcp_rosreestr import server


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        ENV_LOG_LEVEL,
        "ROSREESTR_LOG_LEVEL",
        "MCP_ROSREESTR_HTTP_TIMEOUT",
        "ROSREESTR_HTTP_TIMEOUT",
        "MCP_ROSREESTR_CACHE_PATH",
        "ROSREESTR_CACHE_PATH",
        "MCP_ROSREESTR_CACHE_TTL_LIVE",
        "ROSREESTR_CACHE_TTL_LIVE",
        "MCP_ROSREESTR_CACHE_TTL_STATIC",
        "ROSREESTR_CACHE_TTL_STATIC",
        "MCP_ROSREESTR_USER_AGENT",
        "ROSREESTR_USER_AGENT",
    ):
        monkeypatch.delenv(var, raising=False)
    server._warned_legacy_envs.clear()


@pytest.fixture
def fake_run(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {"called": False, "kwargs": None}

    def _fake_run(**kwargs: Any) -> None:
        captured["called"] = True
        captured["kwargs"] = kwargs

    monkeypatch.setattr(server.mcp, "run", _fake_run)
    return captured


class TestHelp:
    def test_dash_dash_help_prints_usage_and_exits_zero(
        self, capsys: pytest.CaptureFixture[str], fake_run: dict[str, Any]
    ) -> None:
        with pytest.raises(SystemExit) as exc_info:
            server.main(["--help"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "atomno-mcp-rosreestr" in out
        assert "--transport" in out
        assert "--version" in out
        assert "--log-level" in out
        assert fake_run["called"] is False

    def test_dash_h_short_flag_also_works(self, fake_run: dict[str, Any]) -> None:
        with pytest.raises(SystemExit) as exc_info:
            server.main(["-h"])
        assert exc_info.value.code == 0
        assert fake_run["called"] is False


class TestVersion:
    def test_dash_dash_version_prints_version_and_exits_zero(
        self, capsys: pytest.CaptureFixture[str], fake_run: dict[str, Any]
    ) -> None:
        with pytest.raises(SystemExit) as exc_info:
            server.main(["--version"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert __version__ in out
        assert "atomno-mcp-rosreestr" in out
        assert fake_run["called"] is False

    def test_dash_big_v_short_flag_also_works(
        self, capsys: pytest.CaptureFixture[str], fake_run: dict[str, Any]
    ) -> None:
        with pytest.raises(SystemExit) as exc_info:
            server.main(["-V"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert __version__ in out


class TestTransportValidation:
    def test_invalid_transport_exits_two_without_starting_server(
        self, capsys: pytest.CaptureFixture[str], fake_run: dict[str, Any]
    ) -> None:
        with pytest.raises(SystemExit) as exc_info:
            server.main(["--transport", "nonexistent-transport"])
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "invalid choice" in err.lower() or "nonexistent-transport" in err
        assert fake_run["called"] is False

    def test_all_documented_transports_pass_validation(self) -> None:
        parser = server._build_arg_parser()
        for transport in server._SUPPORTED_TRANSPORTS:
            args = parser.parse_args(["--transport", transport])
            assert args.transport == transport

    def test_default_transport_is_stdio(self, fake_run: dict[str, Any]) -> None:
        rc = server.main([])
        assert rc == 0
        assert fake_run["kwargs"] == {"transport": "stdio"}


class TestLogLevelValidation:
    def test_invalid_log_level_exits_two(self, fake_run: dict[str, Any]) -> None:
        with pytest.raises(SystemExit) as exc_info:
            server.main(["--log-level", "TRACE"])
        assert exc_info.value.code == 2
        assert fake_run["called"] is False

    def test_all_valid_log_levels_pass(self) -> None:
        parser = server._build_arg_parser()
        for lvl in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            args = parser.parse_args(["--log-level", lvl])
            assert args.log_level == lvl

    def test_resolve_cli_flag_wins_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_LOG_LEVEL, "ERROR")
        assert server._resolve_log_level("DEBUG") == "DEBUG"

    def test_resolve_falls_back_to_env_when_cli_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_LOG_LEVEL, "WARNING")
        assert server._resolve_log_level(None) == "WARNING"

    def test_resolve_normalizes_env_case_and_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_LOG_LEVEL, "  debug  ")
        assert server._resolve_log_level(None) == "DEBUG"

    def test_resolve_default_is_info_when_nothing_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ENV_LOG_LEVEL, raising=False)
        monkeypatch.delenv("ROSREESTR_LOG_LEVEL", raising=False)
        assert server._resolve_log_level(None) == "INFO"

    def test_resolve_rejects_invalid_env_loudly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_LOG_LEVEL, "TRACE")
        with pytest.raises(SystemExit) as exc_info:
            server._resolve_log_level(None)
        assert exc_info.value.code == 2


class TestParserDefaults:
    def test_default_host_is_localhost(self) -> None:
        args = server._build_arg_parser().parse_args([])
        assert args.host == server._DEFAULT_HTTP_HOST == "127.0.0.1"

    def test_default_port_is_8000(self) -> None:
        args = server._build_arg_parser().parse_args([])
        assert args.port == server._DEFAULT_HTTP_PORT == 8000

    def test_port_flag_parses_as_int(self) -> None:
        args = server._build_arg_parser().parse_args(["--port", "9090"])
        assert args.port == 9090
        assert isinstance(args.port, int)


class TestInvalidEnvBailsOutCleanly:
    def test_invalid_env_and_no_cli_flag_exits_two(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], fake_run: dict[str, Any]
    ) -> None:
        monkeypatch.setenv(ENV_LOG_LEVEL, "VERBOSE")
        with pytest.raises(SystemExit) as exc_info:
            server.main([])
        assert exc_info.value.code == 2
        assert fake_run["called"] is False
        err = capsys.readouterr().err
        assert ENV_LOG_LEVEL in err


class TestCheckConfig:
    @respx.mock
    def test_check_config_exits_zero(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("MCP_ROSREESTR_CACHE_PATH", str(tmp_path / "cache.sqlite"))
        respx.head("https://nspd.gov.ru/").mock(return_value=httpx.Response(200))
        respx.head("https://pkk5.rosreestr.ru/").mock(return_value=httpx.Response(200))
        rc = server.main(["--check-config"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "atomno-mcp-rosreestr" in out
        assert "upstream.nspd" in out


class TestBuildToolContext:
    def test_reads_canonical_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        monkeypatch.setenv("MCP_ROSREESTR_HTTP_TIMEOUT", "12")
        monkeypatch.setenv("MCP_ROSREESTR_CACHE_PATH", str(tmp_path / "c.sqlite"))
        monkeypatch.setenv("MCP_ROSREESTR_CACHE_TTL_LIVE", "100")
        monkeypatch.setenv("MCP_ROSREESTR_CACHE_TTL_STATIC", "200")
        monkeypatch.setenv("MCP_ROSREESTR_USER_AGENT", "test-agent/1")
        ctx, http_client, cache = server.build_tool_context()
        try:
            assert ctx.ttl_live_seconds == 100
            assert ctx.ttl_static_seconds == 200
            assert http_client.headers["User-Agent"] == "test-agent/1"
        finally:
            cache.close()

    def test_invalid_float_env_uses_default(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: Any
    ) -> None:
        monkeypatch.setenv("MCP_ROSREESTR_HTTP_TIMEOUT", "not-a-float")
        monkeypatch.setenv("MCP_ROSREESTR_CACHE_PATH", str(tmp_path / "c.sqlite"))
        with caplog.at_level(logging.WARNING, logger="mcp_rosreestr"):
            _ctx, http_client, cache = server.build_tool_context()
        try:
            assert http_client.timeout.connect == 15.0
        finally:
            cache.close()

    def test_legacy_env_emits_deprecation_warning(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("ROSREESTR_LOG_LEVEL", "WARNING")
        with caplog.at_level(logging.WARNING, logger="mcp_rosreestr"):
            value = server._resolve_env(ENV_LOG_LEVEL)
        assert value == "WARNING"
        assert any("ROSREESTR_LOG_LEVEL" in rec.message for rec in caplog.records)


class TestHttpTransportKwargs:
    def test_http_transport_passes_host_and_port(self, fake_run: dict[str, Any]) -> None:
        rc = server.main(["--transport", "http", "--host", "0.0.0.0", "--port", "9000"])
        assert rc == 0
        assert fake_run["kwargs"] == {
            "transport": "http",
            "host": "0.0.0.0",
            "port": 9000,
        }
