"""全局配置：从环境变量加载，支持 .env 文件。"""
import os
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录（backend 的上一级）
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# ---- 数据库 ----
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "procurement_agent")

# ---- 后端服务 ----
BACKEND_HOST = os.getenv("BACKEND_HOST", "127.0.0.1")
BACKEND_PORT = int(os.getenv("BACKEND_PORT", "8000"))

# ---- RAGFlow ----
RAGFLOW_BASE_URL = os.getenv("RAGFLOW_BASE_URL", "").rstrip("/")
RAGFLOW_API_KEY = os.getenv("RAGFLOW_API_KEY", "")
RAGFLOW_DATASET = os.getenv("RAGFLOW_DATASET", "procurement_rules")

# ---- Dify ----
DIFY_BASE_URL = os.getenv("DIFY_BASE_URL", "")
DIFY_APP_API_KEY = os.getenv("DIFY_APP_API_KEY", "")

# ---- MCP ----
MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "stdio")
MCP_HOST = os.getenv("MCP_HOST", "127.0.10.1")
MCP_PORT = int(os.getenv("MCP_PORT", "8911"))

# ---- 核对引擎默认规则 ----
DEFAULT_PRICE_TOLERANCE = 0.02  # 单价容差 ±2%
