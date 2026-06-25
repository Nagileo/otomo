# 09 · 手搓 runtime vs LangGraph 对比

> 目的：不是证明谁更好，而是**做出两个等价实现、客观对比、给出场景化选择**——体现"既能手搓核心、又懂框架权衡"的工程判断力。Otomo 以手搓为主力（脊：可验证多跳 → RL），LangGraph 作对照。

## 两个实现（同一 `AgentRunner` 接口、同一套自建工具）

| | 手搓 | LangGraph |
|---|---|---|
| 实现 | [`react.py`](../backend/otomo/agent/react.py)（两阶段 ReAct）+ [`_common.py`](../backend/otomo/agent/_common.py) 共享 | [`langgraph_runner.py`](../backend/otomo/agent/langgraph_runner.py)：`create_react_agent`(prebuilt) + 适配层 |
| 工具 | `ToolRegistry`（自建，typed） | **复用同一 registry**——每个 Tool 包成 `StructuredTool`，执行仍走 `registry.dispatch` |
| 实测 | 17 手写 + 45 自动 case；图谱级 set-F1 | 跑通同问题（孤独摇滚年份：search→答 2022），产出同样的 `AgentEvent` |

**关键事实：同一套自建工具在两种 runtime 下都能跑**——工具层与 runtime 解耦，这是对比能成立的前提，也证明工具设计的独立性。

## 8 轴对比

| 轴 | 手搓 | LangGraph | 谁占优 |
|---|---|---|---|
| **代码量/起步** | runner ~100 行 + 共享 `_common` | prebuilt `create_react_agent` 几行 + 适配层 ~70 行 | LangGraph（少写编排；但适配现有契约/工具仍要胶水） |
| **流式控制** | **两阶段真 token 流式**：工具循环静默、最终答案才流式吐，CoT 不外露 | prebuilt 要 `astream_events` 才 token 流式（繁）；本实现用 `ainvoke` 跑完再映射（非流式） | 手搓（对"何时流式、流式什么"控制更细） |
| **结构化 trace / 可验证** | `ObservationEvent` 带 **canonical `EntityRef`**，直接喂图谱级 verifier 的路径验证 | tool 返回字符串，路径验证需额外解析适配 | 手搓（**直接适配 moat：可验证多跳→RL 奖励**） |
| **控制粒度** | 能精确插入 DSML 泄漏纠错、compose 兜底、followup 生成 | prebuilt 是黑盒 ReAct，要定制得拆成 `StateGraph` 自己连节点 | 手搓（细粒度）/ LangGraph（要粒度就回到手写图） |
| **生态/持久化** | checkpoint/恢复要自建（roadmap C2） | 自带 checkpointer、人机协作、时间旅行、持久化 | **LangGraph**（长任务可恢复省力） |
| **可调试/可预测** | 零框架依赖，行为完全可预测，print/断点即可 | 抽象层多（Runnable/Graph/Channel/message 类型），调试要懂框架内部 | 手搓 |
| **依赖** | 仅 `openai` SDK | `langgraph`+`langchain-openai`+`langchain-core` 一串，版本较敏感 | 手搓 |
| **可验证指标对比** | `eval.runner --runner react` | `eval.runner --runner langgraph`（同 benchmark） | 同台可比（见下） |

## 可验证指标对比（数据驱动，可复现）

同一套 golden cases 上跑两个 runtime，比通过率 / 工具调用数 / 延迟：

```bash
python -m otomo.eval.runner --runner react     --limit 6
python -m otomo.eval.runner --runner langgraph  --limit 6   # 需 pip install -e ".[langgraph]"
```

> 注：图谱级**路径有效率**依赖 `ObservationEvent.entities`（手搓填了 canonical 实体），LangGraph 的 prebuilt tool 返回字符串、未带结构化实体，故路径验证对其不适用——对比聚焦 **answer 级 set-F1 / 通过率 / 工具数 / 延迟**。这点本身就是结论之一：**要做"可验证多跳 + RL 奖励"，手搓的结构化 trace 是刚需**。

## 结论：何时用哪个

- **选手搓**：需要精细控制流式时机、注入纠错/兜底、**结构化可验证 trace（→ Agentic-RL 奖励）**、零依赖、教学/完全掌控。→ **Otomo 主力**，因为项目的护城河（可验证多跳 → RL）恰恰要这些。
- **选 LangGraph**：需要开箱的 checkpoint/恢复、人机协作、复杂图编排、团队协作与快速起步。→ 长流程生产系统、多人协作场景。

**Otomo 的选择**：手搓为脊（moat 需要 trace 可验证 + RL 奖励信号），LangGraph 作对照实现保留在 `--runner langgraph`——既证明"会用框架"，也用实测说明"为什么这个项目选手搓"。这比单纯"我用了 LangGraph"或"我手搓了一切"都更能体现判断力。
