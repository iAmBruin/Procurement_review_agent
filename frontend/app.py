"""Streamlit 前端：采购单总览 -> 核对 -> 异常详情 -> 人工复核。

启动：
  streamlit run frontend/app.py --server.port 8501
前提：FastAPI 后端已在 127.0.0.1:8000 运行。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import requests
import streamlit as st
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

API_BASE = os.getenv("BACKEND_HOST", "127.0.0.1") + ":" + str(os.getenv("BACKEND_PORT", "8000"))
API = f"http://{API_BASE.replace('//', '')}/api/v1"
DIFY_BASE = os.getenv("DIFY_BASE_URL", "")
DIFY_KEY = os.getenv("DIFY_APP_API_KEY", "")

STATUS_ZH = {
    "open": "待处理",
    "under_review": "复核中",
    "confirmed": "已确认",
    "rejected": "已驳回",
    "resolved": "已解决",
}
PO_STATUS_ZH = {
    "draft": "草稿",
    "open": "进行中",
    "partially_received": "部分收货",
    "received": "已收货",
    "closed": "已关闭",
    "cancelled": "已取消",
}
TYPE_ZH = {
    "QTY_EXCEEDED": "数量异常（开票超收货）",
    "UNIT_PRICE_MISMATCH": "单价偏差",
    "DUPLICATE_INVOICE": "重复发票",
    "MISSING_DOCUMENT": "单据缺失",
}
SEV_ZH = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}
ACTION_ZH = {
    "confirm": "确认异常",
    "reject": "驳回",
    "resolve": "已解决",
    "request_documents": "要求补充资料",
    "comment": "仅评论",
}

st.set_page_config(page_title="采购履约异常审查 Agent", page_icon="📋", layout="wide")


def api_get(path: str, **kw):
    r = requests.get(f"{API}{path}", timeout=30, **kw)
    r.raise_for_status()
    return r.json()


def api_post(path: str, json=None):
    r = requests.post(f"{API}{path}", json=json, timeout=120)
    r.raise_for_status()
    return r.json()


def fmt(v):
    if isinstance(v, float):
        return f"{v:.2f}".rstrip("0").rstrip(".")
    return "" if v is None else v


def order_selector(label: str = "选择采购单", key: str = "po_id"):
    try:
        orders = api_get("/orders")
    except requests.exceptions.RequestException:
        st.error("无法连接后端，请确认 FastAPI 已在 127.0.0.1:8000 启动。")
        st.stop()
    if not orders:
        st.info("暂无采购单数据，请先运行 `python -m database.seed_data` 生成数据。")
        st.stop()
    options = {f"{o['po_number']} · {o['supplier_name']} (待处理{o.get('open_exception_count', 0)})": o["id"] for o in orders}
    choice = st.selectbox(label, list(options), key=key)
    return options[choice]


# ------------------------------------------------------------------ 页面 1：总览
def page_overview():
    st.header("① 采购单总览")
    try:
        stats = api_get("/stats")
    except requests.exceptions.RequestException:
        st.error("无法连接后端。")
        st.stop()

    c1, c2, c3 = st.columns(3)
    c1.metric("采购单总数", stats["total_po"])
    c2.metric("异常总数", stats["total_exceptions"])
    c3.metric("待处理异常", stats["open_exceptions"])

    if stats.get("by_type"):
        st.subheader("异常类型分布")
        st.dataframe(
            [{"类型": TYPE_ZH.get(k, k), "数量": v} for k, v in stats["by_type"].items()],
            use_container_width=True, hide_index=True,
        )

    st.subheader("采购单列表")
    try:
        orders = api_get("/orders")
    except requests.exceptions.RequestException:
        st.warning("加载采购单失败")
        return
    st.dataframe(
        [{
            "采购单号": o["po_number"],
            "供应商": o["supplier_name"],
            "状态": PO_STATUS_ZH.get(o["status"], o["status"]),
            "明细条数": o["item_count"],
            "异常数": o["exception_count"],
            "待处理": o["open_exception_count"],
        } for o in orders],
        use_container_width=True, hide_index=True,
    )


# ------------------------------------------------------------------ 页面 2：采购单详情
def page_order_detail():
    st.header("② 采购单详情与核对")
    po_id = order_selector(key="detail_po")

    def load():
        return api_get(f"/orders/{po_id}")

    details = load()
    po = details["po"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("采购单号", po["po_number"])
    c2.metric("供应商", po["supplier_name"])
    c3.metric("状态", PO_STATUS_ZH.get(po["status"], po["status"]))
    c4.metric("币种", po.get("currency") or "CNY")

    if st.button("执行核对", type="primary", key="do_check"):
        with st.spinner("核对中……"):
            result = api_post(f"/orders/{po_id}/check")
            st.success(f"核对完成，共检出 {len(result['exceptions'])} 条异常")
            details = load()

    st.subheader("商品明细")
    if details["items"]:
        st.dataframe(
            [{
                "行号": i["line_no"], "SKU": i["sku"], "商品": i["product_name"],
                "规格": i["specification"], "采购数量": fmt(i["ordered_qty"]),
                "单位": i["unit"], "采购单价": fmt(i["unit_price"]),
            } for i in details["items"]],
            use_container_width=True, hide_index=True,
        )

    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("收货明细")
        if details["receipts"]:
            st.dataframe(
                [{"收货单号": r["receipt_no"], "商品": r["product_name"],
                  "收货数量": fmt(float(r["received_qty"])), "日期": r.get("received_date") or "-"} for r in details["receipts"]],
                use_container_width=True, hide_index=True,
            )
        else:
            st.info("无收货记录")
    with col_b:
        st.subheader("发票明细")
        if details["invoices"]:
            st.dataframe(
                [{"发票号": v["invoice_no"], "商品": v["product_name"],
                  "开票数量": fmt(float(v["invoiced_qty"])), "发票单价": fmt(float(v["unit_price"])),
                  "日期": v.get("invoice_date") or "-"} for v in details["invoices"]],
                use_container_width=True, hide_index=True,
            )
        else:
            st.info("无发票记录")

    st.subheader("异常列表")
    if details["exceptions"]:
        st.dataframe(
            [{
                "ID": e["id"], "类型": TYPE_ZH.get(e["exception_type"], e["exception_type"]),
                "严重度": SEV_ZH.get(e["severity"], e["severity"]),
                "状态": STATUS_ZH.get(e["status"], e["status"]),
                "标题": e["title"],
            } for e in details["exceptions"]],
            use_container_width=True, hide_index=True,
        )
    else:
        st.info("暂无异常")


# ------------------------------------------------------------------ 页面 3：异常详情
def page_exception_detail():
    st.header("③ 异常详情与证据")
    po_id = order_selector(key="exc_po")
    details = api_get(f"/orders/{po_id}")
    exceptions = details["exceptions"]
    if not exceptions:
        st.info("该采购单暂无异常。")
        return
    options = {f"#{e['id']} {TYPE_ZH.get(e['exception_type'], e['exception_type'])}": e["id"] for e in exceptions}
    exc_id = st.selectbox("选择异常", list(options), key="exc_sel")
    e = [x for x in exceptions if x["id"] == options[exc_id]][0]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("类型", TYPE_ZH.get(e["exception_type"], e["exception_type"]))
    c2.metric("严重度", SEV_ZH.get(e["severity"], e["severity"]))
    c3.metric("状态", STATUS_ZH.get(e["status"], e["status"]))
    c4.metric("编号", e["exception_code"])
    st.write(f"**标题**：{e['title']}")
    st.write(f"**说明**：{e['description']}")

    m1, m2, m3 = st.columns(3)
    m1.metric("参考值", fmt(e.get("expected_value")))
    m2.metric("实际值", fmt(e.get("actual_value")))
    m3.metric("差值", fmt(e.get("difference_value")))

    st.subheader("证据摘要")
    if e.get("evidence_json"):
        st.json(e["evidence_json"])
    else:
        st.info("无附加证据")

    st.subheader("询问 Agent")
    if st.button("生成解释与处理建议", key="ask_agent"):
        with st.spinner("Agent 思考中……"):
            st.markdown(explain_with_agent(e))


def explain_with_agent(e: dict) -> str:
    """优先调用 Dify 应用（streaming）；未配置时本地生成解释。"""
    if DIFY_BASE and DIFY_KEY:
        try:
            answer = call_dify_streaming(
                f"异常 #{e['id']}（{e['exception_type']}）：{e['title']}。请给出解释与处理建议。"
            )
            if answer:
                return answer
        except requests.exceptions.RequestException as ex:
            st.warning(f"Dify 调用异常：{ex}")

    return (
        f"**异常原因**：{e['description']}\n\n"
        f"**核对结果**：参考值 {fmt(e.get('expected_value'))}，实际值 {fmt(e.get('actual_value'))}，"
        f"差值 {fmt(e.get('difference_value'))}。\n\n"
        f"**证据**：{json.dumps(e.get('evidence_json') or {}, ensure_ascii=False)}\n\n"
        f"**建议**：由人工核对是否存在未登记收货/补充资料，再于「人工复核」页面处理。\n\n"
        f"> 配置 Dify（DIFY_BASE_URL / DIFY_APP_API_KEY）后，此处返回 Agent 生成的解释。"
    )


def call_dify_streaming(query: str) -> str:
    resp = requests.post(
        f"{DIFY_BASE}/chat-messages",
        headers={"Authorization": f"Bearer {DIFY_KEY}"},
        json={"inputs": {}, "query": query, "response_mode": "streaming", "user": "demo"},
        stream=True, timeout=120,
    )
    resp.raise_for_status()
    parts: list[str] = []
    for line in resp.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            obj = json.loads(data)
        except ValueError:
            continue
        ev = obj.get("event")
        if ev == "message":
            parts.append(obj.get("answer", ""))
        elif ev == "message_end":
            break
    return "".join(parts)


# ------------------------------------------------------------------ 页面 4：人工复核
def page_review():
    st.header("④ 人工复核")
    st.caption("选择处理结论并填备注；系统会把每次动作写入 review_logs 形成审计轨迹。")
    try:
        open_list = api_get("/exceptions/open")
    except requests.exceptions.RequestException:
        st.error("无法连接后端。")
        st.stop()
    if not open_list:
        st.info("没有待处理的异常。")
        return

    options = {f"#{e['id']} [{e['po_number']}] {TYPE_ZH.get(e['exception_type'], e['exception_type'])}": e["id"] for e in open_list}
    exc_id = st.selectbox("选择待处理异常", list(options), key="rev_sel")
    target = [e for e in open_list if e["id"] == options[exc_id]][0]
    st.write(f"**{target['title']}** —— {target['description']}")

    with st.form("action_form"):
        action = st.radio(
            "处理结论", ["confirm", "reject", "resolve", "request_documents", "comment"],
            format_func=lambda a: ACTION_ZH[a], horizontal=True,
        )
        comment = st.text_area("备注", placeholder="例如：已联系供应商补充收货记录")
        reviewer = st.text_input("操作人", value="demo_user")
        if st.form_submit_button("提交"):
            try:
                api_post(f"/exceptions/{target['id']}/actions",
                         json={"action": action, "comment": comment, "reviewer": reviewer})
                st.success("已保存处理结果")
                st.rerun()
            except requests.exceptions.HTTPError as ex:
                st.error(f"提交失败：{ex.response.json().get('detail', ex)}")

    st.divider()
    st.subheader("说明")
    st.caption("完整处理历史可通过接口 GET /exceptions/{id}/logs 查询。")


# ------------------------------------------------------------------ 导航
def main():
    st.sidebar.title("📋 采购履约异常审查 Agent")
    st.sidebar.caption("结构化采购单核对（PO / 收货 / 发票）")
    page = st.sidebar.radio("导航", ["采购单总览", "采购单详情", "异常详情", "人工复核"])
    st.sidebar.divider()
    st.sidebar.caption(f"后端：{API}")
    if page == "采购单总览":
        page_overview()
    elif page == "采购单详情":
        page_order_detail()
    elif page == "异常详情":
        page_exception_detail()
    else:
        page_review()


if __name__ == "__main__":
    main()