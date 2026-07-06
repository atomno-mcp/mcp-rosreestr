"""Pydantic v2 models describing inputs and outputs of every MCP tool.

Naming and field semantics follow SPEC §5 in
``_knowledge/specs/spec.md``. Numeric fields use ``float`` (not ``Decimal``)
because cadastral data has limited precision and JSON serialisation of
``Decimal`` adds friction for downstream LLM consumers.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_SOURCE = "nspd"

CADASTRAL_NUMBER_RE = re.compile(r"^\d{2}:\d{2}:\d{6,7}:\d+$")


def normalize_cadastral_number(raw: str) -> str:
    """Strip whitespace and validate the canonical ``XX:XX:XXXXXXX:XXXX`` form."""
    if not isinstance(raw, str):
        raise ValueError("cadastral number must be a string")
    cleaned = raw.strip().replace(" ", "")
    if not CADASTRAL_NUMBER_RE.match(cleaned):
        raise ValueError(
            f"invalid cadastral number format: {raw!r};"
            " expected XX:XX:XXXXXXX:XXXX (e.g. 77:01:0001066:1234)"
        )
    return cleaned


ObjectType = Literal[
    "apartment",
    "house",
    "land_plot",
    "garage",
    "non_residential",
    "construction_in_progress",
    "unknown",
]


class RosreestrModel(BaseModel):
    """Base model with strict configuration for safer round-trips."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=False,
        str_strip_whitespace=True,
    )


class CadastralObjectInfo(RosreestrModel):
    """Full set of public characteristics for a single cadastral object."""

    cadastral_number: str = Field(..., description="Канонический КН XX:XX:XXXXXXX:XXXX.")
    object_type: ObjectType = Field(default="unknown")
    object_type_code: str | None = Field(
        default=None,
        description="Raw type code from the upstream (e.g. 'ROOM', 'BUILDING').",
    )
    address: str | None = None
    address_normalized: str | None = None

    area_sqm: float | None = Field(default=None, ge=0)
    floors_total: int | None = Field(default=None, ge=0)
    floor_number: int | None = None
    year_built: int | None = Field(default=None, ge=1700, le=2100)
    construction_series: str | None = None
    wall_material: str | None = None

    cadastral_cost_rub: float | None = Field(default=None, ge=0)
    cadastral_cost_date: _dt.date | None = None

    permitted_use: str | None = Field(
        default=None, description="Free-text ВРИ (вид разрешённого использования)."
    )
    permitted_use_code: str | None = None

    registration_date: _dt.date | None = None
    last_update_date: _dt.date | None = None

    encumbrances_present: bool | None = Field(
        default=None,
        description=(
            "Whether public sources hint at encumbrances (зарегистрированные ограничения)."
            " Open data rarely exposes this — treated as a soft signal."
        ),
    )
    in_egrn: bool = Field(
        default=True,
        description="Whether the object is present in ЕГРН at all.",
    )

    geometry_geojson: dict | None = Field(
        default=None,
        description="GeoJSON polygon if include_geometry=true was requested.",
    )

    source: str = Field(
        default=DEFAULT_SOURCE,
        description="'nspd' (primary) | 'pkk_fallback' | 'cache'.",
    )
    fetched_at: _dt.datetime = Field(default_factory=lambda: _dt.datetime.now(_dt.UTC))

    @field_validator("cadastral_number")
    @classmethod
    def _validate_kn(cls, v: str) -> str:
        return normalize_cadastral_number(v)


class AddressLookupHit(RosreestrModel):
    """One result of an address-to-cadastral lookup."""

    cadastral_number: str
    object_type: ObjectType = "unknown"
    address: str | None = None
    match_score: float = Field(default=1.0, ge=0.0, le=1.0)
    snippet: str | None = None


class AddressLookupResult(RosreestrModel):
    """Aggregate result for ``lookup_by_address``."""

    query: str
    query_normalized: str | None = None
    results: list[AddressLookupHit] = Field(default_factory=list)
    source: str = DEFAULT_SOURCE
    took_ms: int = 0
    warnings: list[str] = Field(default_factory=list)


class PointLookupHit(RosreestrModel):
    """A single object found at the requested geographic point."""

    cadastral_number: str
    object_type: ObjectType
    layer: str = Field(..., description="NSPD layer the hit came from (e.g. 'land_plots').")
    address: str | None = None
    area_sqm: float | None = Field(default=None, ge=0)
    permitted_use: str | None = None


class PointLookupResult(RosreestrModel):
    """Aggregate result for ``lookup_by_coords``."""

    lat: Annotated[float, Field(ge=41.0, le=82.0)]
    lon: Annotated[float, Field(ge=19.0, le=180.0)]
    buffer_meters: float = 5.0
    results: list[PointLookupHit] = Field(default_factory=list)
    source: str = DEFAULT_SOURCE
    took_ms: int = 0


class CadastralValuePoint(RosreestrModel):
    """One observation in the cadastral-value-history series."""

    value_date: _dt.date
    value_rub: float = Field(..., ge=0)
    basis_act: str | None = Field(
        default=None, description="Reference to the cadastral revaluation act, if known."
    )


class CadastralValue(RosreestrModel):
    """Current cadastral value plus the available history series."""

    cadastral_number: str
    current_value_rub: float | None = Field(default=None, ge=0)
    current_value_date: _dt.date | None = None
    history: list[CadastralValuePoint] = Field(default_factory=list)
    source: str = DEFAULT_SOURCE
    note: str | None = None

    @field_validator("cadastral_number")
    @classmethod
    def _validate_kn(cls, v: str) -> str:
        return normalize_cadastral_number(v)
