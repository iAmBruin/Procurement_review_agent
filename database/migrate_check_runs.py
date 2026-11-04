"""数据库迁移：引入核对批次（check_runs）与 latest_check_run_id / check_run_id。

用法：
  python -m database.migrate_check_runs

幂等：可重复执行。对已有 6 张表的旧库做：
  1. 创建 check_runs 表
  2. 给 purchase_orders 增加 latest_check_run_id
  3. 给 exception_cases 增加 check_run_id + 索引
  4. 回填：为每个尚无 latest_check_run_id 的采购单创建 completed 批次，
     并把其历史异常挂到该批次
"""
from __future__ import annotations

from backend import config
from backend.models.db import get_connection

CHECK_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS check_runs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    po_id BIGINT UNSIGNED NOT NULL COMMENT '关联 purchase_orders.id',
    status ENUM('running','completed','failed') NOT NULL DEFAULT 'running' COMMENT '批次状态',
    error_message TEXT NULL COMMENT '失败原因',
    started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at DATETIME NULL,
    PRIMARY KEY (id),
    KEY idx_check_runs_po (po_id),
    CONSTRAINT fk_check_runs_po
        FOREIGN KEY (po_id) REFERENCES purchase_orders(id)
        ON UPDATE CASCADE ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='核对批次记录';
"""


def _column_exists(cur, table: str, column: str) -> bool:
    cur.execute(
        "SELECT COUNT(*) AS c FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s AND column_name = %s",
        (config.DB_NAME, table, column),
    )
    return cur.fetchone()["c"] > 0


def _index_exists(cur, table: str, index: str) -> bool:
    cur.execute(
        "SELECT COUNT(*) AS c FROM information_schema.statistics "
        "WHERE table_schema = %s AND table_name = %s AND index_name = %s",
        (config.DB_NAME, table, index),
    )
    return cur.fetchone()["c"] > 0


def migrate() -> dict:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(CHECK_RUNS_DDL)

            if not _column_exists(cur, "purchase_orders", "latest_check_run_id"):
                cur.execute(
                    "ALTER TABLE purchase_orders "
                    "ADD COLUMN latest_check_run_id BIGINT UNSIGNED NULL "
                    "COMMENT '最新成功核对批次，关联 check_runs.id'"
                )

            if not _column_exists(cur, "exception_cases", "check_run_id"):
                cur.execute(
                    "ALTER TABLE exception_cases "
                    "ADD COLUMN check_run_id BIGINT UNSIGNED NULL "
                    "COMMENT '所属核对批次，关联 check_runs.id'"
                )

            if not _index_exists(cur, "exception_cases", "idx_exception_check_run"):
                cur.execute(
                    "CREATE INDEX idx_exception_check_run ON exception_cases (check_run_id)"
                )

            # 回填：为尚无 latest_check_run_id 的采购单创建 completed 批次，并挂起历史异常
            cur.execute(
                "SELECT id FROM purchase_orders WHERE latest_check_run_id IS NULL"
            )
            po_ids = [r["id"] for r in cur.fetchall()]
            backfilled = 0
            for po_id in po_ids:
                cur.execute(
                    "INSERT INTO check_runs (po_id, status, finished_at) "
                    "VALUES (%s, 'completed', NOW())",
                    (po_id,),
                )
                run_id = cur.lastrowid
                cur.execute(
                    "UPDATE exception_cases SET check_run_id = %s "
                    "WHERE po_id = %s AND check_run_id IS NULL",
                    (run_id, po_id),
                )
                cur.execute(
                    "UPDATE purchase_orders SET latest_check_run_id = %s WHERE id = %s",
                    (run_id, po_id),
                )
                backfilled += 1

    return {"purchase_orders_backfilled": backfilled}


if __name__ == "__main__":
    import json

    print(json.dumps(migrate(), ensure_ascii=False))