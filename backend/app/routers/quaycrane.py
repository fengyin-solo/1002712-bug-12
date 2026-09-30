"""岸桥调度接口：维护岸桥，覆盖分配作业、释放岸桥、登记检修等动作。

效率值不在动作里直接覆盖：所有提交都进入同一份效率记录统一重算。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.quaycrane import QuaycraneError, QuaycraneService

router = APIRouter(prefix="/api/quaycrane", tags=["岸桥调度"])

service = QuaycraneService()

LIST_FIELDS = ["岸桥编号", "岸桥型号", "额定起重量", "吊具类型", "作业船舶", "作业效率", "操作司机", "岸桥状态"]
STATUSES = ["空闲", "作业中", "检修中", "已停用"]

# 业务拒绝码 -> HTTP 状态
ERROR_STATUS = {
    "not_found": 404,
    "conflict": 409,
    "out_of_rating": 422,
    "invalid": 400,
}


def _raise(error: QuaycraneError) -> None:
    raise HTTPException(status_code=ERROR_STATUS.get(error.code, 400), detail=error.message)


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按岸桥编号检索"),
    status: str | None = Query(default=None, description="空闲、作业中、检修中、已停用"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按岸桥编号与状态过滤岸桥调度列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/efficiency-summary")
def efficiency_summary() -> dict[str, Any]:
    """岸桥效率的同源读取口：其他页面（如工班管理）统一从这里取效率。"""
    return service.fleet_efficiency()


@router.post("/recalculate-efficiency")
def recalculate_efficiency() -> dict[str, Any]:
    """效率算法换版后，按已有流水重算全部岸桥效率记录。"""
    return service.recompute_all_efficiency()


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条岸桥明细（含同一份效率记录与版本号）；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"岸桥 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条岸桥，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="岸桥已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """分配作业、提交作业量、释放岸桥、登记检修，都在同一份效率记录上重算。

    并发冲突（409）与越界（422）用明确的中文原因返回，不覆盖先落库的值。
    """
    try:
        entry = service.submit_efficiency(entry_id, payload.values)
    except QuaycraneError as error:
        if error.code == "not_found":
            raise HTTPException(status_code=404, detail=error.message)
        _raise(error)
    action = str(payload.values.get("action") or "").strip()
    return ActionResult(ok=True, message=f"岸桥已{action}，效率已按统一记录重算", entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出岸桥调度清单：返回当前过滤条件下的全量数据，效率与列表同源。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "quaycrane", "total": total, "items": items}
