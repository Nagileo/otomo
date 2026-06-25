# Otomo（お供 · 番组搭子）

> An ACGN Knowledge-Graph Agent — 你的二次元知识图谱搭子。

基于 **Bangumi API**（结构化 ACG 知识图谱）+ **萌娘百科 / 中文维基**（非结构化设定与梗的 RAG）构建的垂直领域 Agent：
能做**多跳知识检索/问答**、**个性化推荐与追番规划**、**口味画像**，并以**可验证评测闭环**持续迭代；
工程上从**手搓 Agent 核心**起步、再与 **LangGraph** 对比，算法上从**推荐漏斗**延伸到 **Agentic-RL（SFT→GRPO/DAPO）后训练**。

## 这个项目是什么 / 为什么这么设计

四层结构，一个项目同时讲清四件事：

| 层 | 内容 | 对应能力 |
|---|---|---|
| **脊（矛）** | Bangumi 图谱上的**多跳可验证知识 Agent** + 评测闭环 → Agentic-RL 后训练 | 算法护城河 |
| **面（广度）** | 声优/staff 谱系、补番顺序、口味画像、考据问答、推荐…… | 覆盖 Agent 开发能力面 |
| **工（盾）** | 手搓 Agent →LangGraph 对比；Next.js 全栈；Postgres/Redis/Qdrant；异步 worker | 工程纵深 |
| **算（新技能）** | 推荐 recall→rank 漏斗 → 冷启动 LLM 特征 → DPO/GRPO 偏好后训练 | 推荐系统 + RL |

详见 [`docs/00-vision.md`](docs/00-vision.md)。

## 文档地图

| 文档 | 内容 |
|---|---|
| [00-vision](docs/00-vision.md) | 项目愿景、四层结构、目标用户、简历叙事 |
| [01-architecture](docs/01-architecture.md) | 系统架构、数据流、技术栈、目录结构 |
| [02-data-sources](docs/02-data-sources.md) | Bangumi / 萌娘 / 维基 / 数据集的**能力边界与许可证红线**（必读） |
| [03-agent-contract](docs/03-agent-contract.md) | Agent 接口契约、ReAct/Plan-Execute、Verifier、手搓→LangGraph 对比计划 |
| [04-capabilities](docs/04-capabilities.md) | 能力路线图 T1/T2/T3 |
| [05-evaluation](docs/05-evaluation.md) | 可验证多跳 benchmark、推荐指标、Agent 评测 |
| [06-recsys](docs/06-recsys.md) | 推荐漏斗设计与学习路线 |
| [07-roadmap](docs/07-roadmap.md) | 版本里程碑（双轨 + eval 前置） |
| [08-llm-and-config](docs/08-llm-and-config.md) | LLM 两层选型与密钥/配置规范 |

## 代码结构

```
backend/        Python + FastAPI Agent 后端（手搓 ReAct/Plan-Execute/Adaptive + 自建工具 + SSE）
frontend/       Next.js chat（消费 SSE：流式答案 / trace / sources / followups）
recsys-offline/ 离线推荐（Track B）：评测套件 + CF/MF/LTR + Bangumi 原生 CF 闭环
docs/           方案文档（地基）
```

## 快速开始

```bash
# 一次性：建专用 conda 环境（python + node 都在里面）
conda create -n otomo python=3.12 -y && conda activate otomo
conda install -c conda-forge nodejs -y

# 后端
cd backend && pip install -e ".[dev]" && cp .env.example .env   # 填 LLM_API_KEY、改 BANGUMI_USER_AGENT
python -m otomo.cli "孤独摇滚 里 後藤一里 的声优还配过哪些番？"        # 命令行直接验证
uvicorn otomo.api.app:app --reload --port 8000                  # 起 HTTP（给前端）

# 前端（同一个 otomo 环境）
cd frontend && npm install && npm run dev                       # http://localhost:3000
```

详见 [backend/README](backend/README.md) 与 [frontend/README](frontend/README.md)。

## 状态

**Agent 主体 + 推荐双轨已落地**，正按 [07-roadmap](docs/07-roadmap.md) 推进：

- **Track A（Agent 脊柱）**：手搓 ReAct / Plan-Execute / Adaptive 三种 runner（结构化 trace，不外露裸 CoT）；8 个 Bangumi 图谱工具 + 萌娘 / 中文维基 RAG + web search + 短评 / B站外链 / 口味画像；FastAPI SSE + Next.js（流式答案 / trace / sources / followups）；typed Verifier + 手写 17 + 自动生成 24 条 golden cases。
- **Track B（推荐）**：离线 S0–S2（流行度基线 → ItemCF / ALS / BPR → ALS 召回 + LambdaMART 重排）；**Bangumi 原生 CF 闭环**——自采公开收藏训 ALS（NDCG@10 +199% 超基线）→ 导出 i2i → 在线协同召回 provider，离线真正反哺在线。

**下一步**：① 图谱级 Verifier（canonical ID / 路径边 / set-F1 / 路径有效率）→ ② 产品面 + 工程纵深 → ③ Agentic-RL（capstone，待算力就绪）。

## 许可证

代码计划以 MIT 开源。**注意**：检索得到的萌娘百科内容为 CC BY-NC-SA 3.0 CN，仅按需取用、不入库、回答须挂来源链接、不用于模型训练；详见 [02-data-sources](docs/02-data-sources.md)。本项目为非商业、学习/研究用途。
