"""pytest 公共夹具：把 JSON 数据文件指到临时目录，用例之间互不污染。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store import store


@pytest.fixture()
def client(tmp_path):
    store._path = tmp_path / "platform.json"
    store.reset()
    with TestClient(app) as test_client:
        yield test_client
    store.reset()
