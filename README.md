# 采购履约异常审查 Agent

**面向小型企业的采购单据异常审查与条款解释 Agent。**

数据库保存采购单、采购商品明细、收货明细、发票明细，系统自动核对数量、单价、
重复发票与缺失单据，检索 RAGFlow 知识库中的合同/政策条款并解释异常，供 Dify Agent 引用。

## 架构

```
Dify Agent（模型推理、问题路由、结果整合）
   │  MCP 协议
   ▼
MCP Server（5 个工具：业务查询 / 核对 / 知识检索）
   │
   ├── Python 服务层 ── MySQL（采购单/明细/收货/发票/异常/审核日志）
   │        └── 确定性核对引擎（数量/单价/重复/缺失，不依赖大模型计算）
   └── RAGFlow 知识库（合同条款 / 验收政策检索）
```

**职责分工**：

- MySQL：结构化业务数据与处理历史的持久化（库名 `procurement_agent`）
- Python 服务层：核对引擎的确定性计算（数量/单价/重复/缺失）
- RAGFlow：非结构化合同条款的解析与检索，提供引用来源
- MCP Server：把业务能力封装为 Dify 可调用、权限受限的工具
- Dify Agent：结合业务数据与规则生成有证据的解释与建议

## 数据模型（6 张表）

| 表 | 说明 |
|---|---|
| purchase_orders | 采购单主表 |
| purchase_order_items | 采购商品明细 |
| receipt_items | 收货明细（外键 `po_item_id` 指向采购商品明细） |
| invoice_items | 发票明细（外键 `po_item_id` 指向采购商品明细） |
| exception_cases | 异常记录 |
| review_logs | 人工审核记录 |

收货/发票明细通过 `po_item_id` 直接关联采购商品明细，核对引擎无需名称模糊匹配。

## MCP 工具

| 工具 | 作用 |
|---|---|
| get_purchase_case | 获取采购单摘要、商品明细、收货明细、发票明细与异常状态汇总 |
| run_match_check | 对指定采购单执行确定性核对，返回全部异常及证据 |
| get_exception_detail | 获取异常详情与证据 |
| list_open_exceptions | 列出所有待处理（open / under_review）异常 |
| search_contract_rules | 按异常类型检索 RAGFlow 合同/政策条款原文 |

## 核对规则（4 类异常）

| 类型 | 规则 |
|---|---|
| QTY_EXCEEDED | 开票数量超过已登记收货数量 |
| UNIT_PRICE_MISMATCH | 发票单价与采购单价偏差超过容差（默认 ±2%） |
| DUPLICATE_INVOICE | 同一发票编号重复 |
| MISSING_DOCUMENT | 有发票但缺少收货记录 |

## 目录结构

```
├── backend/
│   ├── config.py                # 全局配置（加载 .env）
│   ├── ragflow_client.py        # RAGFlow 检索客户端
│   ├── models/                  # db.py（连接）、schemas.py（Pydantic 模型）
│   ├── services/                # 核对引擎、采购单、异常
│   └── mcp_server/server.py     # MCP Server（5 个工具）
├── database/
│   ├── schema.sql               # 6 张表建表语句
│   └── seed_data.py             # 生成模拟数据（30 采购单 + 62 异常）
├── scripts/                     # RAGFlow 诊断、批量核对、MCP 冒烟测试
├── tests/                       # 核对引擎单元测试
├── requirements.txt
└── .env.example
```

## 快速开始

环境：Python 3.11+、本机 MySQL 8。

```bash
# 1. 安装依赖
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 2. 配置 .env（从 .env.example 复制，填数据库账号、RAGFlow/Dify Key）

# 3. 建库建表并生成模拟数据
.venv\Scripts\python.exe -m database.seed_data

# 4. 启动 MCP Server
#    streamable-http（供 Dify 连接）
.venv\Scripts\python.exe -m backend.mcp_server.server --transport streamable-http
#    stdio（本地调试）
.venv\Scripts\python.exe -m backend.mcp_server.server --transport stdio

# 5. 测试与冒烟
.venv\Scripts\python.exe -m pytest tests -q
.venv\Scripts\python.exe scripts\test_mcp.py
```

## 已知限制

- 测试集为 30 组模拟数据，不能据此宣称适用于所有真实企业。
- 不包含单据 OCR / 解析；扫描件与 PDF 识别列为可选增强。
- RAGFlow / Dify 联调需自行配置 Key 与知识库。