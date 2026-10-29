"""异常服务：读写 exception_cases 与 review_logs（新模型）。"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from backend.models.db import get_connection
from backend.models.schemas import ExceptionCase, ReviewLog

# 审核动作 -> 异常目标状态
ACTION_STATUS_MAP = {
    "confirm": "confirmed",
    "reject": "rejected",
    "resolve": "resolved",
    "request_documents": "under_review",
}
VALID_ACTIONS = set(ACTION_STATUS_MAP) | {"comment"}

_EXC_SELECT = (
    "SELECT e.*, p.po_number, p.supplier_name "
    "FROM exception_cases e JOIN purchase_orders p ON p.id = e.po_id "
)


def _dump(value) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _load_json(value, default=None):
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _row_to_exception(row: dict) -> ExceptionCase:
    return ExceptionCase(
        id=row["id"],
        exception_code=row["exception_code"],
        po_id=row["po_id"],
        po_number=row.get("po_number"),
        supplier_name=row.get("supplier_name"),
        po_item_id=row.get("po_item_id"),
        exception_type=row["exception_type"],
        severity=row["severity"],
        status=row["status"],
        title=row["title"],
        description=row.get("description"),
        expected_value=_dec_or_none(row.get("expected_value")),
        actual_value=_dec_or_none(row.get("actual_value")),
        difference_value=_dec_or_none(row.get("difference_value")),
        evidence_json=_load_json(row.get("evidence_json"), default={}),
        detected_at=row.get("detected_at"),
        reviewed_at=row.get("reviewed_at"),
        resolved_at=row.get("resolved_at"),
    )


def _dec_or_none(v):
    return float(v) if v is not None else None


def save_exceptions(po_id: int, exceptions: list[ExceptionCase]) -> list[ExceptionCase]:
    """批量写入异常，生成 exception_code，返回带 id 的完整对象。"""
    if not exceptions:
        return []
    with get_connection() as conn:
        with conn.cursor() as cur:
            for exc in exceptions:
                exc.po_id = po_id
                cur.execute(
                    """INSERT INTO exception_cases
                       (exception_code, po_id, po_item_id, exception_type, severity,
                        status, title, description, expected_value, actual_value,
                        difference_value, evidence_json)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        "TMP", po_id, exc.po_item_id, exc.exception_type, exc.severity,
                        exc.status, exc.title, exc.description,
                        exc.expected_value, exc.actual_value, exc.difference_value,
                        _dump(exc.evidence_json),
                    ),
                )
                exc.id = cur.lastrowid
                exc.exception_code = f"EX-{exc.id:05d}"
                cur.execute(
                    "UPDATE exception_cases SET exception_code = %s WHERE id = %s",
                    (exc.exception_code, exc.id),
                )
    return exceptions


def list_exceptions(po_id: int, status: Optional[str] = None) -> list[ExceptionCase]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            if status:
                cur.execute(_EXC_SELECT + "WHERE e.po_id = %s AND e.status = %s ORDER BY e.id", (po_id, status))
            else:
                cur.execute(_EXC_SELECT + "WHERE e.po_id = %s ORDER BY e.id", (po_id,))
            return [_row_to_exception(r) for r in cur.fetchall()]


def get_exception(exception_id: int) -> Optional[ExceptionCase]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_EXC_SELECT + "WHERE e.id = %s", (exception_id,))
            row = cur.fetchone()
            return _row_to_exception(row) if row else None


def list_open_exceptions() -> list[ExceptionCase]:
    """查询所有未处理完的异常（open / under_review）。"""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_EXC_SELECT + "WHERE e.status IN ('open','under_review') ORDER BY e.po_id, e.id")
            return [_row_to_exception(r) for r in cur.fetchall()]


def count_by_status(po_id: int) -> dict[str, int]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, COUNT(*) AS cnt FROM exception_cases WHERE po_id = %s GROUP BY status",
                (po_id,),
            )
            return {r["status"]: r["cnt"] for r in cur.fetchall()}


def save_review_log(log: ReviewLog) -> ReviewLog:
    """记录一次审核动作，并同步更新异常状态与审核时间。"""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status FROM exception_cases WHERE id = %s", (log.exception_id,)
            )
            row = cur.fetchone()
            if not row:
                raise ValueError(f"异常不存在：id={log.exception_id}")
            from_status = row["status"]
            to_status = ACTION_STATUS_MAP.get(log.action)

            cur.execute(
                """INSERT INTO review_logs
                   (exception_id, action, from_status, to_status, reviewer, comment)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (log.exception_id, log.action, from_status, to_status, log.reviewer, log.comment),
            )
            log.id = cur.lastrowid
            log.from_status = from_status
            log.to_status = to_status

            if to_status:
                now = datetime.now()
                cur.execute(
                    "UPDATE exception_cases SET status = %s, reviewed_at = %s WHERE id = %s",
                    (to_status, now, log.exception_id),
                )
                if to_status == "resolved":
                    cur.execute(
                        "UPDATE exception_cases SET resolved_at = %s WHERE id = %s",
                        (now, log.exception_id),
                    )
    return log


def list_review_logs(exception_id: int) -> list[ReviewLog]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM review_logs WHERE exception_id = %s ORDER BY id",
                (exception_id,),
            )
            return [_row_to_log(r) for r in cur.fetchall()]


def _row_to_log(row: dict) -> ReviewLog:
    return ReviewLog(
        id=row["id"],
        exception_id=row["exception_id"],
        action=row["action"],
        from_status=row.get("from_status"),
        to_status=row.get("to_status"),
        reviewer=row["reviewer"],
        comment=row.get("comment"),
        created_at=row.get("created_at"),
    )