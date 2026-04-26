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


class DatafieldDetail(BaseModel):
    """Wrapper around the raw `data` payload for a single datafield."""

    model_config = ConfigDict(extra="allow")

    key: str
    label: str
    data: Any = None
