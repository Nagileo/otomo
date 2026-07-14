"""Regressions for the 2026-07-04 review fixes:

- per-event-loop semaphores (cross-loop reuse used to raise and get swallowed)
- real-usage ledger for token quotas
- rightmost X-Forwarded-For (spoofed left values must not win)
- session ownership migration + idempotent ensure_session
- calendar only_mine degradation when collections are unreadable
- airing progress tail-page fetch for 200+ episode shows
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from starlette.requests import Request

from otomo.quota import add_usage_from_response, begin_usage_ledger, client_ip, collected_usage
from otomo.session_store import SessionStore
from otomo.tools._concurrency import gather_limited
from otomo.tools.calendar.tool import (
    AiringProgressArgs,
    AiringProgressTool,
    BroadcastCalendarArgs,
    BroadcastCalendarTool,
)


def test_gather_limited_across_event_loops():
    async def one(i: int) -> int:
        await asyncio.sleep(0.001)
        return i

    # 8 > bangumi limit(6) 强制产生 waiter，让信号量真正绑定 loop
    first = asyncio.run(gather_limited([one(i) for i in range(8)], host="bangumi"))
    second = asyncio.run(gather_limited([one(i) for i in range(8)], host="bangumi"))
    assert first == list(range(8))
    assert second == list(range(8))
    assert not any(isinstance(x, BaseException) for x in second)


def test_usage_ledger_accumulates_and_noops_outside_request():
    async def scenario() -> int:
        begin_usage_ledger()
        resp = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50))
        add_usage_from_response(resp)
        add_usage_from_response(SimpleNamespace(usage=None))  # provider 不回报 → 忽略
        add_usage_from_response(resp)
        return collected_usage()

    assert asyncio.run(scenario()) == 300

    async def no_ledger() -> int:
        add_usage_from_response(SimpleNamespace(usage=SimpleNamespace(prompt_tokens=7, completion_tokens=1)))
        return collected_usage()

    assert asyncio.run(no_ledger()) == 0


def _request_with_forwarded(value: str) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"x-forwarded-for", value.encode())],
        "client": ("10.0.0.1", 1234),
    }
    return Request(scope)


def test_client_ip_ignores_spoofed_leftmost_forwarded():
    # 客户端伪造链在左、可信反代追加的真实 ip 在右
    assert client_ip(_request_with_forwarded("6.6.6.6, 203.0.113.9")) == "203.0.113.9"
    assert client_ip(_request_with_forwarded("203.0.113.9")) == "203.0.113.9"


def test_session_store_owner_migration_and_idempotent_ensure(tmp_path):
    store = SessionStore(path=str(tmp_path / "sessions.sqlite3"))
    store.ensure_session("s1", "anon-cookie", title="第一次")
    store.ensure_session("s1", "anon-cookie")  # INSERT OR IGNORE：重复 ensure 不再抛 UNIQUE
    store.append_message("s1", "anon-cookie", role="user", content="hello")

    moved = store.migrate_owner("anon-cookie", "user:alice")
    assert moved == 1
    assert [s["id"] for s in store.list_sessions("user:alice")] == ["s1"]
    assert store.list_sessions("anon-cookie") == []

    try:
        store.load_messages("s1", "anon-cookie")
        raise AssertionError("old owner must lose access after migration")
    except PermissionError:
        pass


class _NoCollectionBangumi:
    async def get_me(self):
        return {"username": "alice"}

    async def get_calendar(self):
        return [
            {
                "weekday": {"id": 6, "cn": "星期六"},
                "items": [
                    {"id": 100, "name": "A", "name_cn": "动画A", "air_date": "2026-07-04", "air_weekday": 6},
                    {"id": 200, "name": "B", "name_cn": "动画B", "air_date": "2026-07-04", "air_weekday": 6},
                ],
            }
        ]

    async def get_all_user_collections(self, username, subject_type=2, collection_type=None, max_items=300):
        return []


def test_calendar_only_mine_degrades_to_full_table(monkeypatch):
    monkeypatch.setattr("otomo.tools.calendar.tool._today", lambda: __import__("datetime").date(2026, 7, 4))
    tool = BroadcastCalendarTool(_NoCollectionBangumi())
    res = asyncio.run(tool.run(BroadcastCalendarArgs(day="today", only_mine=True)))
    assert res.ok and res.data is not None
    assert res.data.only_mine is False  # 已降级
    assert res.data.count == 2  # 全量表而不是空表
    assert "警告" in res.data.notes[0]


class _AbandonHeavyBangumi:
    """8 部弃坑动画都带 ep_status：外层 gather_limited 持满 bangumi 槽后，
    内层 _episode_context 再嵌套同 host 信号量曾导致死锁（本用例修复前必 hang）。"""

    async def get_me(self):
        return {"username": "alice"}

    async def get_all_user_collections(self, username, subject_type=2, collection_type=None, max_items=300):
        if collection_type == 5:
            return [
                {"ep_status": 3, "rate": 4, "subject": {"id": 1000 + i, "name_cn": f"弃坑番{i}"}}
                for i in range(8)
            ]
        return []

    async def get_episodes(self, subject_id, ep_type=None, limit=100, offset=0):
        await asyncio.sleep(0.001)
        # 分集不带 id → 内层 comments() 提前返回空，但仍会进入（曾经的）嵌套信号量路径
        return {"data": [{"sort": i} for i in range(1, 6)]}


def test_abandon_analysis_does_not_deadlock_on_nested_limits():
    from otomo.tools.user_analysis.tool import AbandonAnalysisArgs, AbandonAnalysisTool

    tool = AbandonAnalysisTool(_AbandonHeavyBangumi())

    async def scenario():
        return await asyncio.wait_for(
            tool.run(AbandonAnalysisArgs(username="alice", include_on_hold=False, limit=10)),
            timeout=10,
        )

    res = asyncio.run(scenario())
    assert res.ok and res.data is not None
    assert len(res.data.items) == 8


class _LongShowBangumi:
    async def get_me(self):
        return {"username": "alice"}

    async def get_all_user_collections(self, username, subject_type=2, collection_type=None, max_items=300):
        if collection_type == 3:
            return [{"ep_status": 230, "subject": {"id": 300, "name_cn": "长寿番", "eps": 250}}]
        return []

    async def get_episodes(self, subject_id, ep_type=None, limit=100, offset=0):
        # 模拟 250 集：首页只覆盖最早 200 集，尾页才有最新已播集
        def page(start: int, end: int):
            return [
                {"sort": i, "airdate": "2026-07-01" if i <= 240 else "2026-07-20"}
                for i in range(start + 1, end + 1)
            ]

        if offset == 0:
            return {"total": 250, "data": page(0, 200)}
        return {"total": 250, "data": page(offset, min(offset + limit, 250))}


def test_airing_progress_fetches_tail_page_for_long_shows(monkeypatch):
    monkeypatch.setattr("otomo.tools.calendar.tool._today", lambda: __import__("datetime").date(2026, 7, 4))
    tool = AiringProgressTool(_LongShowBangumi())
    res = asyncio.run(tool.run(AiringProgressArgs(username="alice")))
    assert res.ok and res.data is not None
    item = res.data.items[0]
    assert item.aired_ep == 240  # 没有尾页时会错误地封顶在 200
    assert item.behind == 10


def test_pilgrimage_city_match_prefix_not_substring():
    """"东京都"包含子串"京都"——朴素 in 匹配会让东京作品穿透京都过滤（用户实测踩坑）。"""
    from otomo.tools.pilgrimage.tool import _city_match

    assert _city_match("京都", "京都府")
    assert _city_match("京都", "京都市")
    assert not _city_match("京都", "东京都")  # 关键：子串命中但前缀不命中
    assert _city_match("东京", "东京都")
    assert _city_match("秩父", "秩父市")
    assert _city_match("京都市", "京都")  # 双向前缀：查询比标注更具体
    assert not _city_match("大阪", "东京都")


def test_pilgrimage_geo_tiers():
    """都市圈分层：名称命中=core；25km 内=core；nearby/bonus 按半径分档。

    覆盖用户点名的场景：东京→饭能/鹫宫/秩父（nearby）、大阪→冈山（bonus）、
    京都→宇治（core，京吹的 city 标"宇治市"，名称匹配盖不住）。"""
    from otomo.tools.pilgrimage.tool import _classify_entry

    assert _classify_entry("京都", "京都市", None) == ("core", None)  # 名称命中不需要坐标
    assert _classify_entry("京都", "宇治市", [34.906, 135.812])[0] == "core"  # ~15km 同城
    hanno = _classify_entry("东京", "饭能市", [35.855, 139.327])
    assert hanno[0] == "nearby" and 30 <= hanno[1] <= 60
    chichibu = _classify_entry("东京", "秩父市", [35.99, 139.08])
    assert chichibu[0] == "nearby"
    okayama = _classify_entry("大阪", "冈山市", [34.655, 133.919])
    assert okayama[0] == "bonus" and okayama[1] > 100
    assert _classify_entry("东京", "冲绳", [26.2, 127.7]) is None  # 圈外
    assert _classify_entry("桂林", "东京都", [35.68, 139.77]) is None  # 未知目的地→仅名称匹配


def test_pilgrimage_hotspot_cities_and_custom_center():
    """热海等巡礼热点入表（用户实测：热海查询曾走名称匹配全空→LLM 乱转）；
    表外长尾目的地支持 LLM 注入坐标。"""
    from otomo.tools.pilgrimage.tool import _REGION_CENTERS, _classify_entry

    assert "热海" in _REGION_CENTERS and "沼津" in _REGION_CENTERS
    # 热海查询：伊豆山(热海市内)=core；箱根 ~18km=core 圈；小田原 ~25km 边缘
    got = _classify_entry("热海", "伊豆山", [35.11, 139.08])
    assert got is not None and got[0] == "core"
    # 表外目的地（如 呉市）由 LLM 传坐标兜底：广岛市 ~20km → core 档
    got2 = _classify_entry("呉", "广岛市", [34.385, 132.455], center=(34.249, 132.566, 60, 160))
    assert got2 is not None and got2[0] == "core"
    # 无坐标的表外目的地仍只能名称匹配
    assert _classify_entry("呉", "广岛市", [34.385, 132.455]) is None


def test_tool_selector_coverage_and_subset():
    """渐进披露：分组+核心必须覆盖全部会暴露给 LLM 的工具（写工具除外），
    且典型查询暴露的工具数远小于全量。"""
    import asyncio as _a
    from otomo.factory import build_registry
    from otomo.tools.bangumi.client import BangumiClient
    from otomo.tools.moegirl.client import MoegirlClient
    from otomo.memory import LongTermMemory
    from otomo.agent.tool_router import ToolSelector, TOOL_GROUPS, CORE_TOOLS, META_TOOL

    async def scenario():
        async with BangumiClient() as c:
            reg = build_registry(c, MoegirlClient(), LongTermMemory())
            registered = set(reg._tools.keys())
            writes = {n for n, t in reg._tools.items() if getattr(t, "is_write", False)}
            grouped = set().union(*[g["tools"] for g in TOOL_GROUPS.values()]) | (CORE_TOOLS - {META_TOOL})
            # 非写工具必须全部可达（core 或某组），否则渐进披露会永久藏掉某能力
            assert (registered - writes) - grouped == set(), (registered - writes) - grouped
            # 组里不能有拼错的工具名
            assert grouped - registered == set(), grouped - registered
            full = reg.openai_tools()
            # 巡礼类查询：子集应含 pilgrimage 工具、且远小于全量
            sel = ToolSelector(reg, "我想去京都巡礼有什么番")
            names = {s["function"]["name"] for s in sel.schemas()}
            assert "get_pilgrimage_map" in names
            assert META_TOOL in names  # 逃生舱始终在
            assert len(names) < len(full) * 0.6
            # 逃生舱：未选中的组能被激活
            assert "vision" not in sel.active_groups
            sel.activate("vision")
            names2 = {s["function"]["name"] for s in sel.schemas()}
            assert "route_image_source" in names2
            # 关闭开关 → 回全量
            sel_off = ToolSelector(reg, "任意", enabled=False)
            assert len(sel_off.schemas()) >= len(full)

    _a.run(scenario())


def test_escape_hatch_step_tools_and_activation():
    """逃生舱确定性验证：step_tools 对 load_tool_group 回合成观察（不走 dispatch），
    ToolSelector.note_meta_calls 依模型请求激活工具组，下一轮 schema 即含新工具。"""
    from types import SimpleNamespace
    from otomo.agent import _common as C
    from otomo.agent.tool_router import ToolSelector, META_TOOL
    from otomo.factory import build_registry
    from otomo.tools.bangumi.client import BangumiClient
    from otomo.tools.moegirl.client import MoegirlClient
    from otomo.memory import LongTermMemory

    async def scenario():
        async with BangumiClient() as c:
            reg = build_registry(c, MoegirlClient(), LongTermMemory())
            sel = ToolSelector(reg, "随便问问")  # 无关键词 → 只有 core
            assert "get_pilgrimage_map" not in {s["function"]["name"] for s in sel.schemas()}
            # 模型"调用"逃生舱加载 pilgrimage
            fake_call = SimpleNamespace(
                id="c1",
                function=SimpleNamespace(name=META_TOOL, arguments='{"groups":["pilgrimage"]}'),
            )
            fake_msg = SimpleNamespace(tool_calls=[fake_call], content=None)
            messages: list = []
            events = []
            async for ev in C.step_tools(reg, fake_msg, messages, [], set(), None):
                events.append(ev)
            # 合成观察发出、且没有真的 dispatch 报 unknown tool
            obs = [e for e in events if e.type == "observation"]
            assert obs and obs[0].name == META_TOOL and obs[0].ok
            assert "get_pilgrimage_map" in obs[0].summary
            # runner 侧激活 → 下一轮暴露该工具
            sel.note_meta_calls(fake_msg)
            assert "get_pilgrimage_map" in {s["function"]["name"] for s in sel.schemas()}

    asyncio.run(scenario())


def test_upload_store_ttl_cleanup(tmp_path, monkeypatch):
    """uploads TTL：过期的 meta+bin 成对删除、未过期保留、ttl<=0 关闭清理。"""
    import base64
    import os
    import time

    from otomo.uploads import ImageUploadStore

    store = ImageUploadStore(base_dir=tmp_path)
    png = base64.b64encode(b"\x89PNG-fake-payload").decode()
    old = store.save_data_url(f"data:image/png;base64,{png}", filename="old.png")
    fresh = store.save_data_url(f"data:image/png;base64,{png}", filename="new.png")
    # 把 old 的两个文件 mtime 拨回 30 天前
    past = time.time() - 30 * 86400
    for p in store._paths(old.id):
        os.utime(p, (past, past))
    assert store.cleanup_expired(ttl_days=0) == 0  # 关闭清理
    removed = store.cleanup_expired(ttl_days=14)
    assert removed == 2  # old 的 json+bin
    assert not any(p.exists() for p in store._paths(old.id))
    assert all(p.exists() for p in store._paths(fresh.id))


def test_selector_exposes_write_tools_in_memory_plan_group():
    """口头确认写回：execute/undo 写工具经 memory_plan 组暴露给模型（护栏在工具层 confirmed 参数）。"""
    import asyncio as _a
    from otomo.factory import build_registry
    from otomo.tools.bangumi.client import BangumiClient
    from otomo.tools.moegirl.client import MoegirlClient
    from otomo.memory import LongTermMemory
    from otomo.agent.tool_router import ToolSelector

    async def scenario():
        async with BangumiClient() as c:
            reg = build_registry(c, MoegirlClient(), LongTermMemory())
            sel = ToolSelector(reg, "帮我把这部加入在看，直接确认写回")
            names = {s["function"]["name"] for s in sel.schemas()}
            assert "prepare_bangumi_write_action" in names
            assert "execute_bangumi_write_action" in names  # 关键词"在看/确认/写回"命中 memory_plan
            # 无关查询不暴露写工具
            sel2 = ToolSelector(reg, "孤独摇滚是谁做的")
            names2 = {s["function"]["name"] for s in sel2.schemas()}
            assert "execute_bangumi_write_action" not in names2

    _a.run(scenario())


def test_trajectory_flywheel_log_feedback_export(tmp_path, monkeypatch):
    """RL 轨迹飞轮：落轮次 → 记反馈 → 导出 SFT/DPO（脱敏、👎不进SFT、偏好成对）。"""
    import json

    from otomo import trajectory
    from otomo.config import settings

    monkeypatch.setattr(settings, "trajectory_dir", str(tmp_path))
    monkeypatch.setattr(settings, "trajectory_log_enabled", True)

    common = dict(session_id="s1", owner="user:nagi", runner="adaptive")
    msgs = [
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "推荐点治愈番，我邮箱 a@b.com"},
        {"role": "assistant", "content": "推荐《摇曳露营》 https://x.com/hook?token=abc123"},
    ]
    trajectory.log_turn(turn_id="t1", user_message="推荐点治愈番", final_answer="推荐《摇曳露营》",
                        messages=msgs, tools_called=["recommend_subjects"], usage_tokens=1234, **common)
    trajectory.log_turn(turn_id="t2", user_message="推荐点治愈番", final_answer="随便看点啥",
                        messages=msgs, tools_called=[], usage_tokens=200, **common)
    trajectory.record_feedback(turn_id="t1", session_id="s1", owner="user:nagi", rating="up")
    trajectory.record_feedback(turn_id="t2", session_id="s1", owner="user:nagi", rating="down")

    files = list(tmp_path.glob("*.jsonl"))
    assert any(f.name == "feedback.jsonl" for f in files) and len(files) == 2
    # owner 伪匿名
    day = next(f for f in files if f.name != "feedback.jsonl")
    rec = json.loads(day.read_text(encoding="utf-8").splitlines()[0])
    assert "nagi" not in json.dumps(rec)

    from scripts.export_trajectories import _scrub, load_all
    turns, fb = load_all()
    assert len(turns) == 2 and fb["t1"]["rating"] == "up" and fb["t2"]["rating"] == "down"
    assert "<email>" in _scrub("a@b.com") and "token=<redacted>" in _scrub("u?token=abc")

    # 导出行为：SFT 排除 👎；DPO 对成型
    import subprocess, sys, os
    env = {**os.environ, "TRAJECTORY_DIR": str(tmp_path)}
    out = subprocess.run(
        [sys.executable, "-m", "scripts.export_trajectories", "--sft", str(tmp_path / "sft.jsonl"), "--dpo", str(tmp_path / "dpo.jsonl")],
        capture_output=True, text=True, env=env, cwd=str(pathlib_Path(__file__).resolve().parents[1]),
    )
    assert out.returncode == 0, out.stderr
    sft = [json.loads(x) for x in (tmp_path / "sft.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(sft) == 1 and sft[0]["meta"]["turn_id"] == "t1"  # 👎 t2 被排除
    assert all(m["role"] != "system" for m in sft[0]["messages"])  # 默认剥 system
    assert "<email>" in json.dumps(sft[0], ensure_ascii=False)  # 脱敏生效
    dpo = [json.loads(x) for x in (tmp_path / "dpo.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(dpo) == 1 and dpo[0]["meta"]["chosen_turn"] == "t1" and dpo[0]["meta"]["rejected_turn"] == "t2"


from pathlib import Path as pathlib_Path  # noqa: E402


def test_shadow_style_taste_sync():
    """Shadow 式口味对比：隐藏分百分位归一、综合评级、想看推荐、收缩排名。"""
    from otomo.tools.user_analysis.tool import (
        _build_affinity, _percentile_map, _sample_confidence, _shadow_curve, _sync_level,
    )

    def item(sid, rate, type_=2, name=""):
        return {"subject_id": sid, "rate": rate, "type": type_,
                "subject": {"id": sid, "name_cn": name or f"作品{sid}", "tags": []}}

    # 百分位：严苛党的 7 分应比送分党的 7 分位置更高
    strict = {i: item(i, r) for i, r in enumerate([3, 4, 5, 5, 6, 7], 1)}   # 7 是最高分
    generous = {i: item(i, r) for i, r in enumerate([7, 8, 8, 9, 9, 10], 1)}  # 7 是最低分
    assert _percentile_map(strict)[7] > 0.3 and _percentile_map(generous)[7] < -0.3

    assert _shadow_curve(1.0) == 1.0 and _shadow_curve(0.0) == 0.0
    assert _sync_level(100) == 10 and _sync_level(0) == 1
    assert _sample_confidence(50) == 1.0 and 0 < _sample_confidence(5) < 1

    # 完全同向的两人：高同步分；想看推荐拿到对方打过分的我的想看
    own = [item(i, r) for i, r in [(1, 9), (2, 8), (3, 3), (4, 10)]] + [item(99, 0, type_=1)]
    peer = [item(i, r) for i, r in [(1, 10), (2, 9), (3, 2), (4, 9)]] + [item(99, 8, name="想看的那部")]
    aff = _build_affinity("peer", own, peer)
    assert aff.sync_score is not None and aff.sync_score >= 80
    assert aff.sync_level == _sync_level(aff.sync_score)
    assert len(aff.wishlist_picks) == 1 and aff.wishlist_picks[0].peer_rate == 8


def test_friends_matrix_shrinkage_ranking(monkeypatch):
    """好友矩阵：小样本高分被收缩到中位附近，大样本稳定分排前。"""
    import asyncio as _a

    from otomo.tools.user_analysis import tool as ua

    def item(sid, rate, type_=2):
        return {"subject_id": sid, "rate": rate, "type": type_,
                "subject": {"id": sid, "name_cn": f"作品{sid}", "tags": []}}

    my_items = [item(i, 7 + (i % 3)) for i in range(1, 61)]
    # A：60 个共同评分、约 75% 一致（大样本高同步但非满分）；B：3 个完全一致（小样本满分）；C：反向（垫底）
    friend_a = [item(i, (9 - (i % 3)) if i % 4 == 0 else (7 + (i % 3))) for i in range(1, 61)]
    friend_b = [item(i, 7 + (i % 3)) for i in range(1, 4)]
    friend_c = [item(i, 9 - (i % 3)) for i in range(1, 41)]
    collections = {"me": my_items, "a": friend_a, "b": friend_b, "c": friend_c}

    class FakeClient:
        async def get_me(self):
            return {"username": "me"}
        async def get_all_user_collections(self, username, stype, ct, max_items=0):
            return collections[username]

    async def fake_friends(username, limit):
        return [{"username": "a"}, {"username": "b"}, {"username": "c"}], f"https://bgm.tv/user/{username}/friends"

    monkeypatch.setattr(ua, "_fetch_friends", fake_friends)
    tool = ua.CompareUserTasteTool(FakeClient())
    res = _a.run(tool.run(ua.TasteCompareArgs(mode="friends_matrix", friends_limit=5)))
    assert res.ok and res.data and len(res.data.matrix) == 3
    by_name = {e.username: e for e in res.data.matrix}
    a, b, c = by_name["a"], by_name["b"], by_name["c"]
    assert res.data.matrix[-1].username == "c"  # 反向口味垫底
    assert b.shrunk_score < b.sync_score  # 小样本满分被往中位拉
    assert abs(a.shrunk_score - a.sync_score) <= 2  # 大样本几乎不动
    assert b.sync_score == 100 and b.shrunk_score < 100


def test_adaptive_thresholds_and_watching_together():
    """Shadow 补偷：自适应好评/差评线按各自分布均衡；共同追新=双方 type=3 交集。"""
    from otomo.tools.user_analysis.tool import _auto_thresholds, _build_affinity

    def item(sid, rate, type_=2):
        return {"subject_id": sid, "rate": rate, "type": type_,
                "subject": {"id": sid, "name_cn": f"作品{sid}", "tags": []}}

    # 送分党（8-10 扎堆）的三档线应明显高于严苛党（3-7）
    generous = {i: item(i, r) for i, r in enumerate([8, 8, 9, 9, 9, 10, 10, 10, 10], 1)}
    strict = {i: item(i, r) for i, r in enumerate([3, 4, 4, 5, 5, 6, 6, 7, 7], 1)}
    g_lo, g_hi = _auto_thresholds(generous)
    s_lo, s_hi = _auto_thresholds(strict)
    assert g_hi > s_hi and g_lo > s_lo

    # 送分党的 9 分与严苛党的 6 分同档（各自的"中/好"边界附近），硬编码 8/4 会漏掉严苛党的好评
    own = [item(1, 6), item(2, 7), item(3, 3), item(4, 5), item(5, 4), item(6, 7)]
    peer = [item(1, 9), item(2, 10), item(3, 8), item(4, 8), item(5, 8), item(6, 10)]
    own += [item(10, 0, 3), item(11, 0, 3)]      # 我在看 10/11
    peer += [item(10, 0, 3), item(12, 0, 3)]     # 对方在看 10/12 → 共同追新 = 10
    aff = _build_affinity("peer", own, peer)
    assert aff.own_thresholds is not None and aff.peer_thresholds is not None
    assert aff.own_thresholds[1] < aff.peer_thresholds[1]  # 严苛党好评线更低
    assert any(x.user_rate == 7 for x in aff.liked_together)  # 严苛党的 7 分进了共同好评
    assert [w.id for w in aff.watching_together] == [10]
    # 显式覆盖阈值生效
    aff2 = _build_affinity("peer", own, peer, like_threshold=10)
    assert all(x.user_rate >= 10 for x in aff2.liked_together) or not aff2.liked_together
