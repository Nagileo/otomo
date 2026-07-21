"""用户模拟器：LLM 扮演不同 persona 的 ACGN 用户，多轮驱动 Otomo 产出对话轨迹。

docs/15 拖最久的欠账。多轮 RL 数据与 T-PRA 式 critiquing 训练的前提是能造出**多轮**
交互；人工写 golden turns 太贵。用户模拟器 = 一个 LLM 按 persona+目标生成开场问 +
根据 Otomo 上一轮回答生成有信息量的追问（换一批/更具体/挑刺/换方向），驱动真实多轮。

产出：
  1. trajectories/simulated_YYYY-MM-DD.jsonl —— 每轮完整 messages，喂 RL 飞轮
  2. --emit-cases：把多轮对话转成 golden multi_turn case 骨架（人工补断言后入 golden）

诚实边界：模拟用户不能替代真人分布，但能扩多轮覆盖、暴露"多轮里记忆/剧透/换一批
是否连贯"的 bug——这正是单轮 golden 测不到的。persona 少而典型，不追求规模。

用法（backend/ 下）：
  python -m scripts.simulate_users --personas 3 --max-turns 4
  python -m scripts.simulate_users --personas 2 --max-turns 3 --emit-cases ../eval/simulated_cases.yaml
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

from openai import AsyncOpenAI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from otomo.agent.contracts import AgentState, FinalEvent, ToolCallEvent  # noqa: E402
from otomo.config import settings  # noqa: E402
from otomo.factory import build_runner  # noqa: E402
from otomo.tools.bangumi.client import BangumiClient  # noqa: E402
from otomo.tools.moegirl.client import MoegirlClient  # noqa: E402

# 典型 persona：口味 + 本轮目标 + 交互风格（少而典型，覆盖不同意图）
PERSONAS = [
    {"name": "百合厨", "goal": "想找几部没看过的百合番，会嫌弃太热门的想要冷门",
     "style": "挑剔，喜欢说'这些我都看过了，来点冷门的'"},
    {"name": "补番新人", "goal": "只看过《进击的巨人》，想入坑更多，需要引导",
     "style": "小白，会问'这个讲什么''适合新手吗'"},
    {"name": "季度追番党", "goal": "想知道这季（2026年7月）有什么值得追，追问在哪看",
     "style": "务实，关心更新时间和观看渠道"},
    {"name": "考据宅", "goal": "喜欢深挖制作组和声优八卦，会顺着一个作品问关联",
     "style": "刨根问底，'那导演还做过什么''这个声优还配过谁'"},
    {"name": "口碑党", "goal": "只看高分神作，会问评价、争议、值不值得看",
     "style": "谨慎，'这个口碑怎么样''会不会烂尾'"},
]


def _sim_system(p: dict) -> str:
    return (
        f"你在扮演一个真实的中文 ACGN 爱好者用户（人设：{p['name']}），正在和一个动画推荐助手对话。"
        f"你的目标：{p['goal']}。你的风格：{p['style']}。\n"
        "规则：\n"
        "- 每次只说一句像真人会打出来的话（口语、简短、可以带点情绪），不要解释你在扮演。\n"
        "- 根据助手上一轮的回答，生成**有信息量的追问**：可以是换一批、要更具体、挑刺、"
        "顺着某个作品深入、或转向相关问题。不要机械复读。\n"
        "- 如果目标基本达成或聊了够多，只回复 [END] 结束对话。"
    )


async def _sim_next(sim_llm: AsyncOpenAI, persona: dict, history: list[dict]) -> str:
    """让模拟用户 LLM 生成下一句用户输入。history 是 [{role: user/assistant, content}]。"""
    messages = [{"role": "system", "content": _sim_system(persona)}]
    # 视角翻转：Otomo 的回答对模拟器是"对方(assistant)"，模拟器自己的话是 user
    for h in history:
        messages.append({"role": "user" if h["role"] == "assistant" else "assistant", "content": h["content"][:1500]})
    if not history:
        messages.append({"role": "user", "content": "（开始对话，说出你的第一句话）"})
    resp = await sim_llm.chat.completions.create(model=settings.llm_model, messages=messages, temperature=0.9)
    return (resp.choices[0].message.content or "").strip()


async def run_persona(persona: dict, client: BangumiClient, sim_llm: AsyncOpenAI, max_turns: int) -> dict:
    runner = build_runner(client, MoegirlClient(), "adaptive")
    state = AgentState()
    history: list[dict] = []
    turns: list[dict] = []
    for _t in range(max_turns):
        user_msg = await _sim_next(sim_llm, persona, history)
        if not user_msg or "[END]" in user_msg:
            break
        answer, tools = "", []
        async for ev in runner.stream(user_msg, state):
            if isinstance(ev, ToolCallEvent):
                tools.append(ev.name)
            elif isinstance(ev, FinalEvent):
                answer = ev.answer
        history.append({"role": "user", "content": user_msg})
        history.append({"role": "assistant", "content": answer})
        turns.append({"question": user_msg, "answer": answer, "tools": tools})
        print(f"    [{persona['name']}] U: {user_msg[:50]}  →  {len(tools)} 工具")
    return {"persona": persona["name"], "goal": persona["goal"], "turns": turns}


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--personas", type=int, default=3, help="用前 N 个 persona")
    ap.add_argument("--max-turns", type=int, default=4)
    ap.add_argument("--out-dir", default="cache/trajectories")
    ap.add_argument("--emit-cases", default="", help="同时导出 golden multi_turn case 骨架 yaml")
    args = ap.parse_args()

    if not settings.llm_api_key:
        sys.exit("需要 LLM_API_KEY（模拟器和 Otomo 都用它）")
    sim_llm = AsyncOpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key)
    personas = PERSONAS[: args.personas]

    convos: list[dict] = []
    async with BangumiClient() as client:
        for p in personas:
            print(f"—— persona: {p['name']}")
            convos.append(await run_persona(p, client, sim_llm, args.max_turns))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    traj = out_dir / f"simulated_{date.today().isoformat()}.jsonl"
    with traj.open("a", encoding="utf-8") as f:
        for convo in convos:
            f.write(json.dumps(convo, ensure_ascii=False) + "\n")
    print(f"\n轨迹落盘 {traj}: {len(convos)} 段对话")

    if args.emit_cases:
        lines = ["# 用户模拟器产出的 multi_turn 骨架；断言（expect_*）需人工补全后再纳入 golden\n"]
        for convo in convos:
            cid = f"sim_{convo['persona']}".replace(" ", "_")
            lines.append(f"- id: {cid}\n  kind: multi_turn\n  note: 模拟 persona「{convo['persona']}」\n  turns:")
            for turn in convo["turns"]:
                q = turn["question"].replace('"', "'")[:80]
                tools = ", ".join(sorted(set(turn["tools"]))) or ""
                lines.append(f"    - question: \"{q}\"\n      expect_tools: [{tools}]  # TODO 人工核对")
        Path(args.emit_cases).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"case 骨架写入 {args.emit_cases}（需人工补断言）")


if __name__ == "__main__":
    asyncio.run(main())
