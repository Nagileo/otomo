# Otomo Backend

Python + FastAPI 的 Agent 后端：手搓 ReAct / Plan-Execute / Adaptive 三种 runner + 自建工具层（Bangumi 图谱 + 萌娘 / 维基 RAG + web search + 推荐）+ SSE 流式 + typed Verifier。

## 结构

```
otomo/
├─ config.py            # 环境配置（.env）
├─ llm.py               # OpenAI 兼容 LLM（默认 DeepSeek，一键换本地 Qwen）
├─ agent/
│  ├─ contracts.py      # Tool / ToolResult / AgentState / AgentEvent / AgentRunner
│  ├─ react.py          # 手搓 ReAct（两阶段：工具循环 → 流式答案，CoT 不外露）
│  ├─ plan_execute.py   # Plan-Execute（plan→execute→reflect→补救→compose）
│  ├─ adaptive.py       # 路由器：按复杂度分流 SIMPLE / SYNTHESIS / 复杂 plan
│  └─ registry.py · prompts.py · _common.py
├─ tools/               # 自建工具层（每个 typed result，禁裸 Any）
│  ├─ bangumi/          # thin client + 8 个图谱工具
│  ├─ moegirl/ wiki/    # 萌娘 / 中文维基 RAG（按需取不入库 + 来源引用）
│  ├─ websearch/        # 全网兜底（tavily/serper/exa/bocha 抽象 + 降级）
│  ├─ recommend/        # 多策略召回推荐（标签 / 图谱 / 协同 + 平衡打分）
│  └─ profile/ comments/ videos/   # 口味画像 / 短评 / B站外链
├─ eval/                # typed Verifier + golden cases + 自动生成器 + runner
├─ api/app.py           # FastAPI：/health + /chat (SSE)
└─ factory.py · cli.py  # 组装 runner / 命令行跑通整条链路
```

## 跑起来

```bash
# 用专用 conda 环境 otomo（隔离；后续 torch/vLLM 等 ML 依赖也装这里）
conda create -n otomo python=3.12 -y
conda activate otomo
conda install -c conda-forge nodejs -y     # 前端 npm 也装进同一环境，一处激活通吃前后端
cd backend && pip install -e ".[dev]"
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
- **可验证**：答案基于 Bangumi 图谱真值；typed Verifier + 手写 17 / 自动 24 golden cases 已落地（正升级到图谱级 set-F1 / 路径验证）。

## 测试

```bash
pytest        # 无网络的契约/注册表 smoke 测试
```

## 本地 Smoke

```bash
# 启动临时后端并检查 health / auth session / CSRF / upload / Bangumi 搜索
python scripts/smoke_http.py --start-server

# 额外验证本地 BANGUMI_TOKEN 绑定与视觉反馈写入（会写入当前用户本地 memory）
python scripts/smoke_http.py --start-server --dev-token-login

# 额外验证 /chat SSE（会调用配置的 LLM）
python scripts/smoke_http.py --start-server --chat "你好，简单介绍 Otomo"

# 真实 VLM 通路测试（会调用 VLM_PROVIDER/VLM_MODEL）
python scripts/smoke_vlm.py path/to/image.png --mode screenshot
python scripts/smoke_vlm.py path/to/frame.jpg --mode ocr --ocr-mode ppt
```
