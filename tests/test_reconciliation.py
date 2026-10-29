"""核对引擎单元测试（新模型）：按 po_item_id 外键关联，覆盖 4 类规则。"""
from __future__ import annotations

from backend.models.db import get_connection
from backend.services import exception_service, reconciliation_engine


def _insert_po(cur, po_number: str, items: list[tuple]) -> tuple[int, list[int]]:
    """插入一张采购单及其商品明细，返回 (po_id, [item_ids])。

    items: [(product_name, unit, ordered_qty, unit_price), ...]
    """
    cur.execute(
        """INSERT INTO purchase_orders (po_number, supplier_code, supplier_name, order_date, currency)
           VALUES (%s, 'SUP-T', '测试供应商', '2024-06-01', 'CNY')""",
        (po_number,),
    )
    po_id = cur.lastrowid
    item_ids: list[int] = []
    for i, (name, unit, qty, price) in enumerate(items, start=1):
        cur.execute(
            """INSERT INTO purchase_order_items
               (po_id, line_no, sku, product_name, specification, unit, ordered_qty, unit_price, tax_rate)
               VALUES (%s, %s, %s, %s, NULL, %s, %s, %s, 0.05)""",
            (po_id, i, f"SKU-T{i}", name, unit, qty, price),
        )
        item_ids.append(cur.lastrowid)
    return po_id, item_ids


def _cleanup_po(conn, po_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM receipt_items WHERE po_item_id IN "
            "(SELECT id FROM purchase_order_items WHERE po_id = %s)", (po_id,)
        )
        cur.execute(
            "DELETE FROM invoice_items WHERE po_item_id IN "
            "(SELECT id FROM purchase_order_items WHERE po_id = %s)", (po_id,)
        )
        cur.execute(
            "DELETE FROM exception_cases WHERE po_id = %s", (po_id,)
        )
        cur.execute(
            "DELETE FROM purchase_order_items WHERE po_id = %s", (po_id,)
        )
        cur.execute("DELETE FROM purchase_orders WHERE id = %s", (po_id,))


def _types(excs) -> list[str]:
    return [e.exception_type for e in excs]


def test_qty_exceeded():
    with get_connection() as conn:
        with conn.cursor() as cur:
            po_id, (item_id,) = _insert_po(cur, "PO-TEST-QTY", [("镀锌管", "米", 100, 20.0)])
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO receipt_items (receipt_no, po_item_id, received_qty) VALUES ('GRN-T1', %s, 90)",
            (item_id,),
        )
        cur.execute(
            "INSERT INTO invoice_items (invoice_no, po_item_id, invoiced_qty, unit_price) VALUES ('INV-T1', %s, 100, 20.0)",
            (item_id,),
        )
        try:
            excs = reconciliation_engine.check_po(po_id)
            qty = [e for e in excs if e.exception_type == "QTY_EXCEEDED"]
            assert len(qty) == 1
            assert qty[0].expected_value == 90
            assert qty[0].actual_value == 100
            assert qty[0].difference_value == 10
            assert qty[0].severity == "high"
        finally:
            _cleanup_po(conn, po_id)


def test_unit_price_mismatch():
    with get_connection() as conn:
        with conn.cursor() as cur:
            po_id, (item_id,) = _insert_po(cur, "PO-TEST-PRICE", [("轴承", "套", 100, 20.0)])
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO receipt_items (receipt_no, po_item_id, received_qty) VALUES ('GRN-T2', %s, 100)",
            (item_id,),
        )
        cur.execute(
            "INSERT INTO invoice_items (invoice_no, po_item_id, invoiced_qty, unit_price) VALUES ('INV-T2', %s, 100, 21.0)",
            (item_id,),
        )
        try:
            excs = reconciliation_engine.check_po(po_id)
            assert "UNIT_PRICE_MISMATCH" in _types(excs)
            assert len(_types(excs)) == 1  # 数量正常，只报单价
        finally:
            _cleanup_po(conn, po_id)


def test_duplicate_invoice():
    with get_connection() as conn:
        with conn.cursor() as cur:
            po_id, (a, b) = _insert_po(
                cur, "PO-TEST-DUP", [("阀门", "台", 10, 50.0), ("法兰", "片", 20, 5.0)]
            )
        cur = conn.cursor()
        for item_id, no in [(a, "INV-DUP"), (b, "INV-DUP")]:
            cur.execute(
                "INSERT INTO receipt_items (receipt_no, po_item_id, received_qty) VALUES (%s, %s, 10)",
                (f"GRN-{item_id}", item_id),
            )
            cur.execute(
                "INSERT INTO invoice_items (invoice_no, po_item_id, invoiced_qty, unit_price) VALUES (%s, %s, 10, 50.0)",
                (no, item_id),
            )
        try:
            excs = reconciliation_engine.check_po(po_id)
            dup = [e for e in excs if e.exception_type == "DUPLICATE_INVOICE"]
            assert len(dup) == 1
            assert dup[0].severity == "critical"
            assert dup[0].po_item_id is None  # 订单级异常
        finally:
            _cleanup_po(conn, po_id)


def test_missing_document():
    with get_connection() as conn:
        with conn.cursor() as cur:
            po_id, (item_id,) = _insert_po(cur, "PO-TEST-MISS", [("电缆", "米", 100, 30.0)])
        cur = conn.cursor()
        # 只开票，无收货
        cur.execute(
            "INSERT INTO invoice_items (invoice_no, po_item_id, invoiced_qty, unit_price) VALUES ('INV-T3', %s, 100, 30.0)",
            (item_id,),
        )
        try:
            excs = reconciliation_engine.check_po(po_id)
            assert "MISSING_DOCUMENT" in _types(excs)
        finally:
            _cleanup_po(conn, po_id)


def test_normal_no_exception():
    with get_connection() as conn:
        with conn.cursor() as cur:
            po_id, (item_id,) = _insert_po(cur, "PO-TEST-OK", [("螺栓", "套", 100, 2.0)])
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO receipt_items (receipt_no, po_item_id, received_qty) VALUES ('GRN-T4', %s, 100)",
            (item_id,),
        )
        cur.execute(
            "INSERT INTO invoice_items (invoice_no, po_item_id, invoiced_qty, unit_price) VALUES ('INV-T4', %s, 100, 2.0)",
            (item_id,),
        )
        try:
            assert reconciliation_engine.check_po(po_id) == []
        finally:
            _cleanup_po(conn, po_id)


def test_seed_data_covers_all_types():
    excs = exception_service.list_open_exceptions()
    types = {e.exception_type for e in excs}
    assert {"QTY_EXCEEDED", "UNIT_PRICE_MISMATCH", "DUPLICATE_INVOICE", "MISSING_DOCUMENT"} <= types


def test_every_exception_has_payload():
    for e in exception_service.list_open_exceptions():
        assert e.title, f"异常 #{e.id} 缺标题"
        assert e.po_number, f"异常 #{e.id} 缺采购单号"
        assert e.exception_code.startswith("EX-"), f"异常 #{e.id} 编号格式错误"