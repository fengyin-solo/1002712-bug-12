"""岸桥调度业务规则。

效率口径的核心约束：
1. 每台岸桥只有一份效率记录（表 quaycrane_efficiency，crane_id 唯一活跃记录），
   分配作业、中途提交、释放岸桥都向同一份流水追加分段并统一重算；
2. 提交必须携带客户端读到的版本号，记录被先落库的提交顶过后，晚到的提交按冲突拒绝，
   不允许覆盖前面的值；
3. 任一档位效率超过该型号额定值时整次提交不落库，并点明越界档位；
4. 效率算法升级后用 recompute_all_efficiency() 按原流水重算全部历史记录。
"""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

from app.efficiency import (
    CURRENT_VERSION,
    EFFICIENCY_TIERS,
    calc_efficiency,
    tier_efficiency,
)
from app.store import store

MODULE = "quaycrane"
# 下划线前缀：内部表，不出现在运营概览的业务模块清单里
EFFICIENCY_MODULE = "_quaycrane_efficiency"
REQUIRED_FIELDS = ["岸桥编号", "岸桥型号", "额定起重量"]
STATUS_ORDER = ["空闲", "作业中", "检修中", "已停用"]
ACTION_ALLOCATE = "分配作业"
ACTION_APPEND = "提交作业量"
ACTION_RELEASE = "释放岸桥"
ACTION_REPAIR = "登记检修"
ACTION_REPAIR_DONE = "完成检修"
# 提交作业量不改状态，只往同一份流水里追加分段
ACTION_RULES = {
    ACTION_ALLOCATE: "作业中",
    ACTION_RELEASE: "空闲",
    ACTION_REPAIR: "检修中",
    ACTION_REPAIR_DONE: "空闲",
}


