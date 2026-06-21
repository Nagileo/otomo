# Otomo Backend (A1 骨架)

Python + FastAPI 的 Agent 后端。A1 实现：手搓 ReAct runner + 自建 Bangumi 只读图谱工具 + SSE 流式。

## 结构

```
otomo/
├─ config.py            # 环境配置（.env）
├─ llm.py               # OpenAI 兼容 LLM（默认 DeepSeek，一键换本地 Qwen）
├─ agent/
│  ├─ contracts.py      # Tool / ToolResult / AgentState / AgentEvent / AgentRunner
│  ├─ registry.py       # 工具注册表 + 分发
│  ├─ react.py          # 手搓 ReAct（两阶段：工具循环 → 流式答案，CoT 不外露）
│  └─ prompts.py
├─ tools/bangumi/       # 自建 thin client + typed models + 7 个图谱工具
├─ api/app.py           # FastAPI：/health + /chat (SSE)
├─ factory.py           # 组装 runner
└─ cli.py               # 命令行跑通整条链路
```

## 跑起来

```bash
cd backend
python -m venv .venv && source .venv/Scripts/activate   # Windows Git Bash
pip install -e ".[dev]"
cp .env.example .env        # 填 LLM_API_KEY；BANGUMI_USER_AGENT 改成你的；BANGUMI_TOKEN 只读可空
```

**命令行验证（无需前端）：**

```bash
python -m otomo.cli "白色相簿2 里 冬马和纱 的声优还配过哪些番？"
```

会打印工具调用/观察轨迹 + 流式最终答案。

**起 HTTP 服务（给前端用）：**

```bash
uvicorn otomo.api.app:app --reload --port 8000
# POST /chat  body={"message": "..."}  → SSE: tool_call / observation / answer_delta / final
```

## 设计要点（对应 docs）

- **工具全自建**：不接 Bangumi-MCP/bgm-cli；手写 thin async httpx client，**强制 User-Agent**，进程内 TTL 缓存占位（A5 换 Redis）。
- **typed result**：每个工具自定义 Pydantic result schema，禁裸 Any。
- **CoT 不外露**：trace 只含 tool_call/observation/answer 结构化事件；最终答案在「无工具」阶段流式生成。
- **可验证**：答案基于 Bangumi 图谱真值；后续接 Verifier 与自动 benchmark（Track A2/A3）。

## 测试

```bash
pytest        # 无网络的契约/注册表 smoke 测试
```
