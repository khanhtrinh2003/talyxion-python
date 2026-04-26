from __future__ import annotations

import pytest

from talyxion import Talyxion

BASE_URL = "https://api.test.talyxion.com"
API_KEY = "tk_test_dummy_key_value"


@pytest.fixture()
def base_url() -> str:
    return BASE_URL


@pytest.fixture()
def api_key() -> str:
    return API_KEY


@pytest.fixture()
def client(base_url: str, api_key: str) -> Talyxion:
    return Talyxion(api_key=api_key, base_url=base_url, max_retries=0)