class QuaycraneError(Exception):
    """业务拒绝：code 决定 HTTP 状态，message 直接回给前端。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _to_number(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        raise ValueError
    return float(text)


def _active_record(rows: list[dict[str, Any]], crane_id: int) -> dict[str, Any] | None:
    for row in rows:
        if int(row.get("crane_id", 0)) == crane_id and row.get("active"):
            return row
    return None


def _latest_record(rows: list[dict[str, Any]], crane_id: int) -> dict[str, Any] | None:
    """取该岸桥最新一份记录：活跃中优先，否则取最近释放归档的那一份。"""
    owned = [row for row in rows if int(row.get("crane_id", 0)) == crane_id]
    active = [row for row in owned if row.get("active")]
    if active:
        return active[0]
    if owned:
        return max(owned, key=lambda row: int(row.get("id", 0)))
    return None


def _merge_segments(
    base: dict[str, dict[str, float]],
    incoming: dict[str, dict[str, float]],
) -> dict[str, dict[str, float]]:
    merged = copy.deepcopy(base)
    for tier, values in incoming.items():
        bucket = merged.setdefault(tier, {"moves": 0.0, "minutes": 0.0})
        bucket["moves"] = round(bucket["moves"] + values["moves"], 4)
        bucket["minutes"] = round(bucket["minutes"] + values["minutes"], 4)
    return merged


def _parse_segments(values: dict[str, Any]) -> dict[str, dict[str, float]]:
    """把提交体解析成 {档位: {moves, minutes}}；非法输入不落库。"""
    raw = values.get("segments")
    if raw is None:
        # 兼容扁平提交：标准档_moves / 标准档_minutes
        raw = {}
        for tier in EFFICIENCY_TIERS:
            moves, minutes = values.get(f"{tier}_箱量"), values.get(f"{tier}_分钟")
            if moves is not None or minutes is not None:
                raw[tier] = {"moves": moves or 0, "minutes": minutes or 0}
    if not isinstance(raw, dict):
        raise QuaycraneError("invalid", "作业分段格式不正确，应为「档位: {箱量, 分钟}」的结构")

    parsed: dict[str, dict[str, float]] = {}
    for tier, segment in raw.items():
        if tier not in EFFICIENCY_TIERS:
            raise QuaycraneError("invalid", f"未知作业档位「{tier}」，可选档位：{'、'.join(EFFICIENCY_TIERS)}")
        if not isinstance(segment, dict):
            raise QuaycraneError("invalid", f"「{tier}」档位数据格式不正确")
        try:
            moves = _to_number(segment.get("moves"))
            minutes = _to_number(segment.get("minutes"))
        except ValueError:
            raise QuaycraneError("invalid", f"「{tier}」档位的箱量与分钟必须是数字") from None
        if moves < 0 or minutes < 0:
            raise QuaycraneError("invalid", f"「{tier}」档位的箱量与分钟不允许为负数")
        if moves == 0 and minutes == 0:
            continue
        parsed[tier] = {"moves": moves, "minutes": minutes}
    return parsed


def _check_ratings(crane: dict[str, Any], segments: dict[str, dict[str, float]]) -> None:
    """档位级额定值校验：越界即拒绝，并点明是哪个档位。"""
    rated_map = crane.get("额定效率") or {}
    allowed = [tier for tier in EFFICIENCY_TIERS if tier in rated_map]
    for tier, segment in segments.items():
        if tier not in rated_map:
            raise QuaycraneError(
                "invalid",
                f"「{tier}」不是岸桥型号 {crane.get('岸桥型号')} 的允许档位，"
                f"允许档位：{'、'.join(allowed) or '无'}",
            )
        rate = tier_efficiency(segment["moves"], segment["minutes"])
        rated = float(rated_map[tier])
        if rate > rated:
            raise QuaycraneError(
                "out_of_rating",
                f"「{tier}」档位效率 {rate} 自然箱/小时超出该型号额定值 {rated:g} 自然箱/小时，"
                f"本次提交未保存",
            )


def _view(crane: dict[str, Any], record: dict[str, Any] | None) -> dict[str, Any]:
    """组装对外视图：面板、详情、导出、跨页面读取共用这一份口径。"""
    view = dict(crane)
    if record is not None:
        view["作业效率"] = record["efficiency"]
        view["作业船舶"] = record.get("vessel") or ""
        view["操作司机"] = record.get("driver") or ""
        view["岸桥状态"] = crane.get("status")
        # 并发令牌始终用岸桥自身版本：任何一次落库都会顶高，晚到的据此识别冲突
        view["version"] = crane.get("version", 0)
        view["record_version"] = record.get("version", 0)
        view["record_active"] = bool(record.get("active"))
        view["efficiency_version"] = record.get("algorithm_version", CURRENT_VERSION)
        view["segments"] = copy.deepcopy(record.get("segments", {}))
        view["history"] = copy.deepcopy(record.get("history", []))
        view["占用开始时间"] = record.get("started_at", "")
        view["最近提交时间"] = record.get("updated_at", "")
    else:
        view["作业效率"] = None
        view["作业船舶"] = crane.get("作业船舶") or ""
        view["操作司机"] = crane.get("操作司机") or ""
        view["岸桥状态"] = crane.get("status")
        view["version"] = crane.get("version", 0)
        view["record_version"] = 0
        view["record_active"] = False
        view["efficiency_version"] = CURRENT_VERSION
        view["segments"] = {}
        view["history"] = []
        view["占用开始时间"] = ""
        view["最近提交时间"] = ""
    return view


class QuaycraneService:
    # ---------- 读取：全部走同一口径 ----------

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        cranes = store.snapshot_rows(MODULE)
        records = store.snapshot_rows(EFFICIENCY_MODULE)
        views = [_view(crane, _latest_record(records, int(crane["id"]))) for crane in cranes]
        if keyword:
            views = [row for row in views if keyword in str(row.get("岸桥编号", ""))]
        if status:
            views = [row for row in views if row.get("status") == status]
        total = len(views)
        start = max(page - 1, 0) * size
        return views[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        crane = store.find(MODULE, entry_id)
        if crane is None:
            return None
        record = _latest_record(store.snapshot_rows(EFFICIENCY_MODULE), entry_id)
        return _view(copy.deepcopy(crane), copy.deepcopy(record))

    def fleet_efficiency(self) -> dict[str, Any]:
        """别的页面同源读取岸桥效率的唯一出口（工班页等都从这里取）。"""
        items, total = self.list_entries(page=1, size=10000)
        active = [item for item in items if item.get("岸桥状态") == "作业中"]
        values = [float(item["作业效率"]) for item in items if item["作业效率"] is not None]
        return {
            "source": "/api/quaycrane/efficiency-summary",
            "algorithm_version": f"v{CURRENT_VERSION}",
            "total": total,
            "working": len(active),
            "average_efficiency": round(sum(values) / len(values), 2) if values else 0.0,
            "items": [
                {
                    "id": item["id"],
                    "岸桥编号": item["岸桥编号"],
                    "岸桥型号": item["岸桥型号"],
                    "作业效率": item["作业效率"],
                    "作业船舶": item["作业船舶"],
                    "操作司机": item["操作司机"],
                    "岸桥状态": item["岸桥状态"],
                    "version": item["version"],
                }
                for item in items
            ],
        }

    # ---------- 登记 ----------

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing

        def commit() -> dict[str, Any]:
            rows = store.rows(MODULE)
            entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
            entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
            entry["吊具类型"] = values.get("吊具类型") or ""
            entry["额定效率"] = _normalize_ratings(values.get("额定效率"))
            entry["作业船舶"] = ""
            entry["操作司机"] = ""
            entry["status"] = STATUS_ORDER[0]
            entry["pending"] = True
            entry["abnormal"] = False
            entry["版本号"] = 0
            rows.append(entry)
            return copy.deepcopy(entry)

        return store.transaction(commit), []

    # ---------- 分配 / 提交 / 释放：同一份记录 ----------

    def submit_efficiency(self, entry_id: int, values: dict[str, Any]) -> dict[str, Any]:
        """分配作业、中途换班提交、释放岸桥共用的落库入口。"""
        action = str(values.get("action") or "").strip()
        if action not in ACTION_RULES and action != ACTION_APPEND:
            raise QuaycraneError("invalid", f"动作「{action}」不属于岸桥调度可执行范围")

        # 完成检修只允许带空分段（不产生效率流水）
        if action == ACTION_REPAIR_DONE:
            if _parse_segments(values):
                raise QuaycraneError("invalid", "完成检修不接受作业量，请改为释放岸桥时提交")

        try:
            expected_version = int(values.get("version", 0))
        except (TypeError, ValueError):
            raise QuaycraneError("invalid", "版本号必须是整数，请刷新详情后重试") from None

        driver = str(values.get("driver") or values.get("操作司机") or "").strip()
        vessel = str(values.get("vessel") or values.get("作业船舶") or "").strip()
        segments = _parse_segments(values)

        def commit() -> dict[str, Any]:
            crane = store.find(MODULE, entry_id)
            if crane is None:
                raise QuaycraneError("not_found", f"岸桥 {entry_id} 不存在或已归档")

            record_rows = store.rows(EFFICIENCY_MODULE)
            record = _active_record(record_rows, entry_id)
            current_status = str(crane.get("status"))

            # 状态流转护栏：该拦的动作先拦掉，不进入落库环节
            if action == ACTION_ALLOCATE:
                if record is not None or current_status == "作业中":
                    # 两人同时抢同一台：只认先落库的那次分配
                    holder = record or {}
                    raise QuaycraneError(
                        "conflict",
                        f"岸桥 {crane.get('岸桥编号')} 已被占用：当前由司机「{holder.get('driver') or '—'}」"
                        f"服务于「{holder.get('vessel') or '—'}」，先落库的分配为准，本次未保存",
                    )
                if current_status in ("检修中", "已停用"):
                    raise QuaycraneError(
                        "invalid",
                        f"岸桥 {crane.get('岸桥编号')} 当前为{current_status}，需先恢复为空闲才能分配作业",
                    )
                if not driver or not vessel:
                    raise QuaycraneError("invalid", "分配作业必须填写操作司机与作业船舶")

            if action == ACTION_REPAIR:
                # 空闲或作业中都可登记检修；已停用/检修中的不允许重复登记
                if current_status in ("检修中", "已停用"):
                    raise QuaycraneError(
                        "invalid",
                        f"岸桥 {crane.get('岸桥编号')} 当前为{current_status}，不能重复登记检修",
                    )

            if action == ACTION_REPAIR_DONE:
                if current_status != "检修中":
                    raise QuaycraneError(
                        "invalid",
                        f"岸桥 {crane.get('岸桥编号')} 当前为{current_status}，只有检修中才能完成检修",
                    )

            if action in (ACTION_APPEND, ACTION_RELEASE):
                if record is None or current_status != "作业中":
                    raise QuaycraneError(
                        "invalid",
                        f"岸桥 {crane.get('岸桥编号')} 当前为{current_status}，没有进行中的作业记录，"
                        f"不能执行「{action}」",
                    )

            # 越界/档位合法性只取决于岸桥型号与提交内容，与版本无关：
            # 无论是否并发，越界数据都不落库，并点明是哪个档位
            if segments:
                _check_ratings(crane, segments)

            # 乐观并发：并发令牌始终是岸桥自身版本，任何一次落库都会顶高。
            # 晚到的提交拿着旧版本号，据此被拒绝，两条记录冲突时以先落库的为准。
            current_version = int(crane.get("version", 0))
            if current_version != expected_version:
                raise QuaycraneError(
                    "conflict",
                    f"该岸桥的效率记录已被先落库的提交更新（当前版本 v{current_version}），"
                    "请刷新后基于最新记录提交，本次未覆盖原值",
                )

            now = _now()
            if action == ACTION_ALLOCATE:
                merged = segments
                record = {
                    "id": max((int(row.get("id", 0)) for row in record_rows), default=0) + 1,
                    "crane_id": entry_id,
                    "active": True,
                    "vessel": vessel,
                    "driver": driver,
                    "segments": merged,
                    "history": [],
                    "efficiency": calc_efficiency(merged),
                    "algorithm_version": CURRENT_VERSION,
                    "version": 1,
                    "started_at": now,
                    "updated_at": now,
                    "released_at": None,
                }
                if segments:
                    record["history"].append(_history_entry(action, driver, segments, now))
                record_rows.append(record)
            else:
                if record is not None:
                    merged = _merge_segments(record["segments"], segments)
                    record["segments"] = merged
                    record["efficiency"] = calc_efficiency(merged)
                    record["algorithm_version"] = CURRENT_VERSION
                    record["version"] = int(record["version"]) + 1
                    record["updated_at"] = now
                    if segments:
                        record["history"].append(_history_entry(action, driver, segments, now))
                    if driver:
                        record["driver"] = driver
                    if vessel:
                        record["vessel"] = vessel
                    if action == ACTION_RELEASE:
                        record["active"] = False
                        record["released_at"] = now
                    elif action == ACTION_REPAIR:
                        # 作业中直接转检修：原效率记录归档，恢复后重新分配会开新记录
                        record["active"] = False
                        record["released_at"] = now
                # 空闲岸桥直接登记检修：没有效率记录，只走状态流转

            if action in ACTION_RULES:
                target_status = ACTION_RULES[action]
                crane["status"] = target_status
                crane["pending"] = target_status != STATUS_ORDER[-1]
            crane["abnormal"] = False
            crane["version"] = int(crane.get("version", 0)) + 1
            # 返回视图取最新记录（活跃或刚归档），保证完成检修/释放后仍看得到交班值
            return _view(copy.deepcopy(crane), copy.deepcopy(_latest_record(record_rows, entry_id)))

        return store.transaction(commit)

    # ---------- 算法升级后重算 ----------

    def recompute_all_efficiency(self) -> dict[str, Any]:
        """效率算法换过之后，按每台岸桥同一份流水重算已有记录。"""

        def commit() -> dict[str, Any]:
            updated = 0
            detail: list[dict[str, Any]] = []
            cranes = {int(row.get("id", 0)): row for row in store.rows(MODULE)}
            touched_cranes: set[int] = set()
            for record in store.rows(EFFICIENCY_MODULE):
                before = record.get("efficiency")
                record["efficiency"] = calc_efficiency(record.get("segments", {}))
                old_algo = int(record.get("algorithm_version", 1))
                record["algorithm_version"] = CURRENT_VERSION
                # 流水值未变但口径变了，同样顶一版，让持有旧详情的客户端走冲突刷新
                record["version"] = int(record.get("version", 0)) + 1
                record["updated_at"] = _now()
                crane_id = int(record.get("crane_id", 0))
                if crane_id in cranes and crane_id not in touched_cranes:
                    crane = cranes[crane_id]
                    crane["version"] = int(crane.get("version", 0)) + 1
                    touched_cranes.add(crane_id)
                updated += 1
                detail.append({
                    "record_id": record.get("id"),
                    "crane_id": record.get("crane_id"),
                    "from_algorithm": f"v{old_algo}",
                    "to_algorithm": f"v{CURRENT_VERSION}",
                    "before": before,
                    "after": record["efficiency"],
                })
            return {"updated": updated, "algorithm_version": f"v{CURRENT_VERSION}", "detail": detail}

        return store.transaction(commit)


def _history_entry(action: str, driver: str, segments: dict[str, dict[str, float]], at: str) -> dict[str, Any]:
    return {
        "action": action,
        "driver": driver,
        "segments": copy.deepcopy(segments),
        "efficiency": calc_efficiency(segments),
        "at": at,
    }


def _normalize_ratings(raw: Any) -> dict[str, float]:
    """新登记岸桥未显式给额定档位时，按起重量给一份保守默认。"""
    if isinstance(raw, dict) and raw:
        ratings: dict[str, float] = {}
        for tier, value in raw.items():
            if tier in EFFICIENCY_TIERS:
                try:
                    number = _to_number(value)
                except ValueError:
                    continue
                if number > 0:
                    ratings[tier] = number
        if ratings:
            return ratings
    return {"标准档": 30.0}
