# 03 · Agent 接口契约与编排

> 核心原则：**先钉死接口契约，再写实现。** 手搓核心与 LangGraph 版必须满足**同一套契约**，对比才公平。

## 1. 三个核心契约（用 Pydantic 定义）

> 工具 / Skills / MCP server 全部**自建**（参考但不接入 Bangumi-MCP/bgm-cli，见 [02 §1.5](02-data-sources.md)）。

### Tool
```
Tool:
  name: str                      # 唯一名
  description: str               # 给 LLM 的说明
  args_schema: pydantic.BaseModel  # typed 入参（function-calling schema 来源）
  result_schema: pydantic.BaseModel# typed 出参（每个工具自定义，禁止裸 Any）
  run(args) -> ToolResult[T]       # 同步/异步执行
  # 元信息：是否写操作(需人工确认)、是否触外网(限流)、超时
ToolResult[T]:                   # T = 该工具的 typed result schema（Pydantic），不用裸 Any
  ok: bool
  data: T                        # 工具专属 typed 结构 —— verifier / trace replay / 评测都依赖它稳定
  source: list[Citation]         # 来源（萌娘/维基必填，用于回答挂链接）
  error: str | None
```

### AgentState
```
AgentState:
  messages: list[Message]        # 对话历史
  plan: list[Step] | None        # Plan-Execute 的计划
  scratchpad: str                # ReAct 内部思考缓冲（ephemeral·不外露·不持久化，见 §2.5）
  short_term: dict               # 短期记忆（会话状态、视觉/检索证据、中间结果）
  memory_refs: list[str]         # 长期记忆检索引用
  trace: list[TraceEvent]        # plan/tool_call/observation/verifier 事件
  status: enum{running, awaiting_approval, done, failed}
```

### AgentRunner
```
AgentRunner:
  stream(input, state) -> Iterator[AgentEvent]   # 流式吐 token + tool/state 事件
  # 同一签名：手搓版、LangGraph 版都实现它 → 可一键切换 + A/B
```

## 2. 编排范式

### ReAct（Reason → Act → Observe，循环）
- ~150–300 行：`while` 包住模型调用；解析 tool-call → 执行 → 结果回填 messages；max-iteration 守卫。
- 适合：开放式多跳检索/问答（边走边看）。

### Plan-and-Execute
- 先出计划（步骤列表）→ 逐步执行 → 可重规划。共享同一 Tool Registry。
- 适合：补番顺序/季番分诊等"结构清晰、可预先拆解"的任务。

### Verifier（两级，复用作者 SAR 经验）
- **检索 Verifier**：召回是否命中正确实体/边（对 Bangumi 真值校验）。
- **答案 Verifier**：最终事实是否落在图谱真值上（exact-match / precision-recall）；设定类用 LLM-as-judge + 来源核对。
- 失败 → 触发重规划 / 降级 / 终止。这是后期 RL 过程奖励的来源。

## 2.5 CoT 与可观测边界（重要）

- **模型内部保留 CoT**：策略模型该有的链式思考能力**不剥夺**——ReAct 的 Thought、reasoning 模型的推理照常用于提升决策质量。
- **但裸 CoT 不外露、不持久化**：用户面与 trace 面板只展示**结构化执行事件**——plan 摘要 / tool call(name+args) / observation / verifier 结果 / final rationale。原始思考是 ephemeral 的内部状态，用完即弃。
- **三个理由**：① 多家模型条款不鼓励透出原始推理；② 裸 CoT 噪声大、不稳定，会拖垮 trace replay 与评测；③ 结构化轨迹对开源更专业、可 typed、可回放。
- **与流式输出的关系**：照常流式，流的是**最终答案 token + 上述结构化事件**，不是思考全文。

## 3. 手搓 → LangGraph 对比计划（工程叙事的主线）

**Stage 0** 钉接口（上面三个契约）。
**Stage 1** 手搓核心：ReAct + Plan-Execute + Verifier + SSE 流式 + 朴素持久化（append-only JSONL trace + Postgres 每 run 一行 state）。
**Stage 2** 手动加"难的部分"：单表 checkpointer（每步 state 快照入 Postgres）+ 粗糙 human-in-the-loop（写 `status=awaiting_approval`，从最近 checkpoint 恢复）。**故意先手写一遍，才能体会框架价值。**
**Stage 3** 用 LangGraph 重写同一 agent：同工具、同 state schema、同 verifier；换上 LangGraph checkpointer(Postgres) + `interrupt()` HITL + LangSmith trace。
**Stage 4** 写对比报告，固定以下 8 轴测量/叙述：

| 轴 | 在两版上测什么 |
|---|---|
| 代码复杂度 | 编排核心 LOC、分支圈复杂度、加一个新工具/分支的成本 |
| 可观测性 | 自建 JSONL+面板 vs LangSmith；定位一次坏 tool-call 的耗时 |
| 持久化/容错 | 让 run 可崩溃恢复的代码量；能否中途 resume |
| 流式 | 把 token + 中间 tool/state 事件都推到 SSE 的成本 |
| Human-in-the-loop | 加审批门 + 恢复的成本 |
| 多 agent | 加第二个 agent（如 search + recommender）并路由的成本 |
| 可调试/time-travel | 能否从第 N 步回放走另一分支 |
| 锁定/可移植 | DIY 无；LangGraph 低（Apache-2.0，可自托管） |

**预期结论（先作为假设，再验证）**：DIY 胜在理解、依赖轻、单 agent 简单；LangGraph 在 checkpoint/HITL/多 agent 路由/trace 级调试上决定性占优——大约就在"anime agent 长出第二个 agent 或可恢复长任务"时。

## 4. 备选框架（评估后默认不用，但记录理由）
- **LlamaIndex Workflows**：事件驱动、RAG 重时占优。
- **Pydantic-AI**：类型安全、DX 好；即便手搓也建议用 Pydantic 定 Tool/State schema。
- **托管 runtime（Claude Agents/OpenAI Agents SDK）**：锁定重、state 不透明，与"开源自托管学习"目标冲突，**不用**。

## 5. 安全（领域原生，非硬塞）
- **Prompt 注入检测**：萌娘正文可能含注入，检索内容入上下文前过滤。
- **白名单校验**：工具参数白名单，降低误调用。
- **R18 门控**：Bangumi NSFW 需 token，按用户态门控。
- **写操作人工确认**：改收藏/进度等写操作必须 human-confirmed，不自动改。
