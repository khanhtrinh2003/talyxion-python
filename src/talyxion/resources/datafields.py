"""`/api/v1/datafields/` and `/api/v1/datafields/<key>/`."""

from __future__ import annotations

from ..models.datafield import Datafield, DatafieldDetail
from ._base import Resource, extract_data


class DatafieldsResource(Resource):
    def list(self) -> list[Datafield]:
        body = self._http.get("/api/v1/datafields/")
        items = extract_data(body) or []
        return [Datafield.model_validate(it) for it in items]

    def get(self, key: str) -> DatafieldDetail:
        body = self._http.get(f"/api/v1/datafields/{key}/")
        field = body.get("field") or {"key": key, "label": key}
        return DatafieldDetail.model_validate({**field, "data": extract_data(body)})
