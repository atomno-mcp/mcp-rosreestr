"""FastMCP entry point for ``mcp-rosreestr``.

Run as:

    python -m mcp_rosreestr
    # or, after `pip install atomno-mcp-rosreestr`:
    atomno-mcp-rosreestr
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from mcp.server.fastmcp import Context, FastMCP

from . import __version__
from .cache import SqliteCache
from .client import DEFAULT_TIMEOUT, DEFAULT_USER_AGENT, RosreestrClient
from .constants import ENV_LOG_LEVEL
from .errors import RosreestrError
from .schemas import (
    AddressLookupResult,
    CadastralObjectInfo,
    CadastralValue,
    PointLookupResult,
)
from .tools import (
    DEFAULT_TTL_LIVE_SECONDS,
    DEFAULT_TTL_STATIC_SECONDS,
    ToolContext,
)
from .tools import (
    get_cadastral_value as _get_cadastral_value,
)
from .tools import (
    lookup_by_address as _lookup_by_address,
)
from .tools import (
    lookup_by_cadastral as _lookup_by_cadastral,
)
from .tools import (
    lookup_by_coords as _lookup_by_coords,
)

logger = logging.getLogger("mcp_rosreestr")

# ---------------------------------------------------------------------------
# Env-var compatibility: MCP_ROSREESTR_* (canonical) > ROSREESTR_* (legacy).
# ---------------------------------------------------------------------------

_LEGACY_ENV_RENAME: dict[str, str] = {
    "ROSREESTR_LOG_LEVEL": ENV_LOG_LEVEL,
    "ROSREESTR_HTTP_TIMEOUT": "MCP_ROSREESTR_HTTP_TIMEOUT",
    "ROSREESTR_CACHE_PATH": "MCP_ROSREESTR_CACHE_PATH",
    "ROSREESTR_CACHE_TTL_LIVE": "MCP_ROSREESTR_CACHE_TTL_LIVE",
    "ROSREESTR_CACHE_TTL_STATIC": "MCP_ROSREESTR_CACHE_TTL_STATIC",
    "ROSREESTR_USER_AGENT": "MCP_ROSREESTR_USER_AGENT",
}
_warned_legacy_envs: set[str] = set()


def _resolve_env(canonical_name: str) -> str | None:
    value = os.environ.get(canonical_name)
    if value:
        return value
    legacy_name = next(
        (legacy for legacy, canonical in _LEGACY_ENV_RENAME.items() if canonical == canonical_name),
        None,
    )
    if legacy_name is None:
        return None
    legacy_value = os.environ.get(legacy_name)
    if legacy_value and legacy_name not in _warned_legacy_envs:
        _warned_legacy_envs.add(legacy_name)
        logger.warning(
            "%s is deprecated; use %s instead. Old name still works for now.",
            legacy_name,
            canonical_name,
        )
    return legacy_value


def _read_float_env(name: str, default: float) -> float:
    raw = _resolve_env(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("invalid float in env var %s=%r, using %s", name, raw, default)
        return default


def _read_int_env(name: str, default: int) -> int:
    raw = _resolve_env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("invalid int in env var %s=%r, using %s", name, raw, default)
        return default


def build_tool_context() -> tuple[ToolContext, httpx.AsyncClient, SqliteCache]:
    """Construct the ``ToolContext`` used by every tool."""
    timeout = _read_float_env("MCP_ROSREESTR_HTTP_TIMEOUT", DEFAULT_TIMEOUT)
    cache_path = _resolve_env("MCP_ROSREESTR_CACHE_PATH") or "./mcp_rosreestr_cache.sqlite"
    ttl_live = _read_int_env("MCP_ROSREESTR_CACHE_TTL_LIVE", DEFAULT_TTL_LIVE_SECONDS)
    ttl_static = _read_int_env("MCP_ROSREESTR_CACHE_TTL_STATIC", DEFAULT_TTL_STATIC_SECONDS)

    user_agent = _resolve_env("MCP_ROSREESTR_USER_AGENT") or (
        f"atomno-mcp-rosreestr/{__version__} (+https://github.com/atomno-mcp/mcp-rosreestr)"
    )

    http_client = httpx.AsyncClient(
        timeout=timeout,
        headers={
            "User-Agent": user_agent,
            "Accept": "application/json,*/*",
        },
        transport=httpx.AsyncHTTPTransport(retries=2),
    )
    rr_client = RosreestrClient(http_client=http_client, timeout=timeout, user_agent=user_agent)
    cache = SqliteCache(cache_path)
    ctx = ToolContext(
        client=rr_client,
        cache=cache,
        ttl_live_seconds=ttl_live,
        ttl_static_seconds=ttl_static,
    )
    return ctx, http_client, cache


@asynccontextmanager
async def _lifespan(_server: FastMCP) -> AsyncIterator[ToolContext]:
    ctx, http_client, cache = build_tool_context()
    try:
        yield ctx
    finally:
        try:
            await ctx.client.aclose()
        finally:
            await http_client.aclose()
            cache.close()


mcp = FastMCP(
    name="mcp-rosreestr",
    instructions=(
        "Tools for the Russian Rosreestr public cadastral data (НСПД / ПКК)."
        " Look up objects by cadastral number, address or coordinates, and"
        " read cadastral value with any cached history. All data is open"
        " (no PII / owner names). Responses are cached locally to be polite"
        " to the upstream APIs."
    ),
    lifespan=_lifespan,
)


def _ctx(ctx: Context) -> ToolContext:
    lifespan_ctx = ctx.request_context.lifespan_context
    if not isinstance(lifespan_ctx, ToolContext):  # pragma: no cover
        raise RuntimeError("server is not initialized: missing ToolContext")
    return lifespan_ctx


def _format_error(exc: Exception) -> str:
    name = type(exc).__name__
    return f"{name}: {exc}"


@mcp.tool(
    name="lookup_by_cadastral",
    description=(
        "Get the public characteristics of a Russian cadastral object by"
        " cadastral number (formatted XX:XX:XXXXXXX:XXXX, for example"
        " 77:01:0001066:1234). Returns area, year built, cadastral cost,"
        " permitted use and address. Open data only — no owner PII."
    ),
)
async def tool_lookup_by_cadastral(
    ctx: Context,
    cadastral_number: str,
    include_geometry: bool = False,
) -> CadastralObjectInfo:
    try:
        return await _lookup_by_cadastral(
            _ctx(ctx),
            cadastral_number,
            include_geometry=include_geometry,
        )
    except RosreestrError as exc:
        raise RuntimeError(_format_error(exc)) from exc


@mcp.tool(
    name="lookup_by_address",
    description=(
        "Find Russian cadastral objects matching a free-text address."
        " Searches the public NSPD geoportal index. Returns up to `limit`"
        " hits with cadastral numbers and a confidence score."
    ),
)
async def tool_lookup_by_address(
    ctx: Context,
    address: str,
    limit: int = 5,
    object_types: list[str] | None = None,
) -> AddressLookupResult:
    try:
        return await _lookup_by_address(
            _ctx(ctx),
            address,
            limit=limit,
            object_types=object_types,
        )
    except RosreestrError as exc:
        raise RuntimeError(_format_error(exc)) from exc


@mcp.tool(
    name="lookup_by_coords",
    description=(
        "Show Russian cadastral objects (parcels and buildings) at a given"
        " geographic point. Coordinates are WGS84 (EPSG:4326). Useful for"
        " 'what's at this dot on the map' lookups."
    ),
)
async def tool_lookup_by_coords(
    ctx: Context,
    lat: float,
    lon: float,
    buffer_meters: float = 5.0,
    object_types: list[str] | None = None,
) -> PointLookupResult:
    try:
        return await _lookup_by_coords(
            _ctx(ctx),
            lat,
            lon,
            buffer_meters=buffer_meters,
            object_types=object_types,
        )
    except RosreestrError as exc:
        raise RuntimeError(_format_error(exc)) from exc


@mcp.tool(
    name="get_cadastral_value",
    description=(
        "Return the current cadastral value of a Russian property by"
        " cadastral number, plus any locally cached revaluation history."
        " Tax base for property tax. Note: full audit trail of historical"
        " revaluations is only available via Pro tools or an EGRN extract."
    ),
)
async def tool_get_cadastral_value(
    ctx: Context,
    cadastral_number: str,
) -> CadastralValue:
    try:
        return await _get_cadastral_value(_ctx(ctx), cadastral_number)
    except RosreestrError as exc:
        raise RuntimeError(_format_error(exc)) from exc


# ---------------------------------------------------------------------------
# Health-check (--check-config)
# ---------------------------------------------------------------------------


async def _check_config_async() -> int:
    """Verify local cache and upstream reachability. Returns exit code."""
    timeout = _read_float_env("MCP_ROSREESTR_HTTP_TIMEOUT", DEFAULT_TIMEOUT)
    cache_path = _resolve_env("MCP_ROSREESTR_CACHE_PATH") or "./mcp_rosreestr_cache.sqlite"

    ok = True

    print(f"atomno-mcp-rosreestr {__version__}")
    print(f"  cache.path        = {cache_path}")
    cache = SqliteCache(cache_path)
    try:
        stats = cache.stats()
        print(f"  cache.stats       = {stats}")
    except Exception as exc:
        print(f"  cache.stats       = ERROR ({exc})")
        ok = False
    finally:
        cache.close()

    async with httpx.AsyncClient(
        timeout=timeout,
        headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json,*/*"},
    ) as http_client:
        for name, url in (
            ("nspd", "https://nspd.gov.ru/"),
            ("pkk", "https://pkk5.rosreestr.ru/"),
        ):
            try:
                resp = await http_client.head(url, follow_redirects=True)
                print(f"  upstream.{name:<5} = HTTP {resp.status_code}")
                if resp.status_code >= 500:
                    ok = False
            except httpx.HTTPError as exc:
                print(f"  upstream.{name:<5} = ERROR ({type(exc).__name__}: {exc})")
                ok = False

    print("  status            =", "OK" if ok else "DEGRADED")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# CLI — argparse wrapper (MCP_BUILD_CHECKLIST §2)
# ---------------------------------------------------------------------------

_SUPPORTED_TRANSPORTS = ("stdio", "http", "sse", "streamable-http")
_DEFAULT_TRANSPORT = "stdio"
_DEFAULT_HTTP_HOST = "127.0.0.1"
_DEFAULT_HTTP_PORT = 8000
_VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

_CLI_DESCRIPTION = (
    "MCP-сервер для публичных открытых данных Росреестра РФ (НСПД / ПКК): "
    "поиск по кадастровому номеру, адресу или координатам, кадастровая стоимость. "
    "Только open data — без ПДн собственников."
)

_CLI_EPILOG = (
    "Примеры:\n"
    "  atomno-mcp-rosreestr                              # stdio для MCP-клиента\n"
    "  atomno-mcp-rosreestr --transport http --port 8000\n"
    "  atomno-mcp-rosreestr --check-config\n"
    "\n"
    "Переменные окружения (canonical):\n"
    "  MCP_ROSREESTR_HTTP_TIMEOUT      — HTTP-таймаут upstream (сек).\n"
    "  MCP_ROSREESTR_CACHE_PATH        — путь к локальному SQLite-кэшу.\n"
    "  MCP_ROSREESTR_CACHE_TTL_LIVE    — TTL «живых» данных (сек).\n"
    "  MCP_ROSREESTR_CACHE_TTL_STATIC  — TTL статичных данных (сек).\n"
    "  MCP_ROSREESTR_USER_AGENT        — User-Agent для upstream.\n"
    "  MCP_ROSREESTR_LOG_LEVEL         — уровень логирования (перекрывается --log-level).\n"
    "\n"
    "Legacy-имена ROSREESTR_* поддерживаются с DeprecationWarning.\n"
    "\n"
    "Документация: https://github.com/atomno-mcp/mcp-rosreestr"
)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atomno-mcp-rosreestr",
        description=_CLI_DESCRIPTION,
        epilog=_CLI_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        "-V",
        action="version",
        version=f"atomno-mcp-rosreestr {__version__}",
        help="показать версию пакета и выйти",
    )
    parser.add_argument(
        "--transport",
        "-t",
        choices=_SUPPORTED_TRANSPORTS,
        default=_DEFAULT_TRANSPORT,
        help=(
            f"MCP-транспорт (по умолчанию: {_DEFAULT_TRANSPORT}). "
            "stdio — для локальных MCP-клиентов; http/sse/streamable-http — для сетевых."
        ),
    )
    parser.add_argument(
        "--host",
        default=_DEFAULT_HTTP_HOST,
        help=(
            f"Хост для http/sse/streamable-http транспортов (по умолчанию: {_DEFAULT_HTTP_HOST}). "
            "Игнорируется для stdio."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=_DEFAULT_HTTP_PORT,
        help=(
            f"Порт для http/sse/streamable-http транспортов (по умолчанию: {_DEFAULT_HTTP_PORT}). "
            "Игнорируется для stdio."
        ),
    )
    parser.add_argument(
        "--log-level",
        "-l",
        choices=_VALID_LOG_LEVELS,
        default=None,
        help=(
            "Уровень логирования; перекрывает MCP_ROSREESTR_LOG_LEVEL "
            "(и legacy ROSREESTR_LOG_LEVEL). По умолчанию INFO."
        ),
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Проверить конфигурацию, кэш и доступность upstream; выйти без запуска MCP.",
    )
    return parser


def _resolve_log_level(cli_value: str | None) -> str:
    """CLI > MCP_ROSREESTR_LOG_LEVEL > legacy ROSREESTR_LOG_LEVEL > INFO."""
    if cli_value is not None:
        return cli_value.upper()
    raw_env = _resolve_env(ENV_LOG_LEVEL)
    if raw_env is not None:
        normalized = raw_env.strip().upper()
        if normalized not in _VALID_LOG_LEVELS:
            print(
                f"atomno-mcp-rosreestr: invalid {ENV_LOG_LEVEL}={raw_env!r} "
                f"(allowed: {', '.join(_VALID_LOG_LEVELS)})",
                file=sys.stderr,
            )
            raise SystemExit(2)
        return normalized
    return "INFO"


def main(argv: list[str] | None = None) -> int:
    """Console entry point with argparse CLI."""
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.check_config:
        return asyncio.run(_check_config_async())

    log_level = _resolve_log_level(args.log_level)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )

    logger.info(
        "atomno-mcp-rosreestr %s starting (transport=%s)",
        __version__,
        args.transport,
    )

    run_kwargs: dict[str, Any] = {"transport": args.transport}
    if args.transport in {"http", "sse", "streamable-http"}:
        run_kwargs["host"] = args.host
        run_kwargs["port"] = args.port
    mcp.run(**run_kwargs)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
