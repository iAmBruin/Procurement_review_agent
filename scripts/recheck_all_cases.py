"""批量核对：遍历全部采购单，重新执行核对并写入异常。"""
from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from backend.models.db import get_connection
from backend.services import order_service


def main():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, po_number FROM purchase_orders ORDER BY id")
            rows = cur.fetchall()

    total = 0
    for row in rows:
        po_id = row["id"]
        result = order_service.run_check(po_id)
        total += len(result.exceptions)
        print(f"{row['po_number']}: {len(result.exceptions)} exceptions")

    print("TOTAL_EXCEPTIONS:", total)


if __name__ == "__main__":
    main()