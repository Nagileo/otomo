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

## 状态

🚧 文档阶段（docs-first）。代码尚未开始，先按文档定义边界与契约。

## 许可证

代码计划以 MIT 开源。**注意**：检索得到的萌娘百科内容为 CC BY-NC-SA 3.0 CN，仅按需取用、不入库、回答须挂来源链接、不用于模型训练；详见 [02-data-sources](docs/02-data-sources.md)。本项目为非商业、学习/研究用途。
