"""Internal helpers shared by resource classes."""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

from .._http import HttpClient
from ..errors import TalyxionResponseError
from ..models.common import Page, Pagination, ResponseMeta

M = TypeVar("M", bound=BaseModel)


class Resource:
    def __init__(self, http: HttpClient) -> None:
        self._http = http


def extract_data(body: dict[str, Any]) -> Any:
    if "data" not in body:
        raise TalyxionResponseError(f"Response missing 'data' field: keys={list(body)[:5]}")
    return body["data"]


def parse_meta(body: dict[str, Any]) -> ResponseMeta | None:
    raw = body.get("meta")
    if isinstance(raw, dict):
        return ResponseMeta.model_validate(raw)
    return None


def parse_pagination(body: dict[str, Any]) -> Pagination:
    raw = body.get("pagination") or {}
    if not isinstance(raw, dict):
        raise TalyxionResponseError("Response 'pagination' must be an object.")
    try:
        return Pagination.model_validate(raw)
    except Exception as exc:
        raise TalyxionResponseError(f"Invalid pagination payload: {exc}") from exc


def build_page(body: dict[str, Any], item_cls: type[M], items_raw: list[Any]) -> Page[M]:
    items = [item_cls.model_validate(it) for it in items_raw]
    return Page[M](items=items, pagination=parse_pagination(body), meta=parse_meta(body))
