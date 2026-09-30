"""岸桥作业效率算法：唯一的口径来源，分配、释放、看板、跨页面读取都走这里。

换算法时只新增一个版本函数并把 CURRENT_VERSION 指过去；
历史记录通过 services.quaycrane.recompute_all_efficiency() 按同一份流水重算。
单位：自然箱/小时。
"""
from __future__ import annotations

from collections.abc import Callable

# 各作业档位名称；提交与额定值校验都以这份顺序为准
EFFICIENCY_TIERS = ["标准档", "双箱档", "舱底档"]

# 档位箱量折算系数：标准档 1:1，双箱档一次双吊按 1.8 个自然箱计，舱底作业按 0.7 计
TIER_WEIGHT_V2 = {"标准档": 1.0, "双箱档": 1.8, "舱底档": 0.7}

Calculator = Callable[[dict[str, dict[str, float]]], float]


def _tier_moves(segments: dict[str, dict[str, float]], tier: str) -> float:
    return float(segments.get(tier, {}).get("moves", 0.0))


def _tier_minutes(segments: dict[str, dict[str, float]], tier: str) -> float:
    return float(segments.get(tier, {}).get("minutes", 0.0))


def calc_efficiency_v1(segments: dict[str, dict[str, float]]) -> float:
    """初版口径：总箱量 / 总作业分钟。"""
    total_moves = sum(_tier_moves(segments, tier) for tier in EFFICIENCY_TIERS)
    total_minutes = sum(_tier_minutes(segments, tier) for tier in EFFICIENCY_TIERS)
    if total_minutes <= 0:
        return 0.0
    return round(total_moves / total_minutes * 60, 2)


def calc_efficiency_v2(segments: dict[str, dict[str, float]]) -> float:
    """现版口径：双箱/舱底档位按折算系数计入综合箱量。"""
    weighted_moves = 0.0
    total_minutes = 0.0
    for tier in EFFICIENCY_TIERS:
        weighted_moves += _tier_moves(segments, tier) * TIER_WEIGHT_V2[tier]
        total_minutes += _tier_minutes(segments, tier)
    if total_minutes <= 0:
        return 0.0
    return round(weighted_moves / total_minutes * 60, 2)


# 版本表：旧算法保留，换算法后历史记录可逐级迁移重算
CALCULATORS: dict[int, Calculator] = {
    1: calc_efficiency_v1,
    2: calc_efficiency_v2,
}
CURRENT_VERSION = 2


def calc_efficiency(segments: dict[str, dict[str, float]], *, version: int | None = None) -> float:
    """按指定版本（默认最新版）计算效率。"""
    version = CURRENT_VERSION if version is None else version
    calculator = CALCULATORS.get(version)
    if calculator is None:
        raise ValueError(f"不支持的效率算法版本：v{version}")
    return calculator(segments)


def tier_efficiency(moves: float, minutes: float) -> float:
    """单档位瞬时效率（自然箱/小时），用于额定值越界校验。"""
    if minutes <= 0:
        return 0.0
    return round(moves / minutes * 60, 2)
