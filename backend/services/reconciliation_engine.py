"""确定性核对引擎（新模型）。

依赖 procurement_agent 库的外键结构：receipt_items / invoice_items 通过
po_item_id 直接关联 purchase_order_items，因此无需再做 SKU / 名称模糊匹配。

规则清单：
  1. QTY_EXCEEDED        对每条 PO 明细：开票数量合计 > 收货数量合计
  2. UNIT_PRICE_MISMATCH 发票单价与采购单价偏差 > 容差
  3. DUPLICATE_INVOICE   同一采购单下 invoice_no 重复
  4. MISSING_DOCUMENT    有发票但无收货记录（行级 / 采购单级）

容差（无 contract_rules 表后）统一取 config 中的默认值。
"""
from __future__ import annotations

from datetime import datetime

from backend import config
from backend.models.db import get_connection
from backend.models.schemas import ExceptionCase


def _dec(v) -> float:
    return float(v) if v is not None else 0.0


def check_po(po_id: int) -> list[ExceptionCase]:
    """对一张采购单执行全部核对规则，返回异常列表（不含 id / exception_code）。"""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM purchase_orders WHERE id = %s", (po_id,))
            po = cur.fetchone()
            if not po:
                raise ValueError(f"采购单不存在：id={po_id}")

            cur.execute(
                "SELECT * FROM purchase_order_items WHERE po_id = %s ORDER BY line_no", (po_id,)
            )
            items = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """SELECT r.* FROM receipt_items r
                   JOIN purchase_order_items i ON i.id = r.po_item_id
                   WHERE i.po_id = %s""",
                (po_id,),
            )
            receipts = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """SELECT v.* FROM invoice_items v
                   JOIN purchase_order_items i ON i.id = v.po_item_id
                   WHERE i.po_id = %s""",
                (po_id,),
            )
            invoices = [dict(r) for r in cur.fetchall()]

    exceptions: list[ExceptionCase] = []

    exceptions.extend(_check_qty_and_receipt(po, items, receipts, invoices))
    exceptions.extend(_check_unit_price(po, items, invoices))
    exceptions.extend(_check_duplicate_invoice(po, invoices))

    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    exceptions.sort(key=lambda e: severity_order.get(e.severity, 9))
    return exceptions


# ------------------------------------------------------------------ 数量 / 收货
def _check_qty_and_receipt(po, items, receipts, invoices) -> list[ExceptionCase]:
    result: list[ExceptionCase] = []
    received_by_item: dict[int, float] = {}
    for r in receipts:
        received_by_item[r["po_item_id"]] = received_by_item.get(r["po_item_id"], 0.0) + _dec(r["received_qty"])

    invoiced_by_item: dict[int, float] = {}
    for v in invoices:
        invoiced_by_item[v["po_item_id"]] = invoiced_by_item.get(v["po_item_id"], 0.0) + _dec(v["invoiced_qty"])

    for item in items:
        pid = item["id"]
        received = received_by_item.get(pid, 0.0)
        invoiced = invoiced_by_item.get(pid, 0.0)
        if invoiced <= 0:
            continue
        if received <= 0:
            # 有发票却无任何收货记录：行级缺失收货
            result.append(
                ExceptionCase(
                    po_id=po["id"],
                    po_item_id=pid,
                    exception_type="MISSING_DOCUMENT",
                    severity="medium",
                    title=f"商品「{item['product_name']}」已开票但无收货记录",
                    description=(
                        f"采购单 {po['po_number']} 中商品「{item['product_name']}」"
                        f"已开票 {invoiced:g}{item['unit'] or ''}，但未找到任何收货记录，"
                        f"无法确认实际收货数量。"
                    ),
                    expected_value=invoiced,
                    actual_value=0.0,
                    difference_value=invoiced,
                    evidence_json={
                        "sku": item.get("sku"),
                        "product_name": item["product_name"],
                        "po_number": po["po_number"],
                        "invoiced_qty": invoiced,
                        "received_qty": 0,
                        "receipt_nos": [],
                        "invoice_nos": sorted({v["invoice_no"] for v in invoices if v["po_item_id"] == pid}),
                    },
                )
            )
        elif invoiced > received:
            result.append(
                ExceptionCase(
                    po_id=po["id"],
                    po_item_id=pid,
                    exception_type="QTY_EXCEEDED",
                    severity="high",
                    title=f"商品「{item['product_name']}」开票数量超过收货数量",
                    description=(
                        f"采购单 {po['po_number']} 中商品「{item['product_name']}」开票数量 "
                        f"{invoiced:g}{item['unit'] or ''} 超过已登记收货数量 "
                        f"{received:g}{item['unit'] or ''}，超出 {invoiced - received:g}{item['unit'] or ''}。"
                    ),
                    expected_value=received,
                    actual_value=invoiced,
                    difference_value=round(invoiced - received, 4),
                    evidence_json={
                        "sku": item.get("sku"),
                        "product_name": item["product_name"],
                        "po_number": po["po_number"],
                        "invoiced_qty": invoiced,
                        "received_qty": received,
                        "receipt_nos": sorted({r["receipt_no"] for r in receipts if r["po_item_id"] == pid}),
                        "invoice_nos": sorted({v["invoice_no"] for v in invoices if v["po_item_id"] == pid}),
                    },
                )
            )
    return result


