"""Pydantic 数据模型：采购单、明细、异常、审核记录。

新模型（procurement_agent 库）：
  purchase_orders        采购单主表
  purchase_order_items   采购商品明细
  receipt_items          收货明细
  invoice_items          发票明细
  exception_cases        异常记录
  review_logs            人工审核记录
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel

# ---- 枚举 ----

ExceptionType = Literal[
    "QTY_EXCEEDED",        # 开票数量超过已收货数量
    "UNIT_PRICE_MISMATCH", # 发票单价与采购单价偏差超容差
    "DUPLICATE_INVOICE",   # 同一采购单下发票号重复
    "MISSING_DOCUMENT",    # 有发票无收货 / 有采购无收货或发票
]
Severity = Literal["low", "medium", "high", "critical"]
ExceptionStatus = Literal["open", "under_review", "confirmed", "rejected", "resolved"]
PoStatus = Literal["draft", "open", "partially_received", "received", "closed", "cancelled"]
InvoiceStatus = Literal["received", "verified", "rejected", "paid"]


# ---- 采购单 ----

class PurchaseOrderCreate(BaseModel):
    po_number: str
    supplier_name: str
    supplier_code: Optional[str] = None
    order_date: Optional[date] = None
    currency: str = "CNY"
    remarks: Optional[str] = None


class PurchaseOrderItemCreate(BaseModel):
    line_no: int
    sku: Optional[str] = None
    product_name: str
    specification: Optional[str] = None
    unit: str = "件"
    ordered_qty: float
    unit_price: float
    tax_rate: float = 0.0
    remarks: Optional[str] = None


class PurchaseOrder(BaseModel):
    id: Optional[int] = None
    po_number: str
    supplier_code: Optional[str] = None
    supplier_name: str
    order_date: Optional[date] = None
    currency: str = "CNY"
    status: PoStatus = "open"
    remarks: Optional[str] = None
    latest_check_run_id: Optional[int] = None
    created_at: Optional[datetime] = None
    # 汇总字段（查询时计算填充）
    item_count: int = 0
    exception_count: int = 0
    open_exception_count: int = 0


class PurchaseOrderItem(BaseModel):
    id: Optional[int] = None
    po_id: int
    line_no: int
    sku: Optional[str] = None
    product_name: str
    specification: Optional[str] = None
    unit: str = "件"
    ordered_qty: float
    unit_price: float
    tax_rate: float = 0.0
    remarks: Optional[str] = None


# ---- 异常 ----

class ExceptionCase(BaseModel):
    """统一异常输出格式（对应 exception_cases 表）。"""
    id: Optional[int] = None
    exception_code: str = ""  # 保存后由 exception_service 填充（EX-00001 形式）
    po_id: int
    check_run_id: Optional[int] = None   # 所属核对批次
    po_number: Optional[str] = None       # 由 JOIN 补全
    supplier_name: Optional[str] = None   # 由 JOIN 补全
    po_item_id: Optional[int] = None
    exception_type: ExceptionType
    severity: Severity = "medium"
    status: ExceptionStatus = "open"
    title: str
    description: Optional[str] = None
    expected_value: Optional[float] = None
    actual_value: Optional[float] = None
    difference_value: Optional[float] = None
    evidence_json: Optional[dict] = None
    detected_at: Optional[datetime] = None
    reviewed_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None


# ---- 审核记录 ----

class ReviewLog(BaseModel):
    id: Optional[int] = None
    exception_id: int
    action: str
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    reviewer: str = "demo_user"
    comment: Optional[str] = None
    created_at: Optional[datetime] = None


# ---- 核对结果 ----

class CheckResult(BaseModel):
    po_id: int
    po_number: str
    checked_at: datetime
    exceptions: list[ExceptionCase]