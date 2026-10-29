"""pytest 公共配置：确保 procurement_agent 库已建表并有种子数据。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))


@pytest.fixture(scope="session", autouse=True)
def ensure_seed_data():
    from backend.models.db import get_connection

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM purchase_orders")
            n = cur.fetchone()["c"]
    if n == 0:
        from database.seed_data import seed

        seed()
    yield