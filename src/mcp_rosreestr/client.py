"""Async HTTP client over the public NSPD / ПКК endpoints.

NSPD (`nspd.gov.ru`) is the main, actively-maintained public geoportal of
Rosreestr. ПКК (`pkk5.rosreestr.ru`) is the legacy public cadastral map,
kept only as a fallback because it is being phased out.

This module isolates network and parsing concerns. The tool layer in
:mod:`tools` calls these methods, applies caching, and reshapes the data
into the response models defined in :mod:`schemas`.

Note: the upstream JSON shapes are undocumented and known to change. Field
mapping below targets the response shape observed in 2026-04 and is kept
defensive — every accessor uses ``.get()`` with sensible defaults so a
shape change degrades to ``None`` fields instead of an unhandled
KeyError. Contract tests against the live API run separately in CI.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Final

import httpx

from .errors import (
    RosreestrApiError,
    RosreestrNotFoundError,
    RosreestrParseError,
    RosreestrRateLimited,
    RosreestrTimeoutError,
    RosreestrUpstreamUnavailable,
)

logger = logging.getLogger(__name__)

NSPD_BASE_URL: Final[str] = "https://nspd.gov.ru"
PKK_BASE_URL: Final[str] = "https://pkk5.rosreestr.ru"

DEFAULT_TIMEOUT: Final[float] = 15.0
DEFAULT_USER_AGENT: Final[str] = (
    "atomno-mcp-rosreestr/0.1 (+https://github.com/atomno-labs/mcp-rosreestr)"
)


def _parse_iso_date(raw: Any) -> date | None:
    """Best-effort parse of ISO 'YYYY-MM-DD' or DD.MM.YYYY into a ``date``."""
    if raw is None:
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    text = str(raw).strip()
    if not text:
        return None
    head = text.split("T", 1)[0]
    try:
        return date.fromisoformat(head)
    except ValueError:
        if "." in head:
            try:
                day, month, year = head.split(".")
                return date(int(year), int(month), int(day))
            except (ValueError, TypeError):
                return None
        return None


def _parse_float(raw: Any) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, int | float):
        return float(raw)
    text = str(raw).replace(",", ".").strip()
    if not text or text in {"-", "—", "n/a", "None"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_int(raw: Any) -> int | None:
    val = _parse_float(raw)
    return int(val) if val is not None else None


# --- NSPD object-type mapping -------------------------------------------------

# NSPD returns category strings like "Помещение", "Земельный участок" etc.
# We map them to the canonical ObjectType literal used in :mod:`schemas`.
_NSPD_CATEGORY_TO_TYPE: dict[str, str] = {
    "помещение": "apartment",
    "квартира": "apartment",
    "комната": "apartment",
    "здание": "house",
    "жилой дом": "house",
    "земельный участок": "land_plot",
    "сооружение": "non_residential",
    "нежилое помещение": "non_residential",
    "нежилое здание": "non_residential",
    "машиноместо": "garage",
    "гараж": "garage",
    "объект незавершенного строительства": "construction_in_progress",
    "объект незавершённого строительства": "construction_in_progress",
}


def _classify_object_type(category: Any) -> str:
    if not category:
        return "unknown"
    key = str(category).strip().lower()
    return _NSPD_CATEGORY_TO_TYPE.get(key, "unknown")


# --- Client -------------------------------------------------------------------


class RosreestrClient:
    """Stateless wrapper around NSPD with PKK as a fallback.

    Lifecycle is bound to an injected :class:`httpx.AsyncClient` so users
    (and tests) can fully control transports, mounts, retries and timeouts.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            timeout=timeout,
            headers={
                "User-Agent": user_agent,
                "Accept": "application/json,*/*",
            },
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> RosreestrClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ HTTP

    async def _get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        try:
            response = await self._client.get(url, params=params)
        except httpx.TimeoutException as exc:
            raise RosreestrTimeoutError(f"timeout while requesting {url}") from exc
        except httpx.HTTPError as exc:
            raise RosreestrApiError(f"transport error for {url}: {exc}") from exc

        if response.status_code == 404:
            raise RosreestrNotFoundError(f"resource not found at {url}")
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            try:
                retry_seconds = float(retry_after) if retry_after else None
            except ValueError:
                retry_seconds = None
            raise RosreestrRateLimited(
                f"upstream throttled requests at {url}",
                retry_after_sec=retry_seconds,
            )
        if response.status_code >= 500:
            raise RosreestrApiError(
                f"upstream {url} returned HTTP {response.status_code}",
                status_code=response.status_code,
            )
        if response.status_code >= 400:
            raise RosreestrApiError(
                f"upstream {url} returned HTTP {response.status_code}",
                status_code=response.status_code,
            )

        try:
            return response.json()
        except ValueError as exc:
            raise RosreestrParseError(f"non-JSON response from {url}: {exc}") from exc

    # ------------------------------------------------------------------ NSPD

    async def search_nspd(
        self,
        query: str,
        *,
        thematic_search_id: int = 1,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Search NSPD by free-text query (cadastral number or address).

        Returns the raw ``features`` array. Empty list means "not found"
        (we do not raise so the tool layer can decide on graceful fallbacks).
        """
        url = f"{NSPD_BASE_URL}/api/geoportal/v2/search/geoportal"
        params: dict[str, Any] = {
            "thematicSearchId": thematic_search_id,
            "query": query,
            "limit": limit,
        }
        data = await self._get_json(url, params=params)
        features = self._extract_features(data)
        return features

    async def lookup_at_point(
        self,
        lat: float,
        lon: float,
        *,
        layer: str = "36048",
    ) -> list[dict[str, Any]]:
        """Find features at a (lat, lon) point on the given NSPD layer.

        ``layer`` defaults to the cadastral-objects layer. The NSPD layer
        catalogue is undocumented, so this is best-effort. The tool layer
        composes results from multiple layers (parcels + buildings).
        """
        url = (
            f"{NSPD_BASE_URL}/api/geoportal/v2/search/geoportal"
        )
        params: dict[str, Any] = {
            "thematicSearchId": 1,
            "query": f"{lat},{lon}",
            "limit": 10,
        }
        data = await self._get_json(url, params=params)
        return self._extract_features(data)

    @staticmethod
    def _extract_features(data: Any) -> list[dict[str, Any]]:
        """Normalise NSPD response shapes ``{data:{features:[...]}}`` /
        ``{features:[...]}`` / ``[...]``."""
        if isinstance(data, dict):
            if "features" in data and isinstance(data["features"], list):
                return data["features"]
            inner = data.get("data")
            if isinstance(inner, dict) and isinstance(inner.get("features"), list):
                return inner["features"]
            if isinstance(inner, list):
                return inner
        if isinstance(data, list):
            return data
        return []

    # ------------------------------------------------------------------ PKK

    async def fetch_pkk_object(
        self,
        cadastral_number: str,
        *,
        type_id: int = 5,
    ) -> dict[str, Any] | None:
        """Legacy ПКК fallback. ``type_id`` defaults to 5 (помещения)."""
        from urllib.parse import quote

        url = (
            f"{PKK_BASE_URL}/api/features/{type_id}/{quote(cadastral_number)}"
        )
        try:
            data = await self._get_json(url)
        except RosreestrNotFoundError:
            return None
        if isinstance(data, dict) and data.get("status") in {"200", 200}:
            features = data.get("features") or []
            return features[0] if features else None
        return None

    # ----------------------------------------------------------- High-level

    async def get_object_by_cadastral(
        self,
        cadastral_number: str,
    ) -> dict[str, Any]:
        """Fetch raw object metadata, trying NSPD first, then PKK."""
        try:
            features = await self.search_nspd(cadastral_number, limit=1)
        except (RosreestrApiError, RosreestrTimeoutError, RosreestrParseError) as exc:
            logger.warning("NSPD lookup failed for %s: %s; trying PKK", cadastral_number, exc)
            features = []

        if features:
            return {"source": "nspd", "feature": features[0]}

        pkk = await self.fetch_pkk_object(cadastral_number)
        if pkk:
            return {"source": "pkk_fallback", "feature": pkk}

        raise RosreestrNotFoundError(
            f"cadastral object {cadastral_number!r} not found in NSPD or PKK"
        )

    @staticmethod
    def map_feature_to_object(
        cadastral_number: str,
        raw: dict[str, Any],
    ) -> dict[str, Any]:
        """Map an NSPD/PKK ``feature`` dict onto the canonical schema fields.

        The upstream shape is ``{type, geometry, properties: {...}, ...}``
        with properties variants between layers and APIs. We use defensive
        getters so partial data still produces a usable record.
        """
        source = raw.get("source", "nspd")
        feature = raw.get("feature", raw)
        props = feature.get("properties") or feature.get("attrs") or feature
        options = props.get("options") if isinstance(props, dict) else None
        if isinstance(options, dict):
            merged = {**props, **options}
        else:
            merged = dict(props) if isinstance(props, dict) else {}

        category = (
            merged.get("category_type")
            or merged.get("categoryType")
            or merged.get("category")
            or merged.get("type")
            or merged.get("oks_type")
        )

        cad_cost = _parse_float(
            merged.get("cad_cost")
            or merged.get("cadastralCost")
            or merged.get("kadastrovaya_stoimost")
        )
        cad_cost_date = _parse_iso_date(
            merged.get("cad_cost_date")
            or merged.get("cadastralCostDate")
            or merged.get("data_kadastrovoy_stoimosti")
        )
        area = _parse_float(
            merged.get("area_value") or merged.get("area") or merged.get("specified_area")
        )
        permitted_use = (
            merged.get("permitted_use_established_by_document")
            or merged.get("permittedUse")
            or merged.get("vid_ispolzovaniya")
        )
        address = (
            merged.get("readable_address")
            or merged.get("address")
            or merged.get("address_value")
            or merged.get("addr")
        )
        registration_date = _parse_iso_date(
            merged.get("registration_date") or merged.get("date_registration")
        )
        year_built = _parse_int(
            merged.get("year_built")
            or merged.get("year_construction")
            or merged.get("yearOfCommissioning")
        )

        return {
            "cadastral_number": cadastral_number,
            "object_type": _classify_object_type(category),
            "object_type_code": str(category) if category else None,
            "address": str(address).strip() if address else None,
            "area_sqm": area,
            "year_built": year_built,
            "cadastral_cost_rub": cad_cost,
            "cadastral_cost_date": cad_cost_date,
            "permitted_use": str(permitted_use).strip() if permitted_use else None,
            "registration_date": registration_date,
            "in_egrn": True,
            "source": source,
        }


__all__ = [
    "DEFAULT_TIMEOUT",
    "DEFAULT_USER_AGENT",
    "NSPD_BASE_URL",
    "PKK_BASE_URL",
    "RosreestrClient",
    "RosreestrUpstreamUnavailable",
]
