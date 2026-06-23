# 07 · 版本路线图

> 原则：**一个项目、不拆碎；整个愿景不缩水。** "轨"与"版本"只是同一项目里的**建造次序**，不是把目标砍成多个 demo。每个里程碑都应**能 git tag、能写博客、能加简历**。守住红线：**不训练的 Agent 主体必须完整实现**，RL 作为可行后增项。

## 优先级（2026-06 调整）

1. **推荐系统（Track B）+ 产品能力广度（面 / A4 RAG）+ 工程纵深（工 / C1-C3）= 当前高优先级** —— 这是项目"血肉与纵深"，也是作者想补的推荐/工程能力。
2. **Agentic-RL（C4/C5）推后** —— 需换成自有可训练模型（本地 Qwen）再做，作为后期 capstone，不阻塞前面。
3. Agent 脊柱（Track A）继续把评测/RAG 补到能支撑上面两点即可。

## 三条并行轨（不是三个任务）

### Track A · Agent 脊柱（eval 前置）
- **A1**：手搓 Bangumi client + 工具 + 手搓 ReAct 循环 + FastAPI/SSE + 结构化 trace（非裸 CoT）+ 单/两跳图谱任务 + 最简 Next.js chat。
- **A2**：手搓 Plan-Execute + 两级 **typed** Verifier + **30 条手写 golden cases**（← 评测前置）+ 短期记忆 + Tier-1 能力（声优网络 / 系列时间线 / 口味画像\*）。
- **A3**：**自动 benchmark 生成 + case replay + 指标报告**（可验证多跳；Agentic Eval ≥100 case）。
- **A4**：萌娘/维基 RAG（opensearch→extracts→chunk→hybrid→rerank）+ citation + 注入/白名单/R18/来源校验 + 长期记忆。
- **A5**：自建 **MCP server + Skill cards**（把 A1 工具对外暴露）+ Trace 面板完善 + Postgres/Redis 落地。

> \*口味画像走 Track B1 的轻量推荐能力接入。

✅ A1–A5 完成 = 简历可写、可开源的完整"不训练 Agent"。

### Track B · 推荐（并行，独立 `recsys-offline/`，现在即可起步）
- **B0**：数据 & 评测脚手架 + 流行度基线（不依赖 agent，**最早可开工**）。
- **B1**：内容/tag/staff 相似 + 口味画像 → 封装为轻量 Recommender Tool，**早期接入主 agent**。
- **B2**：MF 召回（ALS/BPR/LightFM）+ **LambdaMART 重排** → 成熟后并入主系统。
- **B3**：LLM 派生特征 + 冷启动（新用户引导 / 冷启动切片指标）。

（细节见 [06-recsys](06-recsys.md) S0–S6。）

### Track C · 工程纵深（中优先）+ RL（推后）
- **C1**：用 LangGraph 重写同一 agent → 8 轴对比报告（[03 §3](03-agent-contract.md)）。
- **C2**：异步 worker + Redis 队列 + pub/sub SSE 扇出（长 agent 可恢复，配合手搓 checkpointer）。
- **C3**：filtered 混合检索成主力 → 迁 Qdrant。
- **C4（推后·需自有模型）**：**Agentic-RL**——图谱可验证多跳 QA 的 SFT 冷启动 → GRPO/DAPO 后训练（结果奖励=答案对真值，过程奖励=工具路径）。**待本地 Qwen 就绪再做**，作为后期 capstone。
- **C5（推后）**：推荐偏好 **DPO/GRPO**（用户 accept/reject 偏好对）。
- **C6**（可选）：双塔+Faiss 深召回；截图识番多模态彩头。

> 红线复述：RL 训练数据只来自 **Bangumi 图谱真值 + 公开推荐数据集**；**萌娘文本绝不进训练**（`ai-train=no`）。

## 与原线性路线的差别
只有两处：**eval 从靠后提到 A2/A3（前置）**、**推荐从"线性靠后"改为"并行早启动"**。其余全保留。

## LLM 选型（详见 [08-llm-and-config](08-llm-and-config.md)）
- 开发期 agent 大脑 = **DeepSeek API**（OpenAI 兼容接口抽象，一键可换）；
- SFT 冷启动 teacher = 强 API 模型；
- RL policy = **本地开源 Qwen（vLLM）**——API 模型无法 RL，必须开源权重。

## 当前状态
- [x] 立项、调研、方案确定
- [x] 独立仓库 + git + docs 地基
- [x] 方案迭代：自建工具/MCP、eval 前置双轨、LLM 两层选型、typed result、CoT 边界
- [x] **A1 骨架**：手搓 ReAct runner（两阶段，CoT 不外露）+ 自建 Bangumi client/7 工具（typed）+ FastAPI SSE + CLI + 最简 Next.js chat（trace 面板）
- [x] **A2**：评测前置——typed Verifier（answer/retrieval/拒答 + 工具标记泄漏）+ **17 条 golden cases** + eval runner（`--runner react|plan`）；**Plan-Execute runner**（plan→execute→reflect→补救→compose，与 ReAct 共享 `_common`、同 AgentRunner 接口可 A/B）；**短期会话记忆**（API `session_id`）+ **滑动窗口**（`trim_messages`）；新增 subject→staff 工具（8 个工具）；修 DeepSeek DSML 泄漏与过度交叉验证。**ReAct 基线 17/17**（2 single / 11 two-hop / 4 refusal）。
- [x] **Adaptive runner**：路由器按复杂度分流——简单直跑 ReAct、复杂先 plan 再 react+reflect（产品默认；纯 react/plan 保留作 A/B）。
- [x] **A3（核心）**：自动 benchmark 生成器（`otomo.eval.generate`，从 Bangumi 图谱造可验证题、真值取自 API：年份/主角声优/制作公司）→ 24 条自动 cases；eval runner 兼作 case replay + 打分卡；GoldenCase 加 `min_tools`（防纯记忆作答）。**Adaptive 在自动集 24/24**（grounding 使中日双名命中）。
- [ ] 下一步按新优先级转入 **Track B 推荐 / A4 RAG·产品能力**（三 runner 系统对比报告作为 A3 收尾可随时补）

## 下一步（A1 启动清单）
1. `backend/` 脚手架（pyproject、FastAPI 空壳、`agent/` `tools/` 目录）。
2. 手写 thin async httpx Bangumi client + 强制 UA；封 3–5 个 Bangumi 工具（每个 typed result）。
3. 钉死 `Tool/AgentState/AgentRunner` 契约（Pydantic）。
4. 手搓最小 ReAct 循环 + 一条单/两跳图谱问答打通。
5. `frontend/` 最简 Next.js chat 消费 SSE（流式答案 + 结构化事件）。
6. `.env` 放 Bangumi token 与 LLM key（gitignored）。
