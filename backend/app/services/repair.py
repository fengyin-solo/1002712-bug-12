"""箱体修洗业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

import copy
from typing import Any

from app.store import store

MODULE = 'repair'
REQUIRED_FIELDS = ['任务编号', '箱号', '损伤类型']
STATUS_ORDER = ['待修洗', '修理中', '待验收', '已完成']
ACTION_RULES = {'安排修洗': '修理中', '开始修理': '待验收', '验收完成': '已完成'}
NEGATIVE_ACTIONS = []


class RepairService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.snapshot_rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get('任务编号', ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return copy.deepcopy(entry) if entry is not None else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing

        def commit() -> dict[str, Any]:
            rows = store.rows(MODULE)
            entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
            entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
            entry["status"] = STATUS_ORDER[0]
            entry["pending"] = True
            entry["abnormal"] = False
            rows.append(entry)
            return copy.deepcopy(entry)

        entry = store.transaction(commit)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        def commit() -> tuple[dict[str, Any] | None, str]:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"修洗任务 {entry_id} 不存在或已归档"
            if action not in ACTION_RULES:
                return None, f"动作「{action}」不属于箱体修洗可执行范围"
            target = ACTION_RULES[action]
            if target not in STATUS_ORDER:
                return None, f"目标状态「{target}」不在允许的状态序列里"
            entry["status"] = target
            entry["pending"] = target != STATUS_ORDER[-1]
            entry["abnormal"] = action in NEGATIVE_ACTIONS
            return copy.deepcopy(entry), f"修洗任务已{action}"

        return store.transaction(commit)
