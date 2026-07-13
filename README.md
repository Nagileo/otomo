# Otomo（お供 · 番组搭子）

一个基于 Bangumi 的 ACGN 知识图谱 Agent——你的二次元搭子。

它把 Bangumi 的结构化条目 / 人物 / 角色 / 关系图谱，配上萌娘百科、中文维基、批判空间、VNDB 等设定与口碑来源，
组织成一个能多跳问答、个性化推荐、评价融合、新番导视、防剧透的对话 agent。
核心想法很简单：**对每条信息都标注「来源」和「置信度」**，而不是把一堆链接甩给你——
事实走 Bangumi 图谱，口碑标清是哪个圈层，推断性的偏好标成低置信。

## 它能做什么

- **图谱问答**：声优 / 制作 / 角色 / 年份 / 跨媒体关系的多跳查询（如"白色相簿2 里冬马和纱的声优还配过哪些高分番"）。
- **个性化推荐**：基于 Bangumi 收藏画像 + 从私评抽取的 aspect 情感「好球区 / 雷区」+ 长期记忆，多路召回打分；支持对话式修正（"换一批 / 要短的 / 更冷门 / 别这题材"）。
- **评价融合**：把 Bangumi / 批判空间 / VNDB / AniList 等多源评分规整成共识 / 分歧 / 置信度；galgame 给中文圈 / 日本 gal 圈 / 国际 VN 圈三圈层对比。
- **新番导视**：Bangumi 季番 + yuc 放送表 + B站白名单导视，按你的口味分诊（必追 / 可等 / 不适合）。
- **长期记忆**：记住你的偏好 / 避雷 / 观看进度 / 推荐反馈，跨会话生效；每条带来源和置信度，可随时遗忘。
- **防剧透**：识别"看到第 N 集 / 别剧透 / 可以剧透"，分集讨论按进度过滤。
- **看番通路**：在哪看（bangumi-data + yuc 正版入口）→ 离线资源（蜜柑 / DMHY / ACGNX / VCB 的 RSS·磁力·BD 聚合，只给链接不碰下载）→ 一键推送 qBittorrent（前端确认制）。
- **收藏写回**：对话里说"加入在看 / 打 8 分 / 看完了"，生成待确认动作，**口头说"确认"即写回 Bangumi**（可撤销）。
- **产品面板**：追番驾驶舱、作品档案页、IP 跨媒介图谱、月度 / 年度观看报告（**年度 Wrapped 卡片可导出成图分享**）、圣地巡礼地图与行程规划、今日角色生日。
- **识图与视频**：截图识番（trace.moe + SauceNAO + OCR/VLM 聚合路由）、B站导视视频内容总结（字幕 / 弹幕 / 评论 / ASR）、Pixiv 插画入口。
- **分享与订阅**：报告 / 导视 / 驾驶舱一键生成可公开分享页（递归脱敏）；每日追番 / 生日 / RSS 更新 / 周报按规则推送到 inbox / 邮件 / Telegram / Discord / 飞书 / Server酱。
- **免登录冷启动**：30 秒口味速配（题材 × 雷点 × 场景），不绑 Bangumi 也能拿到个性化推荐。
- **探索与发现**：角色声优网络漫游、口味报告人格卡、评分预测、萌点（标签组合）检索、分集口碑雷达、好友同步率、弃坑分析、追番副驾。

## 为什么这么做

通用大模型（豆包 / GPT 等）能聊 ACGN，但拿不到你的 Bangumi 收藏、也不会标来源。
Otomo 想做的是一个**垂直、可溯源**的搭子：事实尽量落到 canonical 图谱、口碑标清圈层、推断标低置信、剧透要先确认。
同时这个项目也是作者实践 Agent 工程（手搓 runner / 工具 / 事件协议）、推荐系统、以及后续后训练的载体。

## 代码结构

```
backend/        Python + FastAPI agent 后端
                手搓 ReAct / Plan-Execute / Adaptive 三种 runner（+ LangGraph 对照实现），
                96+ 自建工具（图谱 / 推荐 / 评价 / 资源 / 巡礼 / 记忆…）+ 渐进式工具披露，
                SSE 流式输出；另可作为 MCP Server 把 31 个只读工具接进 Claude / Cursor
frontend/       Next.js 对话前端：消费 SSE，渲染流式答案 / 执行 trace / 结构化证据面板 / 来源卡片
recsys-offline/ 离线推荐：评测套件 + CF / MF / LTR，Bangumi 原生 CF 闭环（导出 i2i 反哺在线召回）
docs/           方案与设计文档
```

## 快速开始

```bash
# 建专用 conda 环境（python + node 都装在里面）
conda create -n otomo python=3.12 -y && conda activate otomo
conda install -c conda-forge nodejs -y

# 后端
cd backend && pip install -e ".[dev]"
cp .env.example .env          # 填 LLM_API_KEY，改 BANGUMI_USER_AGENT；带 BANGUMI_TOKEN 可解锁个人数据
python -m otomo.cli "孤独摇滚 里 後藤一里 的声优还配过哪些番？"   # 命令行直接验证
uvicorn otomo.api.app:app --reload --port 8000                  # 起 HTTP（给前端用）

# 前端（同一个 otomo 环境）
cd frontend && npm install && npm run dev                       # http://localhost:3000
```

