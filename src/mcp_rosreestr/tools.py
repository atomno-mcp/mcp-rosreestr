"""High-level MCP tools that compose the upstream client with the SQLite cache.

Each public function below corresponds to one MCP tool. They take primitive
arguments (``str``, ``float``, ``int``, ``bool``) so the JSON schema generated
by FastMCP stays compact, validate them through Pydantic, and return the
typed response models from :mod:`schemas`.

The four tools below cover SPEC §5.1–§5.4 (FR-001 through FR-004) — the open
MIT lookup surface. Pro tools (`summarize_property_risks`, `order_egrn_extract`,
`batch_check_properties`) live behind ``MCP_ROSREESTR_API_KEY`` and will be
added in v0.2 (see roadmap Phase 3).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .cache import SqliteCache
from .client import RosreestrClient
from .errors import (
    RosreestrNotFoundError,
    RosreestrValidationError,
)
from .schemas import (
    AddressLookupHit,
    AddressLookupResult,
    CadastralObjectInfo,
    CadastralValue,
    CadastralValuePoint,
    PointLookupHit,
    PointLookupResult,
    normalize_cadastral_number,
)

logger = logging.getLogger(__name__)

DEFAULT_TTL_LIVE_SECONDS: int = 24 * 3600
DEFAULT_TTL_STATIC_SECONDS: int = 7 * 24 * 3600


@dataclass(slots=True)
class ToolContext:
    """Bag of dependencies threaded through every tool call."""

    client: RosreestrClient
    cache: SqliteCache
    ttl_live_seconds: int = DEFAULT_TTL_LIVE_SECONDS
    ttl_static_seconds: int = DEFAULT_TTL_STATIC_SECONDS


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- 5.1 cadastral


async def lookup_by_cadastral(
    ctx: ToolContext,
    cadastral_number: str,
    *,
    include_geometry: bool = False,
) -> CadastralObjectInfo:
    """Get full public characteristics of a cadastral object."""
    started = time.monotonic()
    try:
        kn = normalize_cadastral_number(cadastral_number)
    except ValueError as exc:
        raise RosreestrValidationError(str(exc)) from exc

    cached = ctx.cache.get_object(kn)
    if cached is not None and not include_geometry:
        cache_meta = cached.pop("_cache", None)
        cached.setdefault("source", (cache_meta or {}).get("source", "cache"))
        try:
            return CadastralObjectInfo.model_validate(cached)
        except Exception:
            logger.warning("cache for %s is corrupt; refetching", kn)

    raw = await ctx.client.get_object_by_cadastral(kn)
    mapped = ctx.client.map_feature_to_object(kn, raw)
    info = CadastralObjectInfo.model_validate(mapped)

    cache_until = _now_utc() + timedelta(
        seconds=ctx.ttl_live_seconds if info.cadastral_cost_rub else ctx.ttl_static_seconds
    )
    ctx.cache.set_object(
        kn,
        info.model_dump(mode="json"),
        source=info.source,
        cache_until=cache_until,
    )
    if info.cadastral_cost_rub and info.cadastral_cost_date:
        ctx.cache.add_value_point(
            kn,
            info.cadastral_cost_date.isoformat(),
            info.cadastral_cost_rub,
        )

    elapsed_ms = int((time.monotonic() - started) * 1000)
    ctx.cache.log_call(
        tool_name="lookup_by_cadastral",
        args_hash=kn,
        cache_hit=False,
        upstream_used=info.source,
        took_ms=elapsed_ms,
        success=True,
    )
    return info


# ----------------------------------------------------------------- 5.2 address


async def lookup_by_address(
    ctx: ToolContext,
    address: str,
    *,
    limit: int = 5,
    object_types: list[str] | None = None,
) -> AddressLookupResult:
    """Find cadastral objects matching an address. Returns up to ``limit`` hits."""
    started = time.monotonic()
    if not address or len(address.strip()) < 8:
        raise RosreestrValidationError(
            "address must contain at least 8 characters; got %r" % address
        )
    if not 1 <= int(limit) <= 20:
        raise RosreestrValidationError("limit must be between 1 and 20")

    query = address.strip()
    object_types = object_types or []

    cached = ctx.cache.get_address(query, object_types)
    if cached is not None:
        try:
            return AddressLookupResult.model_validate(cached)
        except Exception:
            logger.warning("address cache for %r is corrupt; refetching", query)

    features = await ctx.client.search_nspd(query, limit=limit)

    hits: list[AddressLookupHit] = []
    for feature in features:
        props = feature.get("properties") or feature.get("attrs") or {}
        kn = (
            props.get("cad_num")
            or props.get("cadastralNumber")
            or props.get("kadastrovyy_nomer")
            or props.get("number")
        )
        if not kn:
            continue
        try:
            kn_norm = normalize_cadastral_number(str(kn))
        except ValueError:
            continue
        category = (
            props.get("category_type")
            or props.get("categoryType")
            or props.get("category")
        )
        from .client import _classify_object_type
        hits.append(
            AddressLookupHit(
                cadastral_number=kn_norm,
                object_type=_classify_object_type(category),  # type: ignore[arg-type]
                address=str(props.get("readable_address") or props.get("address") or "").strip()
                or None,
                match_score=1.0,
                snippet=None,
            )
        )

    result = AddressLookupResult(
        query=query,
        query_normalized=query,
        results=hits,
        source="nspd",
        took_ms=int((time.monotonic() - started) * 1000),
        warnings=[] if hits else ["address not found in NSPD; consider DaData fallback"],
    )

    cache_until = _now_utc() + timedelta(seconds=ctx.ttl_static_seconds * 4)  # ~30 days
    ctx.cache.set_address(
        query,
        result.model_dump(mode="json"),
        source="nspd",
        cache_until=cache_until,
        object_types=object_types,
    )
    ctx.cache.log_call(
        tool_name="lookup_by_address",
        args_hash=query,
        cache_hit=False,
        upstream_used="nspd",
        took_ms=result.took_ms,
        success=True,
    )
    return result


# ------------------------------------------------------------------ 5.3 coords


async def lookup_by_coords(
    ctx: ToolContext,
    lat: float,
    lon: float,
    *,
    buffer_meters: float = 5.0,
    object_types: list[str] | None = None,
) -> PointLookupResult:
    """Show cadastral objects under a (lat, lon) point on the public map."""
    started = time.monotonic()
    if not 41.0 <= lat <= 82.0:
        raise RosreestrValidationError("lat must be in [41.0, 82.0] for RU territory")
    if not 19.0 <= lon <= 180.0:
        raise RosreestrValidationError("lon must be in [19.0, 180.0] for RU territory")
    if not 0.5 <= buffer_meters <= 200.0:
        raise RosreestrValidationError("buffer_meters must be in [0.5, 200]")

    features = await ctx.client.lookup_at_point(lat, lon)
    from .client import _classify_object_type

    hits: list[PointLookupHit] = []
    for feature in features:
        props = feature.get("properties") or feature.get("attrs") or {}
        kn = (
            props.get("cad_num")
            or props.get("cadastralNumber")
            or props.get("kadastrovyy_nomer")
        )
        if not kn:
            continue
        try:
            kn_norm = normalize_cadastral_number(str(kn))
        except ValueError:
            continue
        category = props.get("category_type") or props.get("category")
        layer = feature.get("layer") or feature.get("layerName") or "unknown"
        from .client import _parse_float

        hits.append(
            PointLookupHit(
                cadastral_number=kn_norm,
                object_type=_classify_object_type(category),  # type: ignore[arg-type]
                layer=str(layer),
                address=str(props.get("readable_address") or props.get("address") or "").strip()
                or None,
                area_sqm=_parse_float(props.get("area_value") or props.get("area")),
                permitted_use=str(
                    props.get("permitted_use_established_by_document")
                    or props.get("permittedUse")
                    or ""
                ).strip()
                or None,
            )
        )

    return PointLookupResult(
        lat=lat,
        lon=lon,
        buffer_meters=buffer_meters,
        results=hits,
        source="nspd",
        took_ms=int((time.monotonic() - started) * 1000),
    )


# ------------------------------------------------------------------ 5.4 value


async def get_cadastral_value(
    ctx: ToolContext,
    cadastral_number: str,
) -> CadastralValue:
    """Return the current cadastral value plus any cached revaluation history."""
    try:
        kn = normalize_cadastral_number(cadastral_number)
    except ValueError as exc:
        raise RosreestrValidationError(str(exc)) from exc

    info = await lookup_by_cadastral(ctx, kn)
    history_rows = ctx.cache.get_value_history(kn)
    history = [
        CadastralValuePoint(
            value_date=_safe_iso_date(row["value_date"]),
            value_rub=float(row["value_rub"]),
            basis_act=row.get("basis_act"),
        )
        for row in history_rows
        if row.get("value_date")
    ]
    note = (
        None
        if history
        else (
            "Open NSPD/PKK do not expose full revaluation history;"
            " only the current value is shown. Use Pro `summarize_property_risks`"
            " or order an EGRN extract to get the audit trail."
        )
    )
    return CadastralValue(
        cadastral_number=kn,
        current_value_rub=info.cadastral_cost_rub,
        current_value_date=info.cadastral_cost_date,
        history=history,
        source=info.source,
        note=note,
    )


def _safe_iso_date(raw: object):
    from datetime import date

    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return date.today()


__all__ = [
    "DEFAULT_TTL_LIVE_SECONDS",
    "DEFAULT_TTL_STATIC_SECONDS",
    "ToolContext",
    "get_cadastral_value",
    "lookup_by_address",
    "lookup_by_cadastral",
    "lookup_by_coords",
]
