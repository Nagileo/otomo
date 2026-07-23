# Otomo（お供 · 番组搭子）

一个基于 Bangumi 的 ACGN 知识图谱 Agent——你的二次元搭子。

**🌐 Live Demo:** <https://8-217-237-110.nip.io>（演示环境，免登录即可试；绑定 Bangumi 后解锁个人化）
**🤖 也是一个 Discord Bot:** 在服务器里 @它、私信它、或用 `/推荐` `/评价` `/绑定` 斜杠命令。

它把 Bangumi 的结构化条目 / 人物 / 角色 / 关系图谱，配上萌娘百科、中文维基、批判空间、VNDB、netaba.re 等设定与口碑来源，
组织成一个能多跳问答、个性化推荐、评价融合、新番导视、防剧透的对话 agent。
核心想法很简单：**对每条信息都标注「来源」和「置信度」**，而不是把一堆链接甩给你——
事实走 Bangumi 图谱，口碑标清是哪个圈层，推断性的偏好标成低置信。

`103 个自建工具 · 163 单元测试 · 47 条 golden 行为验收 · 30 用户离线推荐评测集`

## 它能做什么

- **图谱问答**：声优 / 制作 / 角色 / 年份 / 跨媒体关系的多跳查询（如"白色相簿2 里冬马和纱的声优还配过哪些高分番"）。
- **个性化推荐**：收藏画像（带时间衰减）+ aspect 好球区/雷区 + 协同召回 + bge 语义特征 + 好友圈信号，多路召回统一打分；对话式修正（"换一批 / 要短的 / 更冷门"）。
- **评价融合**：Bangumi / 批判空间 / VNDB / AniList 多源评分规整成共识 / 分歧 / 置信度；galgame 给三圈层对比；netaba.re 口碑走势（30/90 天涨跌、争议度、开播前期待）。
- **新番导视**：季番表 + yuc 放送档期 + 热度融合（在看数/trending/分集讨论）+ 播前期待，按你的口味分诊。
- **追番闭环**：放送日历 → 分集进度打卡（"看完第 8 集了"一句话补标）→ 分集爆点雷达（你追的番哪集讨论量暴涨）→ 收藏写回（口头"确认"即写 Bangumi，可撤销）。
- **长期记忆**：偏好 / 避雷 / 进度 / 反馈跨会话生效，每条带来源和置信度，可随时遗忘。
- **防剧透**：识别"看到第 N 集 / 别剧透"，分集讨论按进度过滤。
- **看番通路**：在哪看（bangumi-data + yuc + **B站番剧库实时查证**，查到给 ss 直达、查不到明确说无版权）→ 离线 RSS/磁力/BD 聚合（蜜柑/DMHY/ACGNX/VCB，只给链接不碰下载）→ qBittorrent 推送（确认制）。
- **社交与好友**：口味同步率（隐藏分 + 贝叶斯收缩排名）、共同追新、好友圈聚合（都在追什么）、好友动态订阅、社交召回进推荐。
- **产品面板**：追番驾驶舱、作品档案、IP 图谱、月度/年度报告（**Wrapped 卡片可导出分享**）、圣地巡礼地图与行程、收藏仪表盘、收藏导出 CSV。
- **识图与视频**：截图识番（trace.moe + SauceNAO + OCR/VLM 路由）、B站视频内容总结、Pixiv 插画入口。
- **主动推送**：每日追番 / 生日 / 口碑哨兵（你的番评分异动）/ 分集爆点 / 好友动态 / RSS 更新 / 周报 → 站内 / 邮件（HTML 卡片）/ **Discord 私信** / Telegram / 飞书 / Server酱。
- **轻互动**：今日番签（确定性抽签）、ACGN 小测验（前端判分不剧透）、30 秒免登录口味速配、今日角色生日。

## 怎么用

