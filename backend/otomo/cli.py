"""命令行跑通整条 agent 链路，无需前端：

    python -m otomo.cli "白色相簿2 里 冬马和纱 的声优还配过哪些番？"

会打印结构化轨迹（工具调用/观察）与流式最终答案。需要 .env 里配好 LLM_API_KEY 与 BANGUMI_USER_AGENT。
"""
from __future__ import annotations

import asyncio
import sys

from .agent.contracts import (
    AnswerDeltaEvent,
    ErrorEvent,
    FinalEvent,
    ObservationEvent,
    ToolCallEvent,
)
from .factory import build_runner
from .tools.bangumi.client import BangumiClient

# Windows 控制台默认 GBK，强制 UTF-8 以正确输出中文与符号
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass

DIM, BOLD, CYAN, GREEN, RED, RESET = "\033[2m", "\033[1m", "\033[36m", "\033[32m", "\033[31m", "\033[0m"


async def run(question: str) -> None:
    client = BangumiClient()
    runner = build_runner(client)
    answering = False
    try:
        async for ev in runner.stream(question):
            if isinstance(ev, ToolCallEvent):
                print(f"{CYAN}→ 调用 {ev.name}{RESET} {DIM}{ev.args}{RESET}")
            elif isinstance(ev, ObservationEvent):
                mark = GREEN + "✓" if ev.ok else RED + "✗"
                print(f"  {mark} {ev.summary}{RESET}")
            elif isinstance(ev, AnswerDeltaEvent):
                if not answering:
                    print(f"\n{BOLD}回答：{RESET}", end="")
                    answering = True
                print(ev.text, end="", flush=True)
            elif isinstance(ev, FinalEvent):
                print(f"\n\n{DIM}— 步数 {ev.steps}；来源 {len(ev.sources)} 条 —{RESET}")
                for s in ev.sources:
                    print(f"  {DIM}· {s.title} {s.url}{RESET}")
            elif isinstance(ev, ErrorEvent):
                print(f"{RED}错误：{ev.message}{RESET}")
    finally:
        await client.aclose()


def main() -> None:
    if len(sys.argv) < 2:
        print('用法：python -m otomo.cli "你的问题"')
        raise SystemExit(1)
    asyncio.run(run(" ".join(sys.argv[1:])))


if __name__ == "__main__":
    main()
