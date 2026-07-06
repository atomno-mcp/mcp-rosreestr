"""Pure-Python tests for Pydantic schemas and validation helpers."""

from __future__ import annotations

import pytest

from mcp_rosreestr.schemas import (
    AddressLookupResult,
    CadastralObjectInfo,
    normalize_cadastral_number,
)


def test_normalize_cadastral_number_accepts_canonical() -> None:
    assert normalize_cadastral_number("77:01:0001066:1234") == "77:01:0001066:1234"


def test_normalize_cadastral_number_strips_whitespace() -> None:
    assert normalize_cadastral_number(" 77:01:0001066:1234 ") == "77:01:0001066:1234"


def test_normalize_cadastral_number_accepts_seven_digit_block() -> None:
    assert normalize_cadastral_number("50:21:0010101:42") == "50:21:0010101:42"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "abc",
        "77-01-0001066-1234",
        "77:01:1234",
        "777:01:0001066:1234",
    ],
)
def test_normalize_cadastral_number_rejects_garbage(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_cadastral_number(raw)


def test_cadastral_object_info_validates_kn_field() -> None:
    info = CadastralObjectInfo(cadastral_number="77:01:0001066:1234")
    assert info.cadastral_number == "77:01:0001066:1234"
    assert info.in_egrn is True
    assert info.source == "nspd"


def test_cadastral_object_info_rejects_extra_fields() -> None:
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError variant
        CadastralObjectInfo(cadastral_number="77:01:0001066:1234", what="ever")


def test_address_lookup_result_default_warnings_empty() -> None:
    result = AddressLookupResult(query="Москва Ленинский 10")
    assert result.results == []
    assert result.warnings == []
