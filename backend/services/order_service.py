"""采购单服务：purchase_orders / purchase_order_items 的读写与核对入口。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from backend.models.db import get_connection, transaction
from backend.models.schemas import (
    CheckResult,
    ExceptionCase,
    PurchaseOrder,
    PurchaseOrderCreate,
    PurchaseOrderItem,
    PurchaseOrderItemCreate,
)
from backend.services import exception_service, reconciliation_engine

# 采购单状态 -> 中文
PO_STATUS_ZH = {
    "draft": "草稿",
    "open": "进行中",
    "partially_received": "部分收货",
    "received": "已收货",
    "closed": "已关闭",
    "cancelled": "已取消",
}

EXCEPTION_TYPE_ZH = {
    "QTY_EXCEEDED": "数量超收",
    "UNIT_PRICE_MISMATCH": "单价不符",
    "DUPLICATE_INVOICE": "重复发票",
    "MISSING_DOCUMENT": "单据缺失",
}

SEVERITY_ZH = {
    "low": "低",
    "medium": "中",
    "high": "高",
    "critical": "严重",
}

STATUS_ZH = {
    "open": "待处理",
    "under_review": "复核中",
    "confirmed": "已确认",
    "rejected": "已驳回",
    "resolved": "已解决",
}


def _row_to_po(row: dict) -> PurchaseOrder:
    return PurchaseOrder(
        id=row["id"],
        po_number=row["po_number"],
        supplier_code=row.get("supplier_code"),
        supplier_name=row["supplier_name"],
        order_date=row.get("order_date"),
        currency=row.get("currency") or "CNY",
        status=row.get("status") or "open",
        remarks=row.get("remarks"),
        latest_check_run_id=row.get("latest_check_run_id"),
        created_at=row.get("created_at"),
    )


def list_purchase_orders(status: Optional[str] = None, keyword: Optional[str] = None) -> list[PurchaseOrder]:
    """列表，附带明细条数 / 异常条数 / 未处理异常条数。"""
    where, args = [], []
    if status:
        where.append("p.status = %s")
        args.append(status)
    if keyword:
        where.append("(p.po_number LIKE %s OR p.supplier_name LIKE %s)")
        args += [f"%{keyword}%", f"%{keyword}%"]
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    sql = f"""
        SELECT p.*,
               (SELECT COUNT(*) FROM purchase_order_items i WHERE i.po_id = p.id) AS item_count,
               (SELECT COUNT(*) FROM exception_cases e WHERE e.po_id = p.id
                 AND e.check_run_id = p.latest_check_run_id) AS exception_count,
               (SELECT COUNT(*) FROM exception_cases e WHERE e.po_id = p.id
                 AND e.check_run_id = p.latest_check_run_id
                 AND e.status IN ('open','under_review')) AS open_exception_count
        FROM purchase_orders p
        {clause}
        ORDER BY p.id
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            rows = cur.fetchall()

    result = []
    for r in rows:
        po = _row_to_po(r)
        po.item_count = r.get("item_count") or 0
        po.exception_count = r.get("exception_count") or 0
        po.open_exception_count = r.get("open_exception_count") or 0
        result.append(po)
    return result


def get_purchase_order(po_id: int) -> Optional[PurchaseOrder]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM purchase_orders WHERE id = %s", (po_id,))
            row = cur.fetchone()
            if not row:
                return None
            # 将数据库行转换为 PurchaseOrder 对象
            po = _row_to_po(row)
            latest_run_id = row.get("latest_check_run_id")
            cur.execute("SELECT COUNT(*) AS c FROM purchase_order_items WHERE po_id = %s", (po_id,))
            po.item_count = cur.fetchone()["c"]
            cur.execute(
                "SELECT COUNT(*) AS c FROM exception_cases WHERE po_id = %s AND check_run_id <=> %s",
                (po_id, latest_run_id),
            )
            po.exception_count = cur.fetchone()["c"]
            cur.execute(
                "SELECT COUNT(*) AS c FROM exception_cases WHERE po_id = %s "
                "AND check_run_id <=> %s AND status IN ('open','under_review')",
                (po_id, latest_run_id),
            )
            po.open_exception_count = cur.fetchone()["c"]
            return po


def get_purchase_order_items(po_id: int) -> list[PurchaseOrderItem]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM purchase_order_items WHERE po_id = %s ORDER BY line_no", (po_id,)
            )
            return [
                PurchaseOrderItem(
                    id=r["id"], po_id=r["po_id"], line_no=r["line_no"], sku=r.get("sku"),
                    product_name=r["product_name"], specification=r.get("specification"),
                    unit=r.get("unit") or "件", ordered_qty=float(r["ordered_qty"]),
                    unit_price=float(r["unit_price"]),
                    tax_rate=float(r["tax_rate"]) if r.get("tax_rate") is not None else 0.0,
                    remarks=r.get("remarks"),
                )
                for r in cur.fetchall()
            ]


