"""岸桥效率收口的验收用例。

覆盖需求：
- 面板与详情同源、改完重新读仍是新值（持久化）
- 分配 / 释放走同一份记录统一重算
- 两人同时抢一台：只认先落库，晚到的 409
- 同一记录并发提交：先落库为准，晚到的不覆盖
- 档位效率超额定值：不落库并点明档位
- 换算法后重算已有记录
- 其他页面的效率与调度面板同源
"""
from __future__ import annotations

from app.efficiency import CURRENT_VERSION

QC = "/api/quaycrane"


def _allocate(client, crane_id: int = 1, *, version: int = 0, driver: str = "李司机", vessel: str = "远洋轮A", segments=None):
    return client.post(
        f"{QC}/{crane_id}/actions",
        json={
            "values": {
                "action": "分配作业",
                "version": version,
                "driver": driver,
                "vessel": vessel,
                "segments": segments or {"标准档": {"moves": 60, "minutes": 120}},
            }
        },
    )


def test_panel_and_detail_share_same_value(client):
    listed = client.get(QC).json()["items"]
    row = next(item for item in listed if item["id"] == 2)
    detail = client.get(f"{QC}/2").json()
    assert row["作业效率"] == detail["作业效率"] == 21.0
    assert row["version"] == detail["version"] == 1


def test_value_persists_after_reopen_and_refresh(client):
    # 模拟“改完退出再进”：重新发列表、详情请求，读到的仍是新算的值
    resp = _allocate(client, 1, segments={"标准档": {"moves": 90, "minutes": 180}})
    assert resp.status_code == 200
    assert resp.json()["entry"]["作业效率"] == 30.0

    assert client.get(f"{QC}/1").json()["作业效率"] == 30.0
    panel = next(item for item in client.get(QC).json()["items"] if item["id"] == 1)
    assert panel["作业效率"] == 30.0
    assert panel["操作司机"] == "李司机"
    assert panel["作业船舶"] == "远洋轮A"


def test_release_recalculates_same_record_and_keeps_final_value(client):
    _allocate(client, 1, segments={"标准档": {"moves": 60, "minutes": 120}})
    detail = client.get(f"{QC}/1").json()
    assert detail["version"] == 1

    # 释放时再追加一段作业量，总口径：(60+120)/(120+240)*60 = 30
    resp = client.post(
        f"{QC}/1/actions",
        json={"values": {
            "action": "释放岸桥",
            "version": 1,
            "segments": {"标准档": {"moves": 120, "minutes": 240}},
        }},
    )
    assert resp.status_code == 200
    entry = resp.json()["entry"]
    assert entry["status"] == "空闲"
    assert entry["作业效率"] == 30.0
    assert entry["version"] == 2

    # 释放后面板仍显示交班时的那份最终值
    panel = next(item for item in client.get(QC).json()["items"] if item["id"] == 1)
    assert panel["作业效率"] == 30.0


def test_concurrent_allocate_only_first_wins(client):
    first = _allocate(client, 1, driver="先到司机", vessel="甲船")
    second = _allocate(client, 1, driver="晚到司机", vessel="乙船")
    assert first.status_code == 200
    assert second.status_code == 409
    assert "已被占用" in second.json()["detail"]

    detail = client.get(f"{QC}/1").json()
    assert detail["操作司机"] == "先到司机"
    assert detail["作业船舶"] == "甲船"


def test_concurrent_submit_first_commit_wins_no_overwrite(client):
    _allocate(client, 1, segments={"标准档": {"moves": 60, "minutes": 120}})

    # 两个司机几乎同时基于版本 v1 提交，只有先落库的生效
    first = client.post(f"{QC}/1/actions", json={"values": {
        "action": "提交作业量", "version": 1,
        "segments": {"标准档": {"moves": 60, "minutes": 120}},
    }})
    second = client.post(f"{QC}/1/actions", json={"values": {
        "action": "提交作业量", "version": 1,
        "driver": "后来的司机",
        "segments": {"标准档": {"moves": 30, "minutes": 60}},
    }})
    assert first.status_code == 200
    assert second.status_code == 409
    assert "已被先落库的提交更新" in second.json()["detail"]

    detail = client.get(f"{QC}/1").json()
    # 先落库的值：120 箱 / 240 分钟 = 30；后来的 999 没有被并入
    assert detail["作业效率"] == 30.0
    assert detail["version"] == 2
    assert detail["操作司机"] == "李司机"


