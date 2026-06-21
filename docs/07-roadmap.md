# 07 · 版本路线图

> 原则：每个版本都是一个**能 git tag、能写博客、能加简历**的里程碑——不必等全做完。守住红线：**不训练的 Agent 主体必须完整实现**，RL 作为可行后增项。

## v0.x —— 完整的"不训练 Agent"（首要交付，能投简历/能开源）

**目标**：手搓 Agent 主体跑通端到端，覆盖 Agent 开发能力面。

里程碑：
- **v0.1**：项目骨架 + Bangumi 工具（fork Bangumi-MCP）+ 手搓 ReAct 单跳问答 + FastAPI/SSE + 最简 Next.js chat。能回答单跳问题。
- **v0.2**：手搓 Plan-Execute + 两级 Verifier + Tier-1 三能力（声优网络 / 补番顺序 / 口味画像）+ 短期记忆。
- **v0.3**：萌娘/维基 RAG（opensearch→extracts→chunk→hybrid→rerank）+ 来源链接 + 注入/白名单/R18 安全 + 长期记忆。
- **v0.4**：**推荐漏斗 S0–S2**（评测脚手架+流行度基线 → MF 召回 → LambdaMART 重排）封装为 Recommender Tool + 推荐卡片 UI。
- **v0.5**：可验证多跳 benchmark 生成器 + Agentic Eval（≥100 case，回放）+ Trace 面板 + Postgres/Redis 落地。

✅ 到此 = 简历可写、可开源的完整版本。

## v1.x —— 工程纵深

- **v1.1**：用 LangGraph 重写同一 agent → 8 轴对比报告（[03 §3](03-agent-contract.md)）。
- **v1.2**：异步 worker + Redis 队列 + pub/sub SSE 扇出（长 agent 可恢复，配合手搓 checkpointer）。
- **v1.3**：filtered 混合检索成主力 → 迁 Qdrant；推荐 S3（LLM 特征+冷启动）。
- **v1.4**：Tier-2 能力补齐（考据 QA / 季番分诊 / 防剧透 / affinity）+ docker-compose 一键起。

## v2.x —— RL 后训练（可行再上 · 算法护城河）

- **v2.1**：图谱可验证多跳 QA 的 **SFT 冷启动**（轨迹回收+分级筛选）。
- **v2.2**：**GRPO/DAPO** 后训练（结果奖励=答案对真值，过程奖励=工具路径），报成功率提升曲线。
- **v2.3**：推荐偏好 **DPO/GRPO**（用户 accept/reject 偏好对），报个性化 NDCG 提升。
- **v2.4**（可选）：双塔+Faiss 深召回；截图识番多模态彩头。

> 红线复述：RL 训练数据只来自 **Bangumi 图谱真值 + 公开推荐数据集**；**萌娘文本绝不进训练**（`ai-train=no`）。

## 当前状态

- [x] 立项、调研、方案确定
- [x] 独立仓库 + git + docs 地基
- [ ] v0.1 骨架 ← **下一步**

## 下一步（v0.1 启动清单）

1. `backend/` 脚手架（pyproject、FastAPI 空壳、`agent/` `tools/` 目录）。
2. fork/接入 Bangumi-MCP 的 client 层，封 3–5 个 Bangumi 工具，设好强制 UA。
3. 钉死 `Tool/AgentState/AgentRunner` 契约（Pydantic）。
4. 手搓最小 ReAct 循环 + 一条单跳问答打通。
5. `frontend/` 最简 Next.js chat 消费 SSE。