| 入口 | 说明 |
|---|---|
| **网页** | Live demo 直接开聊；生成中实时显示"它在做什么"，答案带结构化资料卡片 |
| **手机 (PWA)** | 浏览器打开后"添加到主屏幕"，即是一个 App |
| **Discord** | @机器人 / 私信 / 斜杠命令；`/绑定` 关联 Bangumi 后个性化；结果渲染成 embed 卡片；订阅可推送到你的私信 |
| **MCP** | 31 个只读工具接进 Claude Desktop / Cursor（见下文） |

## 为什么这么做

通用大模型能聊 ACGN，但拿不到你的 Bangumi 收藏、也不会标来源。
Otomo 想做的是一个**垂直、可溯源**的搭子：事实尽量落到 canonical 图谱、口碑标清圈层、推断标低置信、剧透要先确认。
同时这个项目也是作者实践 Agent 工程（手搓 runner / 工具 / 事件协议）、推荐系统、以及后续后训练的载体。

## 代码结构

```
backend/        Python + FastAPI agent 后端
                手搓 ReAct / Plan-Execute / Adaptive 三种 runner（+ LangGraph 对照实现），
                103 个自建工具 + 渐进式工具披露，SSE 流式输出；
                Discord bot 入口（otomo/discord_bot.py）；MCP Server（31 只读工具）
frontend/       Next.js 对话前端：SSE 流式 + 执行过程可视化 + 结构化证据面板 + PWA
recsys-offline/ 离线推荐：评测套件 + CF / MF / LTR，Bangumi 原生 CF 闭环（导出 i2i 反哺在线召回）
deploy/         Caddyfile + 生产 env 模板；根目录 docker-compose(.prod) + deploy.sh 一键更新
docs/           方案与设计文档
```

## 快速开始（本地）

```bash
conda create -n otomo python=3.12 -y && conda activate otomo
conda install -c conda-forge nodejs -y

# 后端
cd backend && pip install -e ".[dev]"
cp .env.example .env          # 填 LLM_API_KEY，改 BANGUMI_USER_AGENT；带 BANGUMI_TOKEN 可解锁个人数据
python -m otomo.cli "孤独摇滚 里 後藤一里 的声优还配过哪些番？"
uvicorn otomo.api.app:app --reload --port 8000

# 前端
cd frontend && npm install && npm run dev    # http://localhost:3000
```

可选 extras：`.[rag]`（bge 混合检索/语义推荐，缺则词法降级）、`.[discord]`（Discord bot）、`.[mcp]`、`.[langgraph]`。

## 部署（生产）

免域名方案：`<公网IP>.nip.io` + Caddy 自动 Let's Encrypt。CI 构建镜像推 ghcr，服务器只拉不 build：

```bash
git clone https://github.com/Nagileo/otomo && cd otomo
cp deploy/production.env.example backend/.env   # 填密钥；OTOMO_DOMAIN 会从 FRONTEND_BASE_URL 派生
bash deploy.sh    # 校验配置 → 拉镜像 → 起服务(backend/scheduler/frontend/caddy[/discord]) → 健康检查
```

推送流水线：push → GitHub Actions 跑测试 → **测试通过才**构建镜像发布 ghcr（坏提交永远进不了 `latest`）→ 服务器 `bash deploy.sh` 秒级更新。

## 技术选型（简述）

