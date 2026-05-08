"""API status model matching `main/api/v1/views.py::api_status`."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ApiStatus(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    key_name: str
    key_prefix: str
    tier: str
    scopes: list[str] = Field(default_factory=list)
    daily_quota: int
    requests_today: int
    ip_whitelist_active: bool
