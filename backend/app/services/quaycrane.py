"""岸桥调度业务规则：状态流转、字段校验、筛选口径，以及作业效率的统一口径。

效率值不在本模块内存行上各自维护，一律从 ``crane_efficiency`` 台账投影：
分配作业、释放岸桥都按同一份台账记录统一重算，保证面板、详情、刷新后读到同一个数。
"""
from __future__ import annotations

from typing import Any

from app.services.crane_efficiency import (
    ConflictError,
    ValidationError,
    crane_efficiency,
)
from app.store import store

MODULE = "quaycrane"
REQUIRED_FIELDS = ["岸桥编号", "岸桥型号", "额定起重量"]
STATUS_ORDER = ["空闲", "作业中", "检修中", "已停用"]
# 动作到目标状态的映射；分配与释放同时触发效率台账的统一重算。
ACTION_RULES = {"分配作业": "作业中", "释放岸桥": "空闲", "登记检修": "检修中"}
# 需要带计量数据重算效率的动作。
EFFICIENCY_ACTIONS = {"分配作业", "释放岸桥"}
NEGATIVE_ACTIONS = []

# 台账投影到岸桥记录上的字段：任何页面读到的作业效率都来自这一份。
LEDGER_FIELDS = [
    "作业效率",
    "效率档位",
    "作业箱量",
    "作业分钟",
    "操作司机",
    "作业船舶",
    "algorithm_version",
    "version",
    "updated_at",
    "updated_by",
]


class QuaycraneService:
    def _project(self, row: dict[str, Any]) -> dict[str, Any]:
        """把效率台账投影到岸桥行上（不改动原始行），保证读数同源。"""
        projected = dict(row)
        ledger = crane_efficiency.value_map().get(str(row.get("岸桥编号", "")))
        if ledger is not None:
            for field in LEDGER_FIELDS:
                projected[field] = ledger.get(field)
        return projected

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("岸桥编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        page_rows = [self._project(row) for row in rows[start:start + size]]
        return page_rows, total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        return self._project(row) if row else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        crane_efficiency.register(str(entry.get("岸桥编号", "")).strip())
        return self._project(entry), []

    def run_action(
        self, entry_id: int, action: str, values: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str, str]:
        """执行调度动作。

        返回 (记录, 消息, 结果类型)，结果类型为 ok / conflict / error：
        - 分配作业、释放岸桥先走效率台账统一重算：晚到提交返回 conflict（已被占用），
          越界/缺字段返回 error，状态一律不流转；
        - 台账写入成功后再改岸桥状态，保证效率和状态同一次落库。
        """
        values = values or {}
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"岸桥 {entry_id} 不存在或已归档", "error"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于岸桥调度可执行范围", "error"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里", "error"

        if action in EFFICIENCY_ACTIONS:
            crane_code = str(entry.get("岸桥编号", "")).strip()
            try:
                crane_efficiency.submit(crane_code, values)
            except ConflictError as exc:
                return self._project(entry), str(exc), "conflict"
            except ValidationError as exc:
                return None, str(exc), "error"

        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return self._project(entry), f"岸桥已{action}，作业效率已按统一口径重算", "ok"
