"""岸桥作业效率台账：全平台岸桥效率的唯一数据来源（single source of truth）。

设计目标：
- 一台岸桥只有一份效率记录，调度面板、详情页、交班页都从这里读，避免两班对不上；
- 分配作业、释放岸桥、效率修正都走同一份记录、同一套算法统一重算；
- 写入以「先落库」为准：进程内互斥锁保证同时只有一次提交能写，配合乐观版本号
  （expected_version）拦截晚到的提交，绝不覆盖先落库的值；
- 记录里保留原始计量口径（作业箱量/作业分钟），算法升级后可按新算法整体重算存量记录。
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from app.store import store

MODULE = "quaycrane"

# 当前生效的效率算法版本。算法调整后抬高此版本，存量记录通过 recalculate_all 重算。
CURRENT_ALGORITHM_VERSION = 2

# 各档位的额定作业效率上限（次/小时），超出即视为越界，不落库。
RATED_BY_GEAR: dict[str, float] = {"轻载档": 40.0, "标准档": 32.0, "重载档": 25.0}

# v2 算法在基础效率（箱量/时长）上的档位系数：重载循环额外扣除吊具闭锁耗时。
GEAR_FACTOR: dict[str, float] = {"轻载档": 1.05, "标准档": 1.0, "重载档": 0.92}

# 台账种子：按旧算法（v1）入账的历史作业记录，算法升级后用 recalculate_all 演示重算。
# 值与 compute_efficiency(..., algorithm_version=1) 保持一致。
_SEED_LEDGER: dict[str, dict[str, Any]] = {
    "QUAY-0002": {
        "作业效率": 18.7,
        "效率档位": "重载档",
        "作业箱量": 28.0,
        "作业分钟": 90.0,
        "操作司机": "王建国",
        "作业船舶": "远洋轮样例2",
        "algorithm_version": 1,
        "version": 3,
        "updated_at": "2026-09-30T20:00:00+00:00",
        "updated_by": "王建国",
        "history": [
            {
                "作业效率": 16.0,
                "效率档位": "重载档",
                "作业箱量": 24.0,
                "作业分钟": 90.0,
                "algorithm_version": 1,
                "version": 2,
                "updated_at": "2026-09-30T12:00:00+00:00",
                "updated_by": "上一班司机",
            }
        ],
    }
}


class ConflictError(Exception):
    """提交晚于他人：记录已被先落库的提交占用。"""


class ValidationError(Exception):
    """提交数据不合法（档位未知、数值缺失、越界等），不允许落库。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_efficiency(moves: float, minutes: float, gear: str, algorithm_version: int) -> float:
    """按指定算法版本把原始计量口径换算成作业效率（次/小时）。

    历史算法必须一直保留：存量记录重算时要能复现任意版本的口径，
    新算法只能新增分支，不能改掉旧分支的结果。
    """
    base = round(moves * 60.0 / minutes, 1)
    if algorithm_version <= 1:
        return base
    # v2：按档位加权，体现重载循环更慢的实际工况。
    factor = GEAR_FACTOR.get(gear, 1.0)
    return round(base * factor, 1)


