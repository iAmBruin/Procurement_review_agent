"""采购单服务：purchase_orders / purchase_order_items 的读写与核对入口。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from backend.models.db import get_connection
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
               (SELECT COUNT(*) FROM exception_cases e WHERE e.po_id = p.id) AS exception_count,
               (SELECT COUNT(*) FROM exception_cases e WHERE e.po_id = p.id
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
            po = _row_to_po(row)
            cur.execute("SELECT COUNT(*) AS c FROM purchase_order_items WHERE po_id = %s", (po_id,))
            po.item_count = cur.fetchone()["c"]
            cur.execute(
                "SELECT COUNT(*) AS c FROM exception_cases WHERE po_id = %s", (po_id,)
            )
            po.exception_count = cur.fetchone()["c"]
            cur.execute(
                "SELECT COUNT(*) AS c FROM exception_cases WHERE po_id = %s AND status IN ('open','under_review')",
                (po_id,),
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
    """对单张采购单执行核对，异常写入 exception_cases 并返回完整结果。

    幂等：核对前先清除该采购单已有异常及其审核日志，避免重复累积。
    """
    po = get_purchase_order(po_id)
    if not po:
        raise ValueError(f"采购单不存在：id={po_id}")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM review_logs WHERE exception_id IN "
                "(SELECT id FROM exception_cases WHERE po_id = %s)", (po_id,)
            )
            cur.execute("DELETE FROM exception_cases WHERE po_id = %s", (po_id,))
    exceptions = reconciliation_engine.check_po(po_id)
    saved = exception_service.save_exceptions(po_id, exceptions)
    return CheckResult(
        po_id=po_id,
        po_number=po.po_number,
        checked_at=datetime.now(),
        exceptions=saved,
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
    """仪表盘统计。"""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM purchase_orders")
            total_po = cur.fetchone()["c"]
            cur.execute("SELECT COUNT(*) AS c FROM exception_cases")
            total_exc = cur.fetchone()["c"]
            cur.execute("SELECT COUNT(*) AS c FROM exception_cases WHERE status IN ('open','under_review')")
            open_exc = cur.fetchone()["c"]
            cur.execute(
                "SELECT exception_type, COUNT(*) AS c FROM exception_cases GROUP BY exception_type"
            )
            by_type = {r["exception_type"]: r["c"] for r in cur.fetchall()}
            cur.execute(
                "SELECT severity, COUNT(*) AS c FROM exception_cases GROUP BY severity"
            )
            by_severity = {r["severity"]: r["c"] for r in cur.fetchall()}
    return {
        "total_po": total_po,
        "total_exceptions": total_exc,
        "open_exceptions": open_exc,
        "by_type": by_type,
        "by_severity": by_severity,
    }