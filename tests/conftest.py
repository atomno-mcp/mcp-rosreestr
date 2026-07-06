"""Shared pytest fixtures for ``mcp-rosreestr``."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from mcp_rosreestr.cache import SqliteCache
from mcp_rosreestr.client import RosreestrClient
from mcp_rosreestr.tools import ToolContext

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


def load_fixture_json(name: str):
    return json.loads(load_fixture(name).decode("utf-8"))


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest_asyncio.fixture
async def http_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        timeout=5.0,
        headers={"User-Agent": "atomno-mcp-rosreestr/test"},
    ) as client:
        yield client


@pytest_asyncio.fixture
async def rr_client(http_client: httpx.AsyncClient) -> AsyncIterator[RosreestrClient]:
    client = RosreestrClient(http_client=http_client)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def sqlite_cache(tmp_path: Path) -> SqliteCache:
    cache = SqliteCache(tmp_path / "cache.sqlite")
    yield cache
    cache.close()


@pytest_asyncio.fixture
async def tool_ctx(
    rr_client: RosreestrClient,
    sqlite_cache: SqliteCache,
) -> AsyncIterator[ToolContext]:
    yield ToolContext(client=rr_client, cache=sqlite_cache)
