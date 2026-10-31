"""MCP Server（采购履约异常审查）：把后端实现的采购业务能力，注册成  MCP 工具，
    并封装为  Dify Agent 可以通过 MCP 协议调用的工具。

工具清单：
  - get_purchase_case(po_id)           采购单摘要、明细、收货、发票与异常数量
  - run_match_check(po_id)             执行确定性核对，返回异常列表
  - get_exception_detail(exception_id) 异常详情、参考/实际/差值、证据
  - search_contract_rules(exception_type) 检索 RAGFlow 合同条款原文
  - list_open_exceptions()             未处理异常列表

设计约束：
  - 只提供"查询与解释"工具，不暴露任意 SQL / 直接改库
  - 异常状态修改走后端 API（人工确认），Agent 不直接改状态

启动方式：
  python -m backend.mcp_server.server --transport stdio          # 本地调试
  python -m backend.mcp_server.server --transport streamable-http # 供 Dify 连接
"""
from __future__ import annotations

import argparse

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.base import BaseHTTPMiddleware

from backend import config
from backend.models.db import init_database
from backend.models.schemas import ExceptionCase
from backend.services import exception_service, order_service


# ──────────────────────────────────────────────
# 创建  MCP Server
# ──────────────────────────────────────────────
mcp = FastMCP(
    name="procurement-review-agent",
    instructions=(
        "采购履约异常审查助手。你可以查询采购单、执行单据核对、查看异常证据、"
        "检索合同条款。禁止自行修改核对结果或异常状态；解释规则时必须引用检索结果，"
        "检索不到时如实说明。"
    ),
    host=config.MCP_HOST,
    port=config.MCP_PORT,
    streamable_http_path="/mcp",

    # 传输安全配置
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["host.docker.internal:8911"]
    )
)


# 异常对象类型转换
def _exc_to_dict(exc: ExceptionCase) -> dict:
    return exc.model_dump(mode="json")


# ──────────────────────────────────────────────
# 五个工具
# ──────────────────────────────────────────────
# 1. 采购单、异常查询
@mcp.tool()
def get_purchase_case(po_id: int) -> dict:
    """获取采购单摘要、商品明细、收货明细、发票明细与异常状态汇总。"""
    try:
        details = order_service.get_order_details(po_id)
    except ValueError as e:
        return {"error": str(e)}

    po = details["po"]
    counts: dict[str, int] = {}
    for e in details["exceptions"]:
        counts[e.status] = counts.get(e.status, 0) + 1

    return {
        "po_id": po.id,
        "po_number": po.po_number,
        "supplier": po.supplier_name,
        "status": po.status,
        "currency": po.currency,
        "items": [
            {
                "line_no": it.line_no,
                "sku": it.sku,
                "product_name": it.product_name,
                "ordered_qty": it.ordered_qty,
                "unit": it.unit,
                "unit_price": it.unit_price,
            }
            for it in details["items"]
        ],
        "receipts": [
            {"receipt_no": r["receipt_no"], "product_name": r["product_name"],
             "received_qty": float(r["received_qty"]), "received_date": str(r.get("received_date") or "")}
            for r in details["receipts"]
        ],
        "invoices": [
            {"invoice_no": v["invoice_no"], "product_name": v["product_name"],
             "invoiced_qty": float(v["invoiced_qty"]), "unit_price": float(v["unit_price"]),
             "invoice_date": str(v.get("invoice_date") or "")}
            for v in details["invoices"]
        ],
        "exception_summary": counts,
    }


# 2. 执行确定性核对
@mcp.tool()
def run_match_check(po_id: int) -> dict:
    """对指定采购单执行确定性核对（数量/单价/重复/缺失），返回全部异常及证据。"""
    try:
        result = order_service.run_check(po_id)
    except ValueError as e:
        return {"error": str(e)}
    return {
        "po_id": result.po_id,
        "po_number": result.po_number,
        "checked_at": result.checked_at.isoformat(),
        "exception_count": len(result.exceptions),
        "exceptions": [_exc_to_dict(e) for e in result.exceptions],
    }


# 3. 异常详情查询
@mcp.tool()
def get_exception_detail(exception_id: int) -> dict:
    """获取单项异常详情：类型、严重程度、参考值/实际值/差值、证据、处理状态。"""
    exc = exception_service.get_exception(exception_id)
    if not exc:
        return {"error": f"异常不存在：exception_id={exception_id}"}
    return _exc_to_dict(exc)


# 4. 异常列表查询
@mcp.tool()
def list_open_exceptions() -> list[dict]:
    """列出所有待处理（open / under_review）的异常，便于了解当前积压。"""
    return [_exc_to_dict(e) for e in exception_service.list_open_exceptions()]


# 5. 合同条款检索
@mcp.tool()
def search_contract_rules(exception_type: str) -> dict:
    """检索与该异常类型相关的合同/政策条款原文（来自 RAGFlow 知识库）。"""
    from backend.ragflow_client import EXCEPTION_QUERY_MAP, configured, search

    if not configured():
        return {
            "exception_type": exception_type,
            "source": "unavailable",
            "notice": "RAGFlow 未配置（缺 RAGFLOW_BASE_URL/RAGFLOW_API_KEY），无法检索合同条款原文。",
        }

    query = EXCEPTION_QUERY_MAP.get(exception_type, exception_type)
    try:
        chunks = search(query)
    except Exception as exc:
        return {
            "exception_type": exception_type,
            "source": "unavailable",
            "notice": f"RAGFlow 检索失败（{exc}）。",
            "query": query,
        }
    return {
        "exception_type": exception_type,
        "source": "ragflow",
        "query": query,
        "retrieved": chunks[:5],
    }


class _RequestLogMiddleware(BaseHTTPMiddleware):
    """临时诊断：打印每个到达 MCP 端点的请求头，用于定位 Dify 403 来源。"""

    async def dispatch(self, request, call_next):
        print(
            "[MCP-REQ] %s %s host=%r origin=%r auth=%r accept=%r"
            % (
                request.method,
                request.url.path,
                request.headers.get("host"),
                request.headers.get("origin"),
                request.headers.get("authorization"),
                request.headers.get("accept"),
            ),
            flush=True,
        )
        return await call_next(request)


def main():
    parser = argparse.ArgumentParser(description="采购履约异常审查 MCP Server")
    parser.add_argument(
        "--transport",
        default=config.MCP_TRANSPORT,
        choices=["stdio", "streamable-http"],
        help="stdio（本地调试）或 streamable-http（供 Dify 连接）",
    )
    args = parser.parse_args()
    init_database()
    if args.transport == "stdio":
        mcp.run()
    else:
        app = mcp.streamable_http_app()
        app.add_middleware(_RequestLogMiddleware)
        uvicorn.run(app, host=config.MCP_HOST, port=config.MCP_PORT, log_level="info")


if __name__ == "__main__":
    main()