from otomo.agent.contracts import ObservationEvent, PlanEvent, ProgressEvent, ToolCallEvent
from otomo.session_trace import step_from_event, trace_item_from_event


def test_session_trace_builds_frontend_stable_shapes():
    plan = PlanEvent(summary="先找条目，再核对评价")
    call = ToolCallEvent(name="search_subjects", args={"keyword": "孤独摇滚"})
    progress = ProgressEvent(summary="正在读取 Bangumi", tool="search_subjects", current=1, total=2)
    observation = ObservationEvent(name="search_subjects", ok=True, summary="找到 3 个候选")

    assert trace_item_from_event(plan) == {"kind": "note", "text": "📋 先找条目，再核对评价"}
    assert trace_item_from_event(call) == {
        "kind": "call",
        "name": "search_subjects",
        "args": {"keyword": "孤独摇滚"},
    }
    assert trace_item_from_event(progress)["current"] == 1
    assert step_from_event(observation) == "✓ 找到 3 个候选"
