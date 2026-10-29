"""新模型种子数据生成器。

生成 30 张采购单 + 商品明细 + 收货明细 + 发票明细，并内置四类异常场景：
  - QTY_EXCEEDED        开票数量 > 收货数量
  - UNIT_PRICE_MISMATCH 发票单价与采购单价偏差 > 2%
  - MISSING_DOCUMENT    有发票但无收货记录
  - DUPLICATE_INVOICE   同一采购单下发票号重复

生成后调用核对引擎产出 exception_cases。

用法：
  python -m database.seed_data
"""
from __future__ import annotations

import random
from datetime import date, timedelta

from backend.models.db import get_connection
from backend.services import exception_service, reconciliation_engine

SUPPLIERS = [
    ("SUP-001", "上海钢联物资有限公司"),
    ("SUP-002", "浙江万向流体控制有限公司"),
    ("SUP-003", "江苏远东电缆股份有限公司"),
    ("SUP-004", "宁波金田轴承有限公司"),
    ("SUP-005", "山东永固紧固件有限公司"),
    ("SUP-006", "广东威特电机有限公司"),
    ("SUP-007", "安徽三联泵业有限公司"),
    ("SUP-008", "鞍钢集团钢材销售有限公司"),
]

PRODUCTS = [
    ("SKU-1001", "热镀锌钢管", "DN25*3.2mm", "米"),
    ("SKU-1002", "不锈钢法兰", "DN50 PN16", "片"),
    ("SKU-1003", "闸阀", "Z41H-16C DN80", "台"),
    ("SKU-1004", "电力电缆", "YJV 3*70+1*35", "米"),
    ("SKU-1005", "深沟球轴承", "6205-2RS", "套"),
    ("SKU-1006", "六角螺栓", "M16*80 8.8级", "套"),
    ("SKU-1007", "三相异步电机", "YE3-132S-4 5.5kW", "台"),
    ("SKU-1008", "潜水泵", "QDX10-16-0.75", "台"),
    ("SKU-1009", "热轧钢板", "Q235B 12mm", "吨"),
    ("SKU-1010", "环氧富锌底漆", "H06-4 20kg", "桶"),
]

_DELETE_ORDER = [
    "review_logs",
    "exception_cases",
    "receipt_items",
    "invoice_items",
    "purchase_order_items",
    "purchase_orders",
]


def clear_all() -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS=0")
            for t in _DELETE_ORDER:
                cur.execute(f"TRUNCATE TABLE {t}")
            cur.execute("SET FOREIGN_KEY_CHECKS=1")


def seed() -> dict:
    clear_all()
    rng = random.Random(42)

    # 重复发票注入的采购单索引（0-based）
    dup_po_indexes = {6, 16, 26}

    po_ids: list[int] = []
    with get_connection() as conn:
        with conn.cursor() as cur:
            for idx in range(30):
                po_number = f"PO-2024-{idx + 1:04d}"
                scode, sname = rng.choice(SUPPLIERS)
                order_date = date(2024, 1, 1) + timedelta(days=rng.randint(0, 200))
                cur.execute(
                    """INSERT INTO purchase_orders
                       (po_number, supplier_code, supplier_name, order_date, currency, status)
                       VALUES (%s, %s, %s, %s, 'CNY', 'open')""",
                    (po_number, scode, sname, order_date),
                )
                po_id = cur.lastrowid
                po_ids.append(po_id)

                n_items = rng.randint(2, 4)
                for line_no in range(1, n_items + 1):
                    sku, pname, spec, unit = rng.choice(PRODUCTS)
                    ordered_qty = float(rng.choice([100, 200, 300, 500, 800, 1000]))
                    unit_price = round(rng.uniform(5.0, 900.0), 2)
                    cur.execute(
                        """INSERT INTO purchase_order_items
                           (po_id, line_no, sku, product_name, specification, unit, ordered_qty, unit_price, tax_rate)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0.05)""",
                        (po_id, line_no, sku, pname, spec, unit, ordered_qty, unit_price),
                    )
                    item_id = cur.lastrowid

                    # 每个商品随机分配一种"剧本"
                    kind = rng.choices(
                        ["normal", "partial", "qty_exceeded", "price_mismatch", "missing_doc"],
                        weights=[35, 15, 20, 15, 15],
                        k=1,
                    )[0]

                    # 收货数量（missing_doc 无收货）
                    received_qty = 0.0 if kind == "missing_doc" else ordered_qty
                    # 发票数量（qty_exceeded 多开 10%）
                    invoiced_qty = ordered_qty if kind != "qty_exceeded" else round(ordered_qty * 1.1, 3)
                    # 发票单价（price_mismatch 偏 5%）
                    invoice_price = unit_price if kind != "price_mismatch" else round(unit_price * 1.05, 2)

                    is_dup_po = idx in dup_po_indexes

                    # 收货记录：normal/partial/qty_exceeded/price_mismatch 都有收货
                    if received_qty > 0:
                        if kind == "partial":
                            half = round(received_qty * 0.6, 3)
                            cur.execute(
                                """INSERT INTO receipt_items
                                   (receipt_no, po_item_id, received_date, received_qty, received_by)
                                   VALUES (%s, %s, %s, %s, %s)""",
                                (f"GRN-{idx + 1:04d}-{line_no}a", item_id, order_date + timedelta(days=3), half, "张三"),
                            )
                            cur.execute(
                                """INSERT INTO receipt_items
                                   (receipt_no, po_item_id, received_date, received_qty, received_by)
                                   VALUES (%s, %s, %s, %s, %s)""",
                                (f"GRN-{idx + 1:04d}-{line_no}b", item_id, order_date + timedelta(days=10), round(received_qty - half, 3), "李四"),
                            )
                        else:
                            cur.execute(
                                """INSERT INTO receipt_items
                                   (receipt_no, po_item_id, received_date, received_qty, received_by)
                                   VALUES (%s, %s, %s, %s, %s)""",
                                (f"GRN-{idx + 1:04d}-{line_no}", item_id, order_date + timedelta(days=3), received_qty, "张三"),
                            )

                    # 发票记录
                    inv_no = (
                        f"INV-{idx + 1:04d}-DUP" if is_dup_po
                        else f"INV-{idx + 1:04d}-{line_no:02d}"
                    )
                    cur.execute(
                        """INSERT INTO invoice_items
                           (invoice_no, po_item_id, invoice_date, invoiced_qty, unit_price, tax_rate, currency, invoice_status)
                           VALUES (%s, %s, %s, %s, %s, 0.05, 'CNY', 'received')""",
                        (inv_no, item_id, order_date + timedelta(days=5), invoiced_qty, invoice_price),
                    )

    # 运行核对引擎，产出异常
    total_exc = 0
    for po_id in po_ids:
        excs = reconciliation_engine.check_po(po_id)
        exception_service.save_exceptions(po_id, excs)
        total_exc += len(excs)

    return {"purchase_orders": len(po_ids), "exceptions": total_exc}


if __name__ == "__main__":
    import json

    result = seed()
    print(json.dumps(result, ensure_ascii=False))