# ------------------------------------------------------------------ 单价偏差
def _check_unit_price(po, items, invoices) -> list[ExceptionCase]:
    result: list[ExceptionCase] = []
    item_by_id = {i["id"]: i for i in items}
    for v in invoices:
        item = item_by_id.get(v["po_item_id"])
        if not item:
            continue
        po_price = _dec(item["unit_price"])
        inv_price = _dec(v["unit_price"])
        if po_price <= 0 or inv_price <= 0:
            continue
        ratio = abs(inv_price - po_price) / po_price
        if ratio > config.DEFAULT_PRICE_TOLERANCE:
            result.append(
                ExceptionCase(
                    po_id=po["id"],
                    po_item_id=item["id"],
                    exception_type="UNIT_PRICE_MISMATCH",
                    severity="medium",
                    title=f"商品「{item['product_name']}」发票单价与采购单价偏差超容差",
                    description=(
                        f"采购单 {po['po_number']} 中商品「{item['product_name']}」发票单价 "
                        f"{inv_price:g} 与采购单价 {po_price:g} 偏差 {ratio * 100:.2f}%，"
                        f"超出容差 {config.DEFAULT_PRICE_TOLERANCE * 100:.0f}%。"
                    ),
                    expected_value=po_price,
                    actual_value=inv_price,
                    difference_value=round(ratio, 6),
                    evidence_json={
                        "sku": item.get("sku"),
                        "product_name": item["product_name"],
                        "po_number": po["po_number"],
                        "po_unit_price": po_price,
                        "invoice_unit_price": inv_price,
                        "invoice_no": v["invoice_no"],
                        "deviation_pct": round(ratio * 100, 2),
                    },
                )
            )
    return result


# ------------------------------------------------------------------ 重复发票
def _check_duplicate_invoice(po, invoices) -> list[ExceptionCase]:
    result: list[ExceptionCase] = []
    by_no: dict[str, int] = {}
    for v in invoices:
        by_no[v["invoice_no"]] = by_no.get(v["invoice_no"], 0) + 1
    for inv_no, cnt in by_no.items():
        if cnt <= 1:
            continue
        result.append(
            ExceptionCase(
                po_id=po["id"],
                po_item_id=None,
                exception_type="DUPLICATE_INVOICE",
                severity="critical",
                title=f"发票号 {inv_no} 重复",
                description=(
                    f"采购单 {po['po_number']} 下发票号 {inv_no} 出现 {cnt} 次，疑似重复开票。"
                ),
                expected_value=1,
                actual_value=float(cnt),
                difference_value=float(cnt - 1),
                evidence_json={"po_number": po["po_number"], "invoice_no": inv_no, "occurrences": cnt},
            )
        )
    return result