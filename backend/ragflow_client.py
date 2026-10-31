"""
    配置RAGFlow连接
    获取知识库数据集
    设置检索关键词
"""

from __future__ import annotations
from typing import Optional
import requests
from backend import config


def _headers() -> dict:
    """ 身份验证 """
    return {"Authorization": f"Bearer {config.RAGFLOW_API_KEY}"}


def configured() -> bool:
    """ 判断是否已配置  """
    return bool(config.RAGFLOW_BASE_URL and config.RAGFLOW_API_KEY)


def list_datasets() -> list[dict]:
    """列出数据集 """
    resp = requests.get(f"{config.RAGFLOW_BASE_URL}/api/v1/datasets", headers=_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json().get("data", [])


def search(query: str, dataset_id: Optional[str] = None, top_k: int = 5) -> list[dict]:
    """检索知识库，返回带来源的片段列表。"""
    if not configured():
        return []
    payload = {
        "question": query,
        "top_k": top_k,
        "dataset_ids": [dataset_id] if dataset_id else [ds["id"] for ds in list_datasets()],
    }
    resp = requests.post(
        f"{config.RAGFLOW_BASE_URL}/api/v1/retrieval",
        headers=_headers(),
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    chunks = resp.json().get("data", {}).get("chunks", [])
    return [
        {
            "content": c.get("content", ""),
            "document_name": c.get("document_name", ""),
            "docnm_kwd": c.get("docnm_kwd", ""),
            "score": c.get("similarity", c.get("score")),
            "page": c.get("page_number"),
        }
        for c in chunks
    ]


# 异常类型 -> 检索关键词（供 Agent 查询合同/政策条款原文用）
# 根据采购异常的类型，自动选择适合检索合同条款的关键词
EXCEPTION_QUERY_MAP = {
    "QTY_EXCEEDED": "开票数量 收货数量 结算 依据",
    "UNIT_PRICE_MISMATCH": "单价 偏差 容差 人工复核",
    "DUPLICATE_INVOICE": "发票 重复 编号 冻结",
    "MISSING_DOCUMENT": "收货单 缺失 到货 核实",
}