class CraneEfficiencyService:
    """岸桥效率台账：内存实现，写入串行化，所有页面统一从这里读数。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[str, dict[str, Any]] = {}
        self._ensure_seed()

    # ---- 初始化与内部工具 -------------------------------------------------

    def _ensure_seed(self) -> None:
        """给已登记的岸桥各建一份台账记录；部分记录按旧算法(v1)入账，便于演示重算。"""
        for row in store.rows(MODULE):
            crane_code = str(row.get("岸桥编号", "")).strip()
            if not crane_code or crane_code in self._records:
                continue
            record = {
                "岸桥编号": crane_code,
                "作业效率": None,
                "效率档位": None,
                "作业箱量": None,
                "作业分钟": None,
                "操作司机": "",
                "作业船舶": "",
                "algorithm_version": 1,
                "version": 1,
                "updated_at": None,
                "updated_by": None,
                "history": [],
            }
            record.update(_SEED_LEDGER.get(crane_code, {}))
            self._records[crane_code] = record

    @staticmethod
    def _to_float(value: Any, field_name: str) -> float:
        if value is None or str(value).strip() == "":
            raise ValidationError(f"缺少「{field_name}」，无法重算作业效率")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValidationError(f"「{field_name}」必须是数字，收到的是 {value!r}")
        if number < 0:
            raise ValidationError(f"「{field_name}」不能为负数")
        return number

    def _record_of(self, crane_code: str) -> dict[str, Any] | None:
        return self._records.get(crane_code)

    # ---- 对外读取：所有页面同源 -------------------------------------------

    def get_record(self, crane_code: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._record_of(crane_code)
            return dict(record) if record else None

    def list_records(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(record) for record in self._records.values()]

    def value_map(self) -> dict[str, dict[str, Any]]:
        """按岸桥编号返回台账快照，供调度列表/详情/导出投影作业效率字段。"""
        with self._lock:
            return {code: dict(record) for code, record in self._records.items()}

    def register(self, crane_code: str) -> dict[str, Any]:
        """新岸桥登记时补建一份空台账，保证每台岸桥都有唯一对应的效率记录。"""
        with self._lock:
            record = self._record_of(crane_code)
            if record is not None:
                return dict(record)
            record = {
                "岸桥编号": crane_code,
                "作业效率": None,
                "效率档位": None,
                "作业箱量": None,
                "作业分钟": None,
                "操作司机": "",
                "作业船舶": "",
                "algorithm_version": CURRENT_ALGORITHM_VERSION,
                "version": 1,
                "updated_at": None,
                "updated_by": None,
                "history": [],
            }
            self._records[crane_code] = record
            return dict(record)

    # ---- 写入：分配、释放、效率修正共用这一份逻辑 ---------------------------

    def submit(
        self,
        crane_code: str,
        values: dict[str, Any],
        *,
        updated_by: str | None = None,
    ) -> dict[str, Any]:
        """登记一次作业计量并按当前算法统一重算效率。

        values 必带：expected_version（客户端读到的 version）、效率档位、作业箱量、作业分钟。
        成功返回新记录；档位越界抛 ValidationError（不落库）；版本过期抛 ConflictError
        （只提示已被占用，不覆盖先落库的值）。
        """
        gear = str(values.get("效率档位") or "").strip()
        if gear not in RATED_BY_GEAR:
            raise ValidationError(f"未知效率档位「{gear}」，可选：{'、'.join(RATED_BY_GEAR)}")
        moves = self._to_float(values.get("作业箱量"), "作业箱量")
        minutes = self._to_float(values.get("作业分钟"), "作业分钟")
        if minutes <= 0:
            raise ValidationError("「作业分钟」必须大于 0，否则作业效率无意义")

        if values.get("expected_version") is None:
            raise ValidationError("缺少版本号 expected_version，请重新读取当前岸桥效率后再提交")
        try:
            expected_version = int(values["expected_version"])
        except (TypeError, ValueError):
            raise ValidationError("版本号 expected_version 不合法")

        efficiency = compute_efficiency(moves, minutes, gear, CURRENT_ALGORITHM_VERSION)
        rated = RATED_BY_GEAR[gear]
        if efficiency > rated:
            raise ValidationError(
                f"作业效率 {efficiency} 次/小时超出{gear}额定值 {rated:g} 次/小时，"
                f"越界档位：{gear}，本次数据不落库"
            )

        driver = str(values.get("操作司机") or "").strip()
        vessel = str(values.get("作业船舶") or "").strip()

        with self._lock:
            record = self._record_of(crane_code)
            if record is None:
                raise ValidationError(f"岸桥 {crane_code} 尚未登记，无法记录作业效率")
            if expected_version != int(record["version"]):
                raise ConflictError(
                    f"岸桥 {crane_code} 的效率记录已被先落库的提交占用"
                    f"（当前版本 {record['version']}，提交基于版本 {expected_version}），"
                    "请刷新后以最新数据为准"
                )

            history_entry = {
                "作业效率": record["作业效率"],
                "效率档位": record["效率档位"],
                "作业箱量": record["作业箱量"],
                "作业分钟": record["作业分钟"],
                "algorithm_version": record["algorithm_version"],
                "version": record["version"],
                "updated_at": record["updated_at"],
                "updated_by": record["updated_by"],
            }
            record["作业效率"] = efficiency
            record["效率档位"] = gear
            record["作业箱量"] = moves
            record["作业分钟"] = minutes
            record["algorithm_version"] = CURRENT_ALGORITHM_VERSION
            record["version"] = expected_version + 1
            record["updated_at"] = _now()
            record["updated_by"] = driver or updated_by or record.get("updated_by")
            if driver:
                record["操作司机"] = driver
            if vessel:
                record["作业船舶"] = vessel
            record["history"].append(history_entry)
            return dict(record)

    def recalculate_all(self) -> dict[str, int]:
        """算法升级后用当前算法重算全部存量效率记录。

        以记录里保留的原始计量口径（箱量/分钟/档位）重新换算；每次成功重算同样先落库为准，
        version 递增、algorithm_version 抬到当前版本；原始计量值不会被改动。
        返回重算/跳过的条数。
        """
        recalculated = 0
        skipped = 0
        with self._lock:
            for record in self._records.values():
                moves = record.get("作业箱量")
                minutes = record.get("作业分钟")
                gear = record.get("效率档位")
                if moves is None or minutes is None or gear not in RATED_BY_GEAR:
                    skipped += 1
                    continue
                efficiency = compute_efficiency(
                    float(moves), float(minutes), gear, CURRENT_ALGORITHM_VERSION
                )
                # 重算结果同样受额定值约束：越界的历史数据不覆盖现值，留给人工核对。
                if efficiency > RATED_BY_GEAR[gear]:
                    skipped += 1
                    continue
                if record["作业效率"] == efficiency and int(
                    record["algorithm_version"]
                ) == CURRENT_ALGORITHM_VERSION:
                    skipped += 1
                    continue
                record["history"].append({
                    "作业效率": record["作业效率"],
                    "效率档位": record["效率档位"],
                    "作业箱量": record["作业箱量"],
                    "作业分钟": record["作业分钟"],
                    "algorithm_version": record["algorithm_version"],
                    "version": record["version"],
                    "updated_at": record["updated_at"],
                    "updated_by": record["updated_by"],
                    "reason": "algorithm-recalculate",
                })
                record["作业效率"] = efficiency
                record["algorithm_version"] = CURRENT_ALGORITHM_VERSION
                record["version"] = int(record["version"]) + 1
                record["updated_at"] = _now()
                record["updated_by"] = "system-recalculate"
                recalculated += 1
        return {"recalculated": recalculated, "skipped": skipped}


crane_efficiency = CraneEfficiencyService()
