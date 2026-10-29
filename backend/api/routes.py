"""FastAPI 路由（新模型：采购单 / 明细 / 收货 / 发票 / 异常 / 审核）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.models.schemas import ReviewLog
from backend.services import exception_service, order_service

router = APIRouter(prefix="/api/v1")


@router.get("/health")
def health():
    return {"status": "ok"}


# ------------------------------------------------------------------ 采购单
@router.get("/orders")
def list_orders(status: str | None = None, keyword: str | None = None):
    return [o.model_dump(mode="json") for o in order_service.list_purchase_orders(status, keyword)]


@router.get("/orders/{po_id}")
def get_order(po_id: int):
    try:
        return order_service.get_order_details(po_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/orders/{po_id}/details")
def get_order_detail_v2(po_id: int):
    return get_order(po_id)


# ------------------------------------------------------------------ 核对
@router.post("/orders/{po_id}/check")
def run_check(po_id: int):
    try:
        result = order_service.run_check(po_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return result.model_dump(mode="json")


# ------------------------------------------------------------------ 异常
@router.get("/orders/{po_id}/exceptions")
def list_order_exceptions(po_id: int, status: str | None = None):
    return [e.model_dump(mode="json") for e in exception_service.list_exceptions(po_id, status=status)]


@router.get("/exceptions/open")
def list_open_exceptions():
    return [e.model_dump(mode="json") for e in exception_service.list_open_exceptions()]


@router.get("/exceptions/{exception_id}")
def get_exception_detail(exception_id: int):
    exc = exception_service.get_exception(exception_id)
    if not exc:
        raise HTTPException(404, "异常不存在")
    return exc.model_dump(mode="json")


@router.post("/exceptions/{exception_id}/actions", status_code=201)
def add_review_log(exception_id: int, payload: dict):
    action = payload.get("action")
    if action not in exception_service.VALID_ACTIONS:
        raise HTTPException(400, f"action 必须是 {sorted(exception_service.VALID_ACTIONS)} 之一")
    record = ReviewLog(
        exception_id=exception_id,
        action=action,
        reviewer=payload.get("reviewer", "demo_user"),
        comment=payload.get("comment"),
    )
    saved = exception_service.save_review_log(record)
    return saved.model_dump(mode="json")


@router.get("/exceptions/{exception_id}/logs")
def list_review_logs(exception_id: int):
    return [r.model_dump(mode="json") for r in exception_service.list_review_logs(exception_id)]


# ------------------------------------------------------------------ 统计
@router.get("/stats")
def stats():
    return order_service.stats()