def create_purchase_order(po: PurchaseOrderCreate, items: list[PurchaseOrderItemCreate]) -> int:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO purchase_orders
                   (po_number, supplier_code, supplier_name, order_date, currency, remarks)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (po.po_number, po.supplier_code, po.supplier_name, po.order_date, po.currency, po.remarks),
            )
            po_id = cur.lastrowid
            for it in items:
                cur.execute(
                    """INSERT INTO purchase_order_items
                       (po_id, line_no, sku, product_name, specification, unit, ordered_qty, unit_price, tax_rate, remarks)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (po_id, it.line_no, it.sku, it.product_name, it.specification, it.unit,
                     it.ordered_qty, it.unit_price, it.tax_rate, it.remarks),
                )
    return po_id


# ---------------------------------------------------------------- 核对
def run_check(po_id: int) -> CheckResult:
    """对指定采购单执行核对，每次生成一个新的核对批次。

    保留历史异常与人工审核记录，仅前移采购单的「最新成功核对批次」指针；
    旧批次数据不做物理删除，便于审计追溯。
    """
    po = get_purchase_order(po_id)
    if not po:
        raise ValueError(f"采购单不存在：id={po_id}")

    # 1. 创建核对批次（初始 running）
    check_run_id = _create_check_run(po_id)
    try:
        # 2. 确定性核对（只读，可在写事务之外完成）
        detected = reconciliation_engine.check_po(po_id)

        # 3. 事务内：保存异常 + 前移最新批次指针 + 标记批次完成
        with transaction() as conn:
            exception_service.save_exceptions(po_id, detected, check_run_id, conn)
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE purchase_orders SET latest_check_run_id = %s WHERE id = %s",
                    (check_run_id, po_id),
                )
                cur.execute(
                    "UPDATE check_runs SET status = 'completed', finished_at = NOW() WHERE id = %s",
                    (check_run_id,),
                )
    except Exception as e:
        _mark_check_run_failed(check_run_id, str(e))
        raise

    # 4. 返回最新批次下的全量异常
    exceptions = exception_service.list_exceptions(po_id)

    return CheckResult(
        po_id=po_id,
        po_number=po.po_number,
        checked_at=datetime.now(),
        exceptions=exceptions,
    )


def _create_check_run(po_id: int) -> int:
    """创建一次核对批次（初始状态 running），返回批次 id。"""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO check_runs (po_id, status) VALUES (%s, 'running')",
                (po_id,),
            )
            return cur.lastrowid


def _mark_check_run_failed(check_run_id: int, error_message: str) -> None:
    """将核对批次标记为 failed，并记录失败原因。"""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE check_runs SET status = 'failed', error_message = %s, finished_at = NOW() "
                "WHERE id = %s",
                (error_message[:5000], check_run_id),
            )


def get_order_details(po_id: int) -> dict:
    """返回采购单全量详情：单据、明细、收货、发票、异常。"""
    po = get_purchase_order(po_id)
    if not po:
        raise ValueError(f"采购单不存在：id={po_id}")
    items = get_purchase_order_items(po_id)
    exceptions = exception_service.list_exceptions(po_id)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT r.*, i.product_name, i.sku, i.unit
                   FROM receipt_items r JOIN purchase_order_items i ON i.id = r.po_item_id
                   WHERE i.po_id = %s ORDER BY r.received_date, r.id""",
                (po_id,),
            )
            receipts = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """SELECT v.*, i.product_name, i.sku, i.unit, i.unit_price AS po_unit_price
                   FROM invoice_items v JOIN purchase_order_items i ON i.id = v.po_item_id
                   WHERE i.po_id = %s ORDER BY v.invoice_date, v.id""",
                (po_id,),
            )
            invoices = [dict(r) for r in cur.fetchall()]

    return {
        "po": po,
        "items": items,
        "receipts": receipts,
        "invoices": invoices,
        "exceptions": exceptions,
    }


def stats() -> dict:
    """仪表盘统计（仅统计各采购单最新成功核对批次的异常）。"""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM purchase_orders")
            total_po = cur.fetchone()["c"]
            cur.execute(
                "SELECT COUNT(*) AS c FROM exception_cases e "
                "JOIN purchase_orders p ON p.id = e.po_id "
                "WHERE e.check_run_id = p.latest_check_run_id"
            )
            total_exc = cur.fetchone()["c"]
            cur.execute(
                "SELECT COUNT(*) AS c FROM exception_cases e "
                "JOIN purchase_orders p ON p.id = e.po_id "
                "WHERE e.check_run_id = p.latest_check_run_id "
                "AND e.status IN ('open','under_review')"
            )
            open_exc = cur.fetchone()["c"]
            cur.execute(
                "SELECT e.exception_type, COUNT(*) AS c FROM exception_cases e "
                "JOIN purchase_orders p ON p.id = e.po_id "
                "WHERE e.check_run_id = p.latest_check_run_id GROUP BY e.exception_type"
            )
            by_type = {r["exception_type"]: r["c"] for r in cur.fetchall()}
            cur.execute(
                "SELECT e.severity, COUNT(*) AS c FROM exception_cases e "
                "JOIN purchase_orders p ON p.id = e.po_id "
                "WHERE e.check_run_id = p.latest_check_run_id GROUP BY e.severity"
            )
            by_severity = {r["severity"]: r["c"] for r in cur.fetchall()}
    return {
        "total_po": total_po,
        "total_exceptions": total_exc,
        "open_exceptions": open_exc,
        "by_type": by_type,
        "by_severity": by_severity,
    }