- **Agent**：手搓 ReAct / Plan-Execute / Adaptive 三 runner，输出结构化事件流（plan / tool_call / observation / state / answer），不外露裸 CoT；LangGraph 对照实现共用同一套工具契约。
- **工具披露**：103 工具不全量塞给模型——23 核心常驻 + 15 域工具组按查询词法注入 + `load_tool_group` 逃生舱，单轮 schema ~26k → ~7k token（-70%），详见 [docs/21](docs/21-architecture-evolution-notes.md)。
- **安全模型**：多租户隔离（会话身份是唯一授权边界，工具参数是模型输出、绝不信任；`tenant_scope` 装在 HTTP/Discord/调度器全部路径）+ 写操作从用户原始输入推导授权 + webhook SSRF 防护（DNS 解析后逐 IP 校验）+ 订阅凭证只读投影脱敏。
- **写回信任模型**：真实写操作三层护栏，口头确认即执行、默认通道仍拦截、可撤销。
- **推荐进化链**：手调权重 → 随机搜索（57 样本证实默认已近局部最优）→ LTR 学习排序管线（LogisticRegression，CV AUC 0.799）；bge 语义重排 +7.5% NDCG（消融验证后上线），语义召回索引（1004 部）建好待大样本验证——**每一步有离线指标，不显著就不上**。
- **评测**：163 单元测试 + 47 golden 行为验收（断言路由/面板/越界，不赌易变事实）+ leave-N-out 推荐评测（30 公开用户）+ 图谱级 verifier（canonical 对齐/set-F1/幻觉感知）+ LLM 用户模拟器（5 persona 多轮驱动）。
- **数据飞轮**：每轮对话轨迹 + 👍👎 反馈落盘（伪匿名），一键导出脱敏 SFT / DPO 语料，为后训练攒真实分布数据。
- **记忆**：短期会话状态 + 文件式长期记忆（consolidation 而非追加；多实例三路合并防并发丢写）。
- **RAG**：BM25 + bge dense 混合召回 + reranker 精排（线程池执行不阻塞事件循环）；萌娘 `ai-train=no` 只临时检索、不入库。
- **前端**：Next.js + SSE；生成中"看它思考"（实时步骤 + 秒表，完成后收敛可回看）；`[[panel:x]]` 锚定把证据卡片嵌进答案对应位置（后端缺锚自动兜底注入）。

## 文档地图

| 文档 | 内容 |
|---|---|
| [00-vision](docs/00-vision.md) | 项目愿景与目标用户 |
| [01-architecture](docs/01-architecture.md) | 系统架构、数据流、目录结构 |
| [02-data-sources](docs/02-data-sources.md) | 各数据源的能力边界与许可证红线（必读） |
| [03-agent-contract](docs/03-agent-contract.md) | Agent 接口契约、runner、Verifier、手搓 vs LangGraph |
| [05-evaluation](docs/05-evaluation.md) | 可验证多跳 benchmark 与评测 |
| [06-recsys](docs/06-recsys.md) | 推荐漏斗设计 |
| [17-feature-and-deploy-roadmap](docs/17-feature-and-deploy-roadmap.md) | 功能/部署路线 |
| [18-watch-pipeline](docs/18-watch-pipeline.md) | 看番通路（在线×离线）设计 |
| [21-architecture-evolution-notes](docs/21-architecture-evolution-notes.md) | 架构演进实录（披露经济学/信任模型/评测闭环/踩坑） |

## MCP Server

把 Otomo 的 ACGN 知识工具接进任何 MCP 客户端（Claude Desktop / Claude Code / Cursor…）——
31 个只读工具：图谱检索、评价融合、季度导视、在哪看、资源 RSS、圣地巡礼、OP/ED、生日、IP 图谱、梗考据。

```bash
cd backend && pip install -e ".[mcp]"
```

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

## 数据来源与红线

- **Bangumi**：官方 v0 API，强制合规 User-Agent；多用户 OAuth，每个用户用自己的授权。
- **萌娘百科**：内容为 **CC BY-NC-SA 3.0 CN**——仅按需检索、**不入库、不用于训练**，回答必须挂来源链接。
- 其他外部源（批判空间 / VNDB / AniList / yuc / B站 / netaba.re / anitabi）只读公开数据、标注来源，不持久化受限语料。
- 详见 [02-data-sources](docs/02-data-sources.md)。

## 许可证

代码以 **MIT** 开源（见 [LICENSE](LICENSE)）。非商业、学习 / 研究用途；检索到的第三方内容遵循其各自许可证（见上文红线）。