def test_submit_action_alias_works(client):
    # “提交作业量”不是状态动作，应被识别为中途补数
    _allocate(client, 1)
    resp = client.post(f"{QC}/1/actions", json={"values": {
        "action": "提交作业量", "version": 1,
        "segments": {"标准档": {"moves": 30, "minutes": 60}},
    }})
    assert resp.status_code == 200


def test_out_of_rating_rejected_with_tier_name(client):
    # QUAY-0001 双箱档额定 45，提交 100 箱/120 分钟 = 50 越界
    resp = client.post(
        f"{QC}/1/actions",
        json={"values": {
            "action": "分配作业", "version": 0,
            "driver": "王司机", "vessel": "丙船",
            "segments": {"双箱档": {"moves": 100, "minutes": 120}},
        }},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "双箱档" in detail
    assert "50" in detail and "45" in detail

    # 越界提交不落库：岸桥仍空闲、无活跃记录、可重新分配
    panel = next(item for item in client.get(QC).json()["items"] if item["id"] == 1)
    assert panel["status"] == "空闲"
    assert panel["version"] == 0
    retry = _allocate(client, 1)
    assert retry.status_code == 200


def test_out_of_rating_checked_before_version_conflict(client):
    # 记录已推进到 v1，客户端仍带着 v0 提交且内容越界：应点明档位（422），而不是只报版本冲突
    _allocate(client, 1, segments={"标准档": {"moves": 60, "minutes": 120}})
    resp = client.post(f"{QC}/1/actions", json={"values": {
        "action": "提交作业量", "version": 0,
        "segments": {"标准档": {"moves": 90, "minutes": 60}},  # 90 箱/时 > 额定 30
    }})
    assert resp.status_code == 422
    assert "标准档" in resp.json()["detail"]


def test_repair_from_idle_allowed_and_repairs_rejected_twice(client):
    # QUAY-0001 空闲 v0，可直接登记检修
    resp = client.post(f"{QC}/1/actions", json={"values": {
        "action": "登记检修", "version": 0,
    }})
    assert resp.status_code == 200
    assert resp.json()["entry"]["status"] == "检修中"
    # 重复登记被拦
    again = client.post(f"{QC}/1/actions", json={"values": {"action": "登记检修", "version": 1}})
    assert again.status_code == 400
    assert "检修中" in again.json()["detail"]


def test_repair_while_working_archives_record(client):
    _allocate(client, 1, segments={"标准档": {"moves": 60, "minutes": 120}})
    resp = client.post(f"{QC}/1/actions", json={"values": {"action": "登记检修", "version": 1}})
    assert resp.status_code == 200
    assert resp.json()["entry"]["status"] == "检修中"
    # 原记录归档，效率仍保留在面板上
    panel = next(item for item in client.get(QC).json()["items"] if item["id"] == 1)
    assert panel["作业效率"] == 30.0
    from app.store import store
    records = [row for row in store.rows("_quaycrane_efficiency") if row["crane_id"] == 1]
    assert all(row["active"] is False for row in records)

    # 完成检修恢复空闲后重新分配开新记录，不污染旧流水
    done = client.post(f"{QC}/1/actions", json={"values": {"action": "完成检修", "version": 2}})
    assert done.status_code == 200
    assert done.json()["entry"]["status"] == "空闲"
    # 完成检修后面板仍读得到交班时的那份效率值
    assert done.json()["entry"]["作业效率"] == 30.0
    resp = client.post(f"{QC}/1/actions", json={"values": {
        "action": "分配作业", "version": 3,
        "driver": "新班次司机", "vessel": "新船",
        "segments": {"标准档": {"moves": 30, "minutes": 60}},
    }})
    assert resp.status_code == 200
    assert resp.json()["entry"]["作业效率"] == 30.0
    from app.store import store
    active_records = [row for row in store.rows("_quaycrane_efficiency")
                      if row["crane_id"] == 1 and row["active"]]
    assert len(active_records) == 1
    assert active_records[0]["vessel"] == "新船"


def test_repairing_crane_cannot_be_allocated(client):
    # QUAY-0003 处于检修中，分配应被状态护栏拦下
    resp = client.post(
        f"{QC}/3/actions",
        json={"values": {
            "action": "分配作业", "version": 0,
            "driver": "司机", "vessel": "船",
            "segments": {"标准档": {"moves": 30, "minutes": 120}},
        }},
    )
    assert resp.status_code == 400
    assert "检修中" in resp.json()["detail"]


def test_unsupported_tier_rejected(client):
    resp = client.post(
        f"{QC}/2/actions",
        json={"values": {
            "action": "提交作业量", "version": 1,
            "segments": {"双箱档": {"moves": 10, "minutes": 60}},
        }},
    )
    assert resp.status_code == 400
    assert "双箱档" in resp.json()["detail"]
    assert "STS-65T-单吊" in resp.json()["detail"]
    # 原值未被顶版本
    assert client.get(f"{QC}/2").json()["version"] == 1


def test_recompute_after_algorithm_change(client):
    # 造一条 v1 口径的记录：标准 40/120 分=20，双箱 80/120 分=40；
    # v1 综合 120 箱 /240 分钟=30；v2 加权箱量 40+80*1.8=184，综合 46
    from app.efficiency import calc_efficiency_v1, calc_efficiency_v2
    segments = {"标准档": {"moves": 40, "minutes": 120}, "双箱档": {"moves": 80, "minutes": 120}}
    v1_value = calc_efficiency_v1(segments)
    v2_value = calc_efficiency_v2(segments)
    assert v1_value != v2_value

    _allocate(client, 1, segments=segments)
    from app.store import store
    record = next(row for row in store.rows("_quaycrane_efficiency") if row["crane_id"] == 1)
    record["efficiency"] = v1_value
    record["algorithm_version"] = 1
    assert client.get(f"{QC}/1").json()["作业效率"] == v1_value

    result = client.post(f"{QC}/recalculate-efficiency").json()
    assert result["updated"] >= 1
    changed = next(item for item in result["detail"] if item["crane_id"] == 1)
    assert changed["before"] == v1_value
    assert changed["after"] == v2_value
    assert client.get(f"{QC}/1").json()["作业效率"] == v2_value


def test_other_pages_read_same_source_as_panel(client):
    _allocate(client, 1, segments={"标准档": {"moves": 60, "minutes": 120}})
    panel = {item["id"]: item["作业效率"] for item in client.get(QC).json()["items"]}
    summary = client.get(f"{QC}/efficiency-summary").json()
    summary_map = {item["id"]: item["作业效率"] for item in summary["items"]}
    assert summary_map == panel
    assert summary["algorithm_version"] == f"v{CURRENT_VERSION}"


def test_driver_handover_keeps_one_record(client):
    _allocate(client, 1, driver="上一班司机", segments={"标准档": {"moves": 60, "minutes": 120}})
    resp = client.post(f"{QC}/1/actions", json={"values": {
        "action": "提交作业量", "version": 1, "driver": "接班司机",
        "segments": {"标准档": {"moves": 60, "minutes": 120}},
    }})
    assert resp.status_code == 200
    entry = resp.json()["entry"]
    assert entry["操作司机"] == "接班司机"
    assert entry["作业效率"] == 30.0
    assert len(entry["history"]) == 2

    from app.store import store
    records = [row for row in store.rows("_quaycrane_efficiency") if row["crane_id"] == 1 and row["active"]]
    assert len(records) == 1
