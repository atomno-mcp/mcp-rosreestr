"""Unit tests for the upstream client (NSPD / PKK) using respx mocks."""

from __future__ import annotations

import httpx
import pytest
import respx

from mcp_rosreestr.client import (
    NSPD_BASE_URL,
    PKK_BASE_URL,
    RosreestrClient,
    _classify_object_type,
)
from mcp_rosreestr.errors import (
    RosreestrApiError,
    RosreestrNotFoundError,
    RosreestrRateLimited,
    RosreestrTimeoutError,
)
from tests.conftest import load_fixture, load_fixture_json


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Помещение", "apartment"),
        ("ЗЕМЕЛЬНЫЙ УЧАСТОК", "land_plot"),
        ("Машиноместо", "garage"),
        ("Сооружение", "non_residential"),
        ("Объект незавершенного строительства", "construction_in_progress"),
        ("чёрный квадрат", "unknown"),
        (None, "unknown"),
        ("", "unknown"),
    ],
)
def test_classify_object_type(raw, expected) -> None:
    assert _classify_object_type(raw) == expected


@pytest.mark.asyncio
async def test_search_nspd_extracts_features_data_envelope(rr_client: RosreestrClient) -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{NSPD_BASE_URL}/api/geoportal/v2/search/geoportal").respond(
            200, content=load_fixture("nspd_apartment.json")
        )
        features = await rr_client.search_nspd("77:01:0001066:1234")
    assert len(features) == 1
    assert features[0]["properties"]["cad_num"] == "77:01:0001066:1234"


@pytest.mark.asyncio
async def test_search_nspd_extracts_flat_features(rr_client: RosreestrClient) -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{NSPD_BASE_URL}/api/geoportal/v2/search/geoportal").respond(
            200, content=load_fixture("nspd_land_plot.json")
        )
        features = await rr_client.search_nspd("50:21:0010101:42")
    assert len(features) == 1
    assert features[0]["properties"]["cad_num"] == "50:21:0010101:42"


@pytest.mark.asyncio
async def test_search_nspd_empty_returns_empty_list(rr_client: RosreestrClient) -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{NSPD_BASE_URL}/api/geoportal/v2/search/geoportal").respond(
            200, content=load_fixture("nspd_empty.json")
        )
        features = await rr_client.search_nspd("99:99:9999999:9999")
    assert features == []


@pytest.mark.asyncio
async def test_get_object_by_cadastral_falls_back_to_pkk(rr_client: RosreestrClient) -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{NSPD_BASE_URL}/api/geoportal/v2/search/geoportal").respond(
            200, content=load_fixture("nspd_empty.json")
        )
        mock.get(f"{PKK_BASE_URL}/api/features/5/77:01:0001066:1234").respond(
            200,
            json={
                "status": 200,
                "features": [
                    {
                        "type": "Feature",
                        "attrs": {
                            "cn": "77:01:0001066:1234",
                            "address": "г. Москва, ул. Тестовая",
                            "area_value": "47.3",
                            "cad_cost": "8000000",
                        },
                    }
                ],
            },
        )
        raw = await rr_client.get_object_by_cadastral("77:01:0001066:1234")
    assert raw["source"] == "pkk_fallback"
    assert raw["feature"]["attrs"]["cn"] == "77:01:0001066:1234"


@pytest.mark.asyncio
async def test_get_object_by_cadastral_raises_not_found(rr_client: RosreestrClient) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{NSPD_BASE_URL}/api/geoportal/v2/search/geoportal").respond(
            200, content=load_fixture("nspd_empty.json")
        )
        mock.get(f"{PKK_BASE_URL}/api/features/5/99:99:9999999:9999").respond(
            200, json={"status": 200, "features": []}
        )
        with pytest.raises(RosreestrNotFoundError):
            await rr_client.get_object_by_cadastral("99:99:9999999:9999")


@pytest.mark.asyncio
async def test_search_nspd_4xx_becomes_api_error(rr_client: RosreestrClient) -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{NSPD_BASE_URL}/api/geoportal/v2/search/geoportal").respond(400)
        with pytest.raises(RosreestrApiError):
            await rr_client.search_nspd("garbage")


@pytest.mark.asyncio
async def test_search_nspd_5xx_becomes_api_error(rr_client: RosreestrClient) -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{NSPD_BASE_URL}/api/geoportal/v2/search/geoportal").respond(503)
        with pytest.raises(RosreestrApiError) as exc:
            await rr_client.search_nspd("77:01:0001066:1234")
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_search_nspd_429_becomes_rate_limited(rr_client: RosreestrClient) -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{NSPD_BASE_URL}/api/geoportal/v2/search/geoportal").respond(
            429, headers={"Retry-After": "30"}
        )
        with pytest.raises(RosreestrRateLimited) as exc:
            await rr_client.search_nspd("77:01:0001066:1234")
    assert exc.value.retry_after_sec == 30.0


@pytest.mark.asyncio
async def test_search_nspd_timeout_becomes_timeout_error(rr_client: RosreestrClient) -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{NSPD_BASE_URL}/api/geoportal/v2/search/geoportal").mock(
            side_effect=httpx.TimeoutException("slow upstream")
        )
        with pytest.raises(RosreestrTimeoutError):
            await rr_client.search_nspd("77:01:0001066:1234")


def test_map_feature_to_object_apartment_canonical() -> None:
    raw = {
        "source": "nspd",
        "feature": load_fixture_json("nspd_apartment.json")["data"]["features"][0],
    }
    mapped = RosreestrClient.map_feature_to_object("77:01:0001066:1234", raw)
    assert mapped["cadastral_number"] == "77:01:0001066:1234"
    assert mapped["object_type"] == "apartment"
    assert mapped["address"] == "г. Москва, Ленинский проспект, д. 10, кв. 5"
    assert mapped["area_sqm"] == 47.3
    assert mapped["cadastral_cost_rub"] == 8_423_000.0
    assert mapped["year_built"] == 1957
    assert mapped["source"] == "nspd"


def test_map_feature_to_object_land_plot() -> None:
    raw = {
        "source": "nspd",
        "feature": load_fixture_json("nspd_land_plot.json")["features"][0],
    }
    mapped = RosreestrClient.map_feature_to_object("50:21:0010101:42", raw)
    assert mapped["object_type"] == "land_plot"
    assert mapped["area_sqm"] == 1500.0
    assert mapped["permitted_use"].startswith("Для индивидуального")
