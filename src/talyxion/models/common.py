"""Shared model primitives."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T", bound=BaseModel)


class ResponseMeta(BaseModel):
    model_config = ConfigDict(extra="allow")

    timestamp: str | None = None
    request_id: str | None = None


class Pagination(BaseModel):
    total: int
    limit: int
    offset: int


class Page(BaseModel, Generic[T]):
    """Cursor-less offset/limit page returned by list endpoints."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    items: list[T] = Field(default_factory=list)
    pagination: Pagination
    meta: ResponseMeta | None = None

    # Loader injected by the resource so iter_all() can fetch the next page.
    _loader: Callable[[int, int], Page[T]] | None = None

    def __iter__(self) -> Iterator[T]:  # type: ignore[override]
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, idx: int) -> T:
        return self.items[idx]

    @property
    def has_next(self) -> bool:
        return self.pagination.offset + len(self.items) < self.pagination.total

    def with_loader(self, loader: Callable[[int, int], Page[T]]) -> Page[T]:
        object.__setattr__(self, "_loader", loader)
        return self

    def iter_all(self) -> Iterator[T]:
        """Yield every item across pages, fetching as needed."""
        page: Page[T] = self
        while True:
            yield from page.items
            if not page.has_next or page._loader is None:
                return
            next_offset = page.pagination.offset + page.pagination.limit
            page = page._loader(page.pagination.limit, next_offset)

    def to_dataframe(self) -> Any:
        """Convert items to a pandas DataFrame. Requires the `pandas` extra."""
        try:
            import pandas as pd
        except ImportError as exc:
            raise ImportError(
                "pandas is required for to_dataframe(). Install with `pip install talyxion[pandas]`."
            ) from exc
        return pd.DataFrame([item.model_dump() for item in self.items])
