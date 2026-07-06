"""SQLite cache round-trip tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mcp_rosreestr.cache import SqliteCache


def _future(seconds: int = 3600) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def _past(seconds: int = 3600) -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=seconds)


def test_set_and_get_object_roundtrip(sqlite_cache: SqliteCache) -> None:
    payload = {"cadastral_number": "77:01:0001066:1234", "area_sqm": 47.3}
    sqlite_cache.set_object(
        "77:01:0001066:1234",
        payload,
        source="nspd",
        cache_until=_future(),
    )
    got = sqlite_cache.get_object("77:01:0001066:1234")
    assert got is not None
    assert got["cadastral_number"] == "77:01:0001066:1234"
    assert got["area_sqm"] == 47.3
    assert got["_cache"]["source"] == "nspd"


def test_get_object_returns_none_when_expired(sqlite_cache: SqliteCache) -> None:
    sqlite_cache.set_object(
        "77:01:0001066:1234",
        {"cadastral_number": "77:01:0001066:1234"},
        source="nspd",
        cache_until=_past(),
    )
    assert sqlite_cache.get_object("77:01:0001066:1234") is None


def test_address_cache_keyed_by_object_types(sqlite_cache: SqliteCache) -> None:
    payload = {"query": "Москва Ленинский 10", "results": []}
    sqlite_cache.set_address(
        "Москва Ленинский 10",
        payload,
        source="nspd",
        cache_until=_future(),
        object_types=["apartment"],
    )
    assert sqlite_cache.get_address("Москва Ленинский 10", ["apartment"]) is not None
    assert sqlite_cache.get_address("Москва Ленинский 10", ["land_plot"]) is None
    assert sqlite_cache.get_address("Москва Ленинский 10", []) is None


def test_value_history_dedupes_same_date(sqlite_cache: SqliteCache) -> None:
    sqlite_cache.add_value_point("77:01:0001066:1234", "2025-01-01", 8_000_000.0)
    sqlite_cache.add_value_point("77:01:0001066:1234", "2025-01-01", 9_999_999.0)
    sqlite_cache.add_value_point("77:01:0001066:1234", "2026-01-01", 8_500_000.0)

    rows = sqlite_cache.get_value_history("77:01:0001066:1234")
    assert len(rows) == 2
    assert rows[0]["value_date"] == "2025-01-01"
    assert rows[0]["value_rub"] == 8_000_000.0
    assert rows[1]["value_date"] == "2026-01-01"


def test_log_call_and_stats(sqlite_cache: SqliteCache) -> None:
    sqlite_cache.log_call(
        tool_name="lookup_by_cadastral",
        args_hash="77:01:0001066:1234",
        cache_hit=False,
        upstream_used="nspd",
        took_ms=120,
        success=True,
    )
    stats = sqlite_cache.stats()
    assert stats["audit_entries"] == 1
    assert stats["objects"] == 0


def test_prune_removes_only_expired(sqlite_cache: SqliteCache) -> None:
    sqlite_cache.set_object(
        "77:01:0001066:1111",
        {"cadastral_number": "77:01:0001066:1111"},
        source="nspd",
        cache_until=_past(),
    )
    sqlite_cache.set_object(
        "77:01:0001066:2222",
        {"cadastral_number": "77:01:0001066:2222"},
        source="nspd",
        cache_until=_future(),
    )
    removed = sqlite_cache.prune()
    assert removed == 1
    assert sqlite_cache.get_object("77:01:0001066:2222") is not None
