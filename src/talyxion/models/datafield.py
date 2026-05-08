"""Datafield models matching `main/api/v1/views.py::datafields_list/detail`."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class Datafield(BaseModel):
    model_config = ConfigDict(extra="allow")

    key: str
    label: str
    category: str | None = None
    description: str | None = None
    min_tier: str | None = None
    # Phase 1 of the rename rollout: APIs may emit `aliases` (legacy names that
    # still resolve to this datafield) and `legacy_key` (the most prominent
    # alias). Defaults preserve forward compatibility for older SDK responses.
    aliases: list[str] = []
    legacy_key: str | None = None
    deprecated: bool = False


class DatafieldDetail(BaseModel):
    """Wrapper around the raw `data` payload for a single datafield."""

    model_config = ConfigDict(extra="allow")

    key: str
    label: str
    data: Any = None
