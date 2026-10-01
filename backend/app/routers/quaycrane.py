"""岸桥调度接口：维护岸桥，覆盖分配作业、释放岸桥、登记检修等动作。

作业效率的读写统一走效率台账（app.services.crane_efficiency），本路由只做参数收发，
业务判断（先落库为准、档位越界、算法重算）都收在服务层。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.crane_efficiency import (
    RATED_BY_GEAR,
    ConflictError,
    ValidationError,
    crane_efficiency,
)
from app.services.quaycrane import QuaycraneService

router = APIRouter(prefix="/api/quaycrane", tags=["岸桥调度"])

service = QuaycraneService()

LIST_FIELDS = ["岸桥编号", "岸桥型号", "额定起重量", "吊具类型", "作业船舶", "作业效率", "效率档位", "操作司机", "岸桥状态"]
STATUSES = ["空闲", "作业中", "检修中", "已停用"]


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


@router.get("/efficiency")
def list_efficiency() -> dict[str, Any]:
    """岸桥效率台账：交班页等其他页面统一从这里读数，与调度面板同源。"""
    items = crane_efficiency.list_records()
    return {
        "items": items,
        "total": len(items),
        "rated_by_gear": RATED_BY_GEAR,
    }


@router.post("/efficiency/recalculate")
def recalculate_efficiency() -> dict[str, Any]:
    """效率算法升级后，按当前算法统一重算已有的效率记录。"""
    result = crane_efficiency.recalculate_all()
    return {"ok": True, "message": "存量效率记录已按当前算法重算", **result}


@router.get("/efficiency/{crane_code}")
def get_efficiency(crane_code: str) -> dict[str, Any]:
    """读取单台岸桥的效率台账（详情页改完重新进入时取的就是这份）。"""
    record = crane_efficiency.get_record(crane_code)
    if record is None:
        raise HTTPException(status_code=404, detail=f"岸桥 {crane_code} 的效率台账不存在")
    return record


@router.post("/efficiency/{crane_code}/submit")
def submit_efficiency(crane_code: str, payload: EntryPayload) -> ActionResult:
    """效率修正：与分配/释放共用同一份台账记录与同一套重算逻辑。

    档位越界返回 400 且不落库；提交晚于他人（expected_version 过期）返回 409，
    仅提示已被占用，不覆盖先落库的值。
    """
    try:
        record = crane_efficiency.submit(crane_code, payload.values)
    except ConflictError as exc:
        current = crane_efficiency.get_record(crane_code)
        return ActionResult(ok=False, conflict=True, message=str(exc), entry=current)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ActionResult(ok=True, message="作业效率已按统一口径重算", entry=record)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出岸桥调度清单：返回当前过滤条件下的全量数据（效率同样取自台账）。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "quaycrane", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条岸桥明细；不存在时给出可读的错误说明。"""
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
    """对单条岸桥执行分配作业、释放岸桥、登记检修。

    分配与释放会先在效率台账上统一重算：越界或缺字段时拒绝并说明原因；
    别人已先落库时只提示已被占用（conflict），本次提交不覆盖任何值。
    """
    action = str(payload.values.get("action") or "").strip()
    entry, message, kind = service.run_action(entry_id, action, payload.values)
    if kind == "conflict":
        return ActionResult(ok=False, conflict=True, message=message, entry=entry)
    if kind == "error":
        return ActionResult(ok=False, message=message, entry=entry)
    return ActionResult(ok=True, message=message, entry=entry)
