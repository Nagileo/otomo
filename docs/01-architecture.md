# 01 · 系统架构

## 总览

```
┌────────────────────────────────────────────────────────────┐
│  Frontend  (Next.js + React + Tailwind + shadcn/ui)         │
│  ├─ Chat（SSE 流式，Vercel AI SDK useChat）                  │
│  ├─ Recommendation Cards（AnimeCard 网格 + "为什么推荐"）     │
│  └─ Trace Panel（plan→tool→observation→verifier 时间线）     │
└───────────────▲───────────────────────┬────────────────────┘
                │ SSE (Data Stream Protocol)                    
┌───────────────┴───────────────────────▼────────────────────┐
│  Backend  (Python + FastAPI)                                │
│  ├─ API 层：/chat（SSE）, /recommend, /trace, /auth         │
│  ├─ Agent Runtime（手搓核心；后期可切 LangGraph）            │
│  │   ReAct / Plan-Execute · Tool Registry · Verifier · Memory│
│  ├─ Tools：Bangumi / Moegirl / Wiki / Recommender / Eval     │
│  ├─ RAG：opensearch→extracts→chunk→hybrid(BM25+dense)→rerank │
│  └─ Recommender：recall(MF/CF) → rank(LambdaMART) → 解释     │
└──────┬────────────┬───────────┬──────────────┬──────────────┘
       │            │           │              │
  ┌────▼───┐  ┌─────▼────┐ ┌────▼─────┐  ┌─────▼──────┐
  │Postgres│  │  Redis   │ │ pgvector │  │ Async      │
  │+pgvector│ │ 缓存/记忆 │ │ →Qdrant  │  │ Worker     │
  │系统真值 │  │限流/队列 │ │ 向量检索 │  │ (长 agent) │
  └────────┘  └──────────┘ └──────────┘  └────────────┘
       │
  ┌────▼─────────────────────────────────────┐
  │ 外部：Bangumi API · 萌娘 api.php · 维基   │
  └──────────────────────────────────────────┘
```

## 组件职责

### Frontend（Next.js App Router）
- **为什么 Next 而非 Vite/Vue**：AI streaming 生态 React/Next 一家独大；shadcn 的 **AI Elements** 直接提供 conversation/message/reasoning/tool-call/sources 组件，三块 UI（chat/卡片/trace）几乎 1:1 映射。SSR 用于 API key 安全（route handler 代理后端、key 不落客户端）、封面图优化、可分享详情页。
- 三个核心面：流式 Chat（`useChat` 消费 SSE）、推荐卡片（流式 tool 结果 → generative UI）、**自建 Trace 面板**（无现成组件，是学习点也是"手搓 vs LangGraph"对比展示位）。

### Backend（FastAPI）
- 全程 Python，对齐训练/推理栈。SSE 走 **AI SDK Data Stream Protocol**（typed events：`text-delta`/`tool-input-available`/`finish-step`），同一事件流既驱动 chat 又喂 trace。
- 长 agent 跑（多工具 plan-execute）**不在 HTTP 请求内**完成，丢给异步 worker，worker 把 trace 事件 publish 到 Redis channel，web 层 subscribe 后经 SSE 转发。

### Agent Runtime
见 [03-agent-contract](03-agent-contract.md)。先手搓 ReAct + Plan-Execute + 两级 Verifier，钉死 `Tool/AgentState/AgentRunner` 接口；后用 LangGraph 重写同一 agent 做对比。

### 工具层
> **全部自己手搓**：client + 工具封装 + MCP server + Skill cards 都自建，只参考 Bangumi-MCP/bgm-cli 的 API 用法、不接其代码——这本身是「工」层叙事的一部分。
- **Bangumi Tool**（自建：手写 thin async httpx client + 强制 UA + 工具封装）：search/subject/character/person/collection/episode/calendar。
- **Moegirl Tool**：`opensearch`(标题解析) + `extracts`(取正文) + `info/categories`；按需取+缓存+署名。
- **Wiki Tool**：中文维基全文搜索兜底。
- **Recommender Tool**：`recommend(user_profile, filters) → ranked list`（漏斗封装为单一工具，agent 决定何时调、如何从对话设 filter）。
- **Eval Tool**：case replay + 指标计算。

### 存储（每个用途都被真实需求驱动）
| 组件 | 角色 | 触发的真实需求 |
|---|---|---|
| **Postgres** | 系统真值：番元数据、用户、收藏快照、agent run、checkpoint、trace | 单库事务一致 |
| **pgvector** | 起步向量库（番简介/萌娘 chunk embedding，<1M 向量） | 一库到底，简单 |
| **Qdrant**（后期） | filtered 混合检索成主力时迁入（"相似 X 但排除类型 Y、年份>Z"） | pgvector 过滤弱 |
| **Redis** | ①**Bangumi 响应缓存（旗舰，挡限流）** ②session/短期记忆 ③限流(入/出双向) ④异步任务队列+run 状态 ⑤sorted-set 排行榜/Top-N 候选 | 限流、非阻塞、快排名 |
| **Async Worker** | 长 agent 后台跑、可恢复（配合手搓 checkpointer） | 长任务不堵 HTTP |

**明确不碰**：Kafka / K8s 级 Milvus / 多区复制 / 微服务扇出——对本体量是假复杂度。

## 计划目录结构

```
otomo/
├─ docs/                  # 当前阶段
├─ backend/
│  ├─ otomo/
│  │  ├─ agent/           # Runtime：runner, react, plan_execute, verifier, memory, state
│  │  ├─ tools/           # bangumi, moegirl, wiki, recommender, eval
│  │  ├─ rag/             # ingest, chunk, hybrid_retrieve, rerank
│  │  ├─ recsys/          # recall(mf/cf), rank(lambdamart), features, eval
│  │  ├─ storage/         # postgres, redis, vector
│  │  ├─ api/             # fastapi routes, sse
│  │  └─ safety/          # injection 检测, 白名单, R18 门控
│  ├─ tests/
│  └─ pyproject.toml
├─ frontend/              # Next.js
│  ├─ app/  components/  lib/
│  └─ package.json
├─ recsys-offline/        # 离线推荐实验（数据集、训练、评测报告）
├─ eval/                  # 可验证多跳 benchmark 生成与运行
└─ docker-compose.yml     # postgres + redis + qdrant(后期)
```

## 数据流示例：「冬马和纱的声优还配过哪些 2013 后评分≥8 的恋爱番？」

1. Intent 解析 → 识别为多跳图谱任务（实体=角色"冬马和纱"，约束=年份/评分/标签）。
2. Plan：`search_characters("冬马和纱")` → `characters/{id}/persons`(取 CV) → `persons/{cv}/subjects` → 按 `air_date≥2013 & rating≥8 & tag~恋爱` 过滤/排序。
3. （可选）对 top 结果 `moegirl.extracts` 补剧情，挂来源链接。
4. Verifier：检索层校验召回、答案层校验事实是否落在 Bangumi 真值边上；失败触发重规划。
5. SSE 流式吐**最终答案 token + 结构化执行事件**（plan 摘要 / tool call / observation / verifier）+ 卡片；**裸 CoT 不外露、不持久化**（见 [03 §2.5](03-agent-contract.md)）；轨迹写 trace。
