"""JSON 文件持久化：数据落在 data/platform.json，进程重启、刷新后仍读到同一份。

只依赖标准库；写盘采用「先写临时文件再原子替换」，避免读到半截文件。
线程内用 RLock 串行化读改写，保证「先落库」的判定只有一个赢家。
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable, TypeVar

from app.seed import SEED_ROWS

T = TypeVar("T")

_DATA_DIR = Path(os.environ.get("PLATFORM_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
_DATA_FILE = _DATA_DIR / "platform.json"


class JsonStore:
    """以模块名为键的表结构仓库，接口与旧的内存 Store 保持一致。"""

    def __init__(self, path: Path = _DATA_FILE) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._tables: dict[str, list[dict[str, Any]]] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                with self._path.open("r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                if isinstance(payload, dict):
                    self._tables = {
                        name: [dict(row) for row in rows]
                        for name, rows in payload.items()
                        if isinstance(rows, list)
                    }
                    return
            except (json.JSONDecodeError, OSError):
                # 数据文件损坏时回退到种子数据，不让服务起不来
                pass
        self._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}
        self._flush()

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(".json.tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(self._tables, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self._path)

    def module_names(self) -> list[str]:
        with self._lock:
            # 下划线开头的是内部表（如效率流水），不计入业务模块
            return sorted(name for name in self._tables if not name.startswith("_"))

    def rows(self, module: str) -> list[dict[str, Any]]:
        """返回表本身的引用；调用方必须在 transaction 内做读改写。"""
        with self._lock:
            return self._tables.setdefault(module, [])

    def snapshot_rows(self, module: str) -> list[dict[str, Any]]:
        """读场景用的深拷贝，避免把内部结构泄漏到锁外被改。"""
        with self._lock:
            return [dict(row) for row in self._tables.setdefault(module, [])]

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        with self._lock:
            for row in self._tables.setdefault(module, []):
                if int(row.get("id", 0)) == entry_id:
                    return row
        return None

    def transaction(self, fn: Callable[[], T]) -> T:
        """在同一把锁内完成「检查—修改—落盘」，冲突时由调用方回滚返回提示。"""
        with self._lock:
            result = fn()
            self._flush()
            return result

    def reset(self) -> None:
        """测试用：清回种子数据。"""
        with self._lock:
            self._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}
            self._flush()

    def overview(self) -> dict[str, object]:
        with self._lock:
            modules: list[dict[str, object]] = []
            for name in self.module_names():
                rows = self._tables.get(name, [])
                modules.append({
                    "name": name,
                    "created": len(rows),
                    "pending": sum(1 for row in rows if row.get("pending")),
                    "abnormal": sum(1 for row in rows if row.get("abnormal")),
                })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = JsonStore()