可选：RAG 第二刀（`pip install -e ".[rag]"`，缺则自动降级到词法检索）、LangGraph 对照（`".[langgraph]"`）。

## 技术选型（简述）

- **Agent**：手搓 ReAct / Plan-Execute / Adaptive 三 runner，输出结构化事件流（plan / tool_call / observation / state / answer），不外露裸 CoT；另有 LangGraph 对照实现共用同一套工具与契约。事件协议借鉴 AG-UI 的分类。
- **工具**：全部自建（不接 Bangumi-MCP），typed 入参 / 出参；外部源（批判空间 / yuc / B站 / 好友页）带 TTL 缓存与失败降级。
- **工具披露**：96 工具不全量塞给模型——23 个核心常驻 + 15 个域工具组按查询词法注入 + `load_tool_group` 逃生舱，单轮工具 schema 从 ~26k token 降到 ~7k（-70%），详见 [docs/21](docs/21-architecture-evolution-notes.md)。
- **写回信任模型**：真实写操作三层护栏（confirmed 参数 / 仅执行已准备动作 / prompt 明确确认规则），对话内口头确认即执行、默认通道仍拦截。
- **评测**：136 单元测试 + 47 条 golden 行为验收（断言路由 / 面板 / 越界，不赌易变事实，CI 手动触发）+ 离线推荐 leave-N-out 评测（HR@K/NDCG@K 量化各召回通道贡献）。
- **数据飞轮**：部署期每轮对话轨迹 + 👍👎 反馈落盘（伪匿名），一键导出脱敏 SFT / DPO 语料，为后训练阶段攒真实分布数据。
- **记忆**：短期会话状态（剧透 / 上轮推荐）+ 文件式长期记忆（结构化偏好，写入走 consolidation 而非追加）。
- **RAG**：BM25 + bge dense 混合召回 + reranker 精排；萌娘 `ai-train=no` 的内容只临时检索、不入库。
- **评测**：图谱级 verifier（canonical 实体对齐 / set-F1 / 路径有效率 / 幻觉感知）+ golden cases（含"该调哪些工具 / 不该调 / 结构化面板是否产出"维度）。
- **前端**：Next.js + SSE，按工具名渲染对应证据面板（评价矩阵 / 同步率 / 季番卡 / 推荐解释 / 记忆 / 探索网络…）。

## 文档地图

| 文档 | 内容 |
|---|---|
| [00-vision](docs/00-vision.md) | 项目愿景与目标用户 |
| [01-architecture](docs/01-architecture.md) | 系统架构、数据流、目录结构 |
| [02-data-sources](docs/02-data-sources.md) | 各数据源的能力边界与许可证红线（必读） |
| [03-agent-contract](docs/03-agent-contract.md) | Agent 接口契约、runner、Verifier、手搓 vs LangGraph |
| [05-evaluation](docs/05-evaluation.md) | 可验证多跳 benchmark 与评测 |
| [06-recsys](docs/06-recsys.md) | 推荐漏斗设计 |
| [10-source-router-and-episodes](docs/10-source-router-and-episodes.md) | 信息源路由与分集维度 |
| [11-roadmap-2026h2](docs/11-roadmap-2026h2.md) | 当前主线路线（含已完成 Phase 1–6 与后续） |

## 数据来源与红线

- **Bangumi**：走官方 v0 API，强制合规 User-Agent；读接口免 token，带 token 解锁个人收藏 / R18。
- **萌娘百科**：内容为 **CC BY-NC-SA 3.0 CN**——仅按需检索、**不入库、不用于训练**，回答必须挂来源链接。
- 其他外部源（批判空间 / VNDB / AniList / yuc / B站）只读公开数据、标注来源，不持久化受限语料。
- 详见 [02-data-sources](docs/02-data-sources.md)。

## 许可证

代码以 **MIT** 开源（见 [LICENSE](LICENSE)）。非商业、学习 / 研究用途；检索到的第三方内容遵循其各自许可证（见上文红线）。


## MCP Server

把 Otomo 的 ACGN 知识工具接进任何 MCP 客户端（Claude Desktop / Claude Code / Cursor…）——
31 个只读工具：图谱检索、评价融合、季度导视、在哪看、资源 RSS、圣地巡礼、OP/ED、生日、IP 图谱、梗考据。

```bash
cd backend && pip install -e ".[mcp]"
```

Claude Desktop 配置（`claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "otomo": {
      "command": "python",
      "args": ["-m", "otomo.mcp_server"],
      "cwd": "/path/to/otomo/backend"
    }
  }
}
```

只暴露公共知识面：用户态（长期记忆、收藏写回、个性化画像）不经 MCP 暴露。
