"""RAGFlow 客户端（阶段 6）：数据集创建、文档上传、检索。

对接 RAGFlow HTTP API（需要登录后在 头像 -> API Key 中创建 Key）。
RAGFlow 尚未部署/注册账号时，search_rules 会回退到结构化规则，
保证核心链路不依赖外部服务。
"""
from __future__ import annotations

from typing import Optional

import requests

from backend import config


def _headers() -> dict:
    return {"Authorization": f"Bearer {config.RAGFLOW_API_KEY}"}


def configured() -> bool:
    return bool(config.RAGFLOW_BASE_URL and config.RAGFLOW_API_KEY)


# ------------------------------------------------------------------ 数据集
def list_datasets() -> list[dict]:
    """列出全部数据集。"""
    resp = requests.get(f"{config.RAGFLOW_BASE_URL}/api/v1/datasets", headers=_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json().get("data", [])


def get_or_create_dataset(name: str = config.RAGFLOW_DATASET) -> dict:
    """按名称查找数据集，不存在则创建。"""
    for ds in list_datasets():
        if ds.get("name") == name:
            return ds
    resp = requests.post(
        f"{config.RAGFLOW_BASE_URL}/api/v1/datasets",
        headers=_headers(),
        json={"name": name, "parse_type": 1, "parser_id": "naive"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("data", {})


# ------------------------------------------------------------------ 文档
def upload_document(dataset_id: str, file_path: str) -> dict:
    """上传文档到数据集并触发解析。"""
    with open(file_path, "rb") as f:
        resp = requests.post(
            f"{config.RAGFLOW_BASE_URL}/api/v1/datasets/{dataset_id}/documents",
            headers=_headers(),
            files={"file": f},
            timeout=120,
        )
    resp.raise_for_status()
    return resp.json()


def upload_knowledge_dir(knowledge_dir: str) -> dict:
    """批量上传 knowledge/ 目录下全部文档，并触发解析。"""
    import glob

    dataset = get_or_create_dataset()
    results = []
    doc_ids: list[str] = []
    for path in sorted(glob.glob(str(knowledge_dir) + "/*")):
        if path.lower().endswith((".md", ".docx", ".pdf", ".txt")):
            resp = upload_document(dataset["id"], path)
            results.append(resp)
            docs = (resp.get("data") or [])
            doc_list = docs if isinstance(docs, list) else [docs]
            for d in doc_list:
                if d.get("id"):
                    doc_ids.append(d["id"])
    if doc_ids:
        trigger_parse(dataset["id"], doc_ids)
    return {"dataset_id": dataset["id"], "documents": results, "parsed_doc_ids": doc_ids}


def trigger_parse(dataset_id: str, document_ids: list[str]) -> dict:
    """触发文档解析（切块 + embedding 索引）。"""
    resp = requests.post(
        f"{config.RAGFLOW_BASE_URL}/api/v1/datasets/{dataset_id}/documents/parse",
        headers=_headers(),
        json={"document_ids": document_ids},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ------------------------------------------------------------------ 检索
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
EXCEPTION_QUERY_MAP = {
    "QTY_EXCEEDED": "开票数量 收货数量 结算 依据",
    "UNIT_PRICE_MISMATCH": "单价 偏差 容差 人工复核",
    "DUPLICATE_INVOICE": "发票 重复 编号 冻结",
    "MISSING_DOCUMENT": "收货单 缺失 到货 核实",
}
