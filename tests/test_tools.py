"""End-to-end tests of the MCP tool layer (client + cache composition)."""

from __future__ import annotations

import pytest
import respx

from mcp_rosreestr.client import NSPD_BASE_URL, PKK_BASE_URL
from mcp_rosreestr.errors import RosreestrNotFoundError, RosreestrValidationError
from mcp_rosreestr.tools import (
    ToolContext,
    get_cadastral_value,
    lookup_by_address,
    lookup_by_cadastral,
    lookup_by_coords,
)
from tests.conftest import load_fixture


# ---------------------------------------------------------------- 5.1 cadastral


@pytest.mark.asyncio
async def test_lookup_by_cadastral_happy_path(tool_ctx: ToolContext) -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{NSPD_BASE_URL}/api/geoportal/v2/search/geoportal").respond(
            200, content=load_fixture("nspd_apartment.json")
        )
        info = await lookup_by_cadastral(tool_ctx, "77:01:0001066:1234")

    assert info.cadastral_number == "77:01:0001066:1234"
    assert info.object_type == "apartment"
    assert info.area_sqm == 47.3
    assert info.cadastral_cost_rub == 8_423_000.0
    assert info.source == "nspd"


@pytest.mark.asyncio
async def test_lookup_by_cadastral_uses_cache_on_second_call(tool_ctx: ToolContext) -> None:
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(f"{NSPD_BASE_URL}/api/geoportal/v2/search/geoportal").respond(
            200, content=load_fixture("nspd_apartment.json")
        )
        await lookup_by_cadastral(tool_ctx, "77:01:0001066:1234")
        await lookup_by_cadastral(tool_ctx, "77:01:0001066:1234")
        assert route.call_count == 1


@pytest.mark.asyncio
async def test_lookup_by_cadastral_invalid_raises_validation(tool_ctx: ToolContext) -> None:
    with pytest.raises(RosreestrValidationError):
        await lookup_by_cadastral(tool_ctx, "not-a-cadastral-number")


@pytest.mark.asyncio
async def test_lookup_by_cadastral_not_found_propagates(tool_ctx: ToolContext) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{NSPD_BASE_URL}/api/geoportal/v2/search/geoportal").respond(
            200, content=load_fixture("nspd_empty.json")
        )
        mock.get(f"{PKK_BASE_URL}/api/features/5/99:99:9999999:9999").respond(
            200, json={"status": 200, "features": []}
        )
        with pytest.raises(RosreestrNotFoundError):
            await lookup_by_cadastral(tool_ctx, "99:99:9999999:9999")


# ----------------------------------------------------------------- 5.2 address


@pytest.mark.asyncio
async def test_lookup_by_address_returns_hits(tool_ctx: ToolContext) -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{NSPD_BASE_URL}/api/geoportal/v2/search/geoportal").respond(
            200, content=load_fixture("nspd_apartment.json")
        )
        result = await lookup_by_address(tool_ctx, "Москва, Ленинский 10, кв. 5")

    assert len(result.results) == 1
    assert result.results[0].cadastral_number == "77:01:0001066:1234"
    assert result.results[0].object_type == "apartment"
    assert result.warnings == []


@pytest.mark.asyncio
async def test_lookup_by_address_empty_returns_warning(tool_ctx: ToolContext) -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{NSPD_BASE_URL}/api/geoportal/v2/search/geoportal").respond(
            200, content=load_fixture("nspd_empty.json")
        )
        result = await lookup_by_address(tool_ctx, "Несуществующий 1, кв 99")

    assert result.results == []
    assert result.warnings and "DaData" in result.warnings[0]


@pytest.mark.asyncio
async def test_lookup_by_address_short_query_raises(tool_ctx: ToolContext) -> None:
    with pytest.raises(RosreestrValidationError):
        await lookup_by_address(tool_ctx, "Hi")


@pytest.mark.asyncio
async def test_lookup_by_address_uses_cache(tool_ctx: ToolContext) -> None:
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(f"{NSPD_BASE_URL}/api/geoportal/v2/search/geoportal").respond(
            200, content=load_fixture("nspd_apartment.json")
        )
        await lookup_by_address(tool_ctx, "Москва, Ленинский 10, кв. 5")
        await lookup_by_address(tool_ctx, "Москва, Ленинский 10, кв. 5")
        assert route.call_count == 1


# ------------------------------------------------------------------ 5.3 coords


@pytest.mark.asyncio
async def test_lookup_by_coords_validates_lat_lon(tool_ctx: ToolContext) -> None:
    with pytest.raises(RosreestrValidationError):
        await lookup_by_coords(tool_ctx, lat=10.0, lon=37.0)
    with pytest.raises(RosreestrValidationError):
        await lookup_by_coords(tool_ctx, lat=55.0, lon=10.0)
    with pytest.raises(RosreestrValidationError):
        await lookup_by_coords(tool_ctx, lat=55.0, lon=37.0, buffer_meters=0.0)


@pytest.mark.asyncio
async def test_lookup_by_coords_returns_hits(tool_ctx: ToolContext) -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{NSPD_BASE_URL}/api/geoportal/v2/search/geoportal").respond(
            200, content=load_fixture("nspd_land_plot.json")
        )
        result = await lookup_by_coords(tool_ctx, lat=55.701, lon=37.555)

    assert result.lat == 55.701
    assert result.lon == 37.555
    assert len(result.results) == 1
    assert result.results[0].cadastral_number == "50:21:0010101:42"


# ------------------------------------------------------------------- 5.4 value


@pytest.mark.asyncio
async def test_get_cadastral_value_returns_current_and_history(tool_ctx: ToolContext) -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{NSPD_BASE_URL}/api/geoportal/v2/search/geoportal").respond(
            200, content=load_fixture("nspd_apartment.json")
        )
        value = await get_cadastral_value(tool_ctx, "77:01:0001066:1234")

    assert value.cadastral_number == "77:01:0001066:1234"
    assert value.current_value_rub == 8_423_000.0
    assert value.current_value_date is not None
    assert len(value.history) == 1
    assert value.history[0].value_rub == 8_423_000.0
