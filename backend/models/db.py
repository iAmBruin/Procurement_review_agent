"""MySQL 连接工具：基于 pymysql，每次操作获取短连接。"""
import pymysql

from backend import config


def get_connection():
    """获取一个 MySQL 连接（自动提交关闭，由调用方管理事务）。"""
    return pymysql.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        database=config.DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def init_database():
    """建库（若不存在）并应用 schema.sql。"""
    conn = pymysql.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )
    with conn.cursor() as cur:
        cur.execute(
            f"CREATE DATABASE IF NOT EXISTS `{config.DB_NAME}` "
            "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
    conn.close()

    schema_path = config.BASE_DIR / "database" / "schema.sql"
    with open(schema_path, "r", encoding="utf-8") as f:
        raw = f.read()
    # 剔除整行注释后按分号切分语句
    lines = [ln for ln in raw.splitlines() if not ln.strip().startswith("--")]
    sql = "\n".join(lines)
    conn = get_connection()
    with conn.cursor() as cur:
        for statement in sql.split(";"):
            stmt = statement.strip()
            if stmt:
                cur.execute(stmt)
    conn.close()
