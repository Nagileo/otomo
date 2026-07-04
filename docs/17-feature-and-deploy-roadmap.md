# 17 · 功能收口与部署路线（训练前的最后一轮功能面建设）

> 来源：2026-07-04 全库核验（逐项 grep/读码确认，非臆测）+ 联网补充技术细节。
> 决策背景：**RL 数据侧暂缓**——rejection sampling 规模化 / 用户模拟器 / DPO 导出等 docs/15 C 线内容，等换真 policy（qwen3.5 级开源模型 + A100）时再启动，届时 API 也要换，现在做数据生产收益打折。**当前主线 = 功能面 + 体验面 + 部署面收口**，把 Otomo 从"能力齐全的 demo"推到"真正可日常使用、可公开访问的 ACGN 生活助手"。
> 承接 docs/15（A/B/D/E 线大部分已落地）、docs/16（多模态一二波已落地）。本文编号自成体系，与 docs/15/16 的字母线无关。

---

## 0. 现状快照（2026-07-04 核验，全部读码确认）

**已落地**（远超 docs/11 的 0.3 快照）：Phase 1–6 全闭环；Bangumi 写回（强制确认 + decision_log + 可撤销）；计划板/watch_plan/recommendation_lists；claim verifier（obs 层自动跑 + auto revision + ClaimCheckPanel）；轨迹采集；rejection sampling 管线（雏形）；周报（asyncio 常驻 scheduler + inbox/webhook/email 三通道 + SMTP config）；收藏仪表盘（前后端）；OAuth 多用户（sqlite + Fernet + CSRF）；31 个 golden cases；trace.moe 双路识番 / SauceNAO 溯源 / OCR / 画风推荐 / 视频抽帧 / 多图上传；B站公开字幕读取；人格卡 PNG 导出；TracePanel 分级（summary/full）。

**确认缺口（本文范围，13 项）**：

| # | 缺口 | 核验证据 | 落位 |
|---|---|---|---|
| 1 | 每日放送日历 + 追番对齐 | 全库无 `/calendar` 调用；`WatchCopilotItem` 无放送时间字段 | F1 |
| 2 | pixiv 数据能力 | 只有 tag 导航链接 + SauceNAO 外链，无 API 读取 | F2 |
| 3 | B站视频 ASR（无公开字幕时） | `get_bilibili_video_subtitles` 无字幕即失败，无 ASR 兜底 | F3 |
| 4 | 推荐卡一键写回 | RecommendPanel 无"加入想看"入口，需打字发起 | F4 |
| 5 | 会话持久化 | 前端 `sessionId` 是 `useRef`（刷新丢）；后端 `app.state.sessions` 内存 dict（重启丢）；无会话列表 | S1 |
| 6 | 跨轮 claim 证据池 | `obs.py` 的 observations 池每轮清零，第二轮引用第一轮事实会被误判"无据" | S2 |
| 7 | 重工具全串行 | recommend/review/season/user_analysis 无一处 `gather`/`Semaphore` | X1 |
| 8 | 长工具无进度反馈 | recommend 十几秒内部黑盒 | X2 |
| 9 | 容器化 + CI | 无 Dockerfile/compose/Actions，README 只有 `--reload` 开发姿势 | D1 |
| 10 | 生产运行形态未定 | scheduler 随 API 进程起（每 worker 一份）；LTM 文件写无锁 | D2 |
| 11 | 限流/成本护栏 | 全库无 rate limit；公网可被刷爆 LLM 账单 | D3 |
| 12 | 生产配置与备份 | cookie_secure=False 等 config 已预留未执行；无备份方案 | D4 |
| 13 | 周报推送最后一公里 | 代码全备，但 `_send_webhook` 发 generic JSON，与 Server酱/Telegram 参数格式不兼容 | D5 |

---

## 1. 工作线总览

```
F 线 · ACGN 功能    F1 放送日历+追番对齐 · F2 pixiv · F3 B站 ASR · F4 一键写回
S 线 · 会话与验证    S1 会话持久化 · S2 跨轮 claim 证据池
X 线 · 体验/性能    X1 并发化 · X2 工具进度事件
D 线 · 部署        D1 Docker/CI · D2 运行形态 · D3 限流护栏 · D4 生产清单+备份 · D5 推送落地
```

**三波节奏**（先"别人用五分钟能感知的"，再"上云前置"，最后"深化"）：

- **第一波（功能核心 + 体验）**：F1 → X1 → S1。
- **第二波（上云）**：D1 → D2 → D3 → D4 → D5（D5 依赖 F1 才有"当期新番状态"的内容）。
- **第三波（深化）**：F3 → S2 → X2 → F4 → F2（F2 有网络前置，见详述）。

---

## 2. F 线 · ACGN 功能补全

### F1 · 每日放送日历 + 追番对齐（最大的 ACGN 功能缺口）

**目标**：回答 ACGN 助手最高频的一类问题——"**今天/本周有什么番更新**""我在看的番周几更新、已经播到第几集、我落后几集"。并让周报从"收藏队列复读"升级为真正的"当期新番状态"。

**API 依据（2026-07-04 实测）**：
- `GET https://api.bgm.tv/calendar`（免 token，带 UA 即可）返回按星期分组的当季放送表：
  ```json
  [{"weekday": {"en": "Mon", "cn": "星期一", "id": 1},
    "items": [{"id": 456080, "name_cn": "...", "air_date": "2026-07-06",
               "air_weekday": 1, "rating": {...}, "images": {...},
               "collection": {"doing": 425}}]}]
  ```
- `GET /v0/episodes?subject_id=X&type=0` 返回每集 `airdate/name/name_cn/duration`（已实测），`airdate <= today` 的最大 sort 即"已播到第 N 集"。
- 用户侧：在看列表的 `ep_status`（已有收藏工具在用）即"用户看到第 N 集"。

**做法**：
1. 新建 `backend/otomo/tools/calendar/tool.py`，两个工具：
   - `get_broadcast_calendar(day: "today"|"week", only_mine: bool)`：调 `/calendar`（走 `client._TTLCache`，TTL 可到 6h）→ `only_mine=True` 时与用户在看/想看收藏 join，命中的条目标 `in_my_collection: "watching"|"wishlist"` → 按 weekday 输出，今天置顶。
   - `get_airing_progress(username?)`：在看列表逐部（**用 X1 的并发原语**）拉 episodes → 计算 `{aired_ep, my_ep(=ep_status), behind(=aired-my), next_air_date}` → 输出"落后榜"排序。
2. 周报接入：`WeeklyDigestTool` 增加一个 section「本周放送」——在看/想看 × calendar 的 `air_weekday`，想看列表中 `air_date` 落在本周的标「**本周开播**」并写 inbox 提醒（复用现有 `_digest_inbox_item` 流）。
3. 前端 `BroadcastCalendarPanel`：按星期分组的封面 grid，今天高亮，"我在追"角标，卡片点开进 subject；`AiringProgressPanel`：落后集数条形列表 +「继续看第 N 集」提示。
4. system prompt 的工具导航区补一句路由提示（"今天/本周更新→get_broadcast_calendar；落后进度→get_airing_progress"）。

**验收**：`今天有什么番更新？`（应调 calendar 不应 web_search）；`我在追的番这周哪几天更新？我落后几集了？`（应出 AiringProgressPanel，数字与 Bangumi 页面一致）。
**新增 golden cases**：`calendar_today`、`airing_progress_behind`、`wishlist_premiere_alert`（想看开播提醒进 inbox）。
**坑**：calendar 只覆盖"当季在播"，完结番不在里面（fallback 到 episodes airdate）；`air_weekday` 以日本放送日为准，跨零点档（如周四 25:00 = 周五凌晨）可能与国内平台上架日差一天——UI 标注"日本放送时间"，不强断言 B站上架时间。

### F2 · pixiv 数据能力（从导航壳到真数据）

**目标**：溯源命中后能答"这个画师还画过什么/画过你喜欢的谁"；每日/每周插画排行榜；按作品/角色 tag 的插画发现。补齐 docs/16 S2。

**技术现状（2026-07 联网确认）**：pixiv 无公开官方 API；社区标准是 [pixivpy](https://github.com/upbit/pixivpy)（及异步分支 [PixivPy-Async](https://pypi.org/project/PixivPy-Async/)），**refresh_token 是唯一登录方式**（密码登录 2021 年已废弃），token 获取用 [gppt](https://pypi.org/project/gppt/)（4.3.0，活跃维护）或 ZipFile 的 OAuth 手动流。refresh_token 长期有效，每小时换 access_token。

**⚠ 两个硬前提，决定它排第三波**：
1. **网络**：pixiv 在中国大陆网络（含阿里云国内节点）直连不可达。三种形态择一：仅本地 demo 开启（config 开关，服务器上关闭）/ 服务器挂代理出口 / 部署海外节点。**建议先做成 `pixiv_enabled` 开关能力**，本地演示用，公网版默认关。
2. **账号安全**：取 token 的工具均为第三方，建议用小号；token 放 `.env`（`pixiv_refresh_token`），绝不入库。

**做法**：
1. config 增 `pixiv_enabled: bool = False`、`pixiv_refresh_token: str = ""`、`pixiv_proxy: str = ""`。
2. 新建 `backend/otomo/tools/pixiv/tool.py`（PixivPy-Async，贴项目 async 架构），三个工具：
   - `get_pixiv_ranking(mode: day|week|month, limit)`：排行榜，输出 `{pid, title, artist, tags, thumb_url}`。
   - `get_artist_portfolio(artist_id | artist_name)`：画师代表作 + 常画 tag → tag 回锚 Bangumi character/subject search（"常画 C102 的角色"）。与 `search_image_source` 串联：SauceNAO 溯源命中 pixiv → 直接接画师卡。
   - `discover_illustrations(query)`：作品/角色中文名 → 罗马字/日文别名（图谱 infobox 已有别名数据）→ tag 搜索。
3. **合规红线**（docs/16 §7 的实例化）：`sanity_level`/`x_restrict` 过滤 R18（默认硬过滤，不做开关）；**只存 PID/标题/画师名/缩略 URL，不下载不转存原图**；结果标 `source="pixiv"`（docs/16 已预留该 source 类型）+ 限频（Semaphore(2)）+ `_cache` TTL 6h。
4. 前端 `PixivPanel`：缩略图 grid（`referrerPolicy="no-referrer"` 处理 pixiv 图床防盗链；若仍 403 则走 `i.pixiv.cat` 反代或只展示文字卡+外链，实现时二选一验证）。

**验收**：给一张同人图 → SauceNAO 溯源 → 画师卡（代表作/常画角色回锚 Bangumi）；`最近一周 pixiv 上什么插画火？` → 排行榜面板。
**新增 golden cases**：`pixiv_artist_trace`（依赖网络，标记 external）、`pixiv_ranking`。

### F3 · B站视频 ASR 兜底（补完视频内容理解的最后一级）

**目标**：B站大多数导视/漫评视频**没有公开字幕**，当前链路（公开字幕 → 抽帧 OCR → 评论/标题）读不到 UP 主口播。补 ASR 后形成四级降级：**公开字幕 → ASR 转写 → 抽帧 OCR/VLM → 评论/标题**。

**方案对比（联网确认）**：

| 方案 | 成本 | 关键约束 | 适用 |
|---|---|---|---|
| A · 本地 faster-whisper | 免费 | CPU 上 small/int8 实时率约 1~2×，30min 视频转 15~30min；模型走 modelscope 下载（项目惯例） | 本地 demo / 服务器 CPU 富余 |
| B · 阿里云百炼 Paraformer/Fun-ASR | ¥0.288/小时音频（仅按语音时长计费） | **只接受公网可访问 URL，不支持本地文件/二进制**（[官方文档](https://help.aliyun.com/zh/model-studio/recording-file-recognition)）→ B站 CDN 音频 URL 带防盗链不能直喂，必须先中转 OSS；仅北京地域 | 部署阿里云后（内网传 OSS 快，且已有百炼 key） |

**做法**（两方案共用管道，config 选型 `asr_provider: local|dashscope|off`）：
1. 音频获取：`yt-dlp -f bestaudio -x --audio-format m4a "https://www.bilibili.com/video/{bvid}"`（yt-dlp 对 B站支持良好，新版已自带 Referer 处理；ffmpeg 需装机）→ 临时文件。
2. 转写：
   - A：`faster_whisper.WhisperModel("small", compute_type="int8")` → segments（自带时间戳）。
   - B：临时文件 → `ossutil cp` 到私有 bucket（带过期生命周期）→ 签发临时公网 URL → `dashscope.audio.asr.Transcription.async_call(model="paraformer-v2", file_urls=[url])` → wait → 拉 `transcription_url` JSON → 删 OSS 对象。
3. 接入点：`get_bilibili_video_subtitles` 的无字幕分支（现在直接返回失败）→ 改为按 `asr_provider` 走 ASR 管道，产出**与公开字幕相同的 `subtitle_segments` 结构**（下游摘要/RAG 零改动），`source` 标 `bili_asr`。
4. 防成本爆炸：视频时长 > 30min 拒绝并提示（metadata 里有 duration）；BV 级缓存转写文本（`_cache`，TTL 7 天）；转写后即删音频文件（**不持久化音频**，只留文本——合规红线）。

**验收**：挑一个无公开字幕的导视视频，`这个视频里 UP 主推了哪几部？`能给出基于口播内容的摘要，来源标 `bili_asr`（话语源）。
**新增 golden case**：`bili_asr_digest`（标记 external + slow）。

### F4 · 推荐卡一键写回（小而顺手）

**目标**：写回确认流已完整（pending action + MemoryPanel「确认写回」），但推荐卡上没有入口，用户要打字"帮我把 X 加入想看"。补一个直达按钮，写回体验闭环。

**做法**：后端加轻端点 `POST /actions/prepare-write {subject_id, subject_name, collection_type}`（复用 writeback tool 的 pending 生成逻辑，带 CSRF）→ RecommendPanel / SeasonGuidePanel / BroadcastCalendarPanel 每张卡加「➕想看」按钮 → 点击生成 pending_write_action → 现有确认流接管（二次确认原则不变，**按钮只 prepare 不执行**）。
**验收**：推荐面板点「➕想看」→ MemoryPanel 出现待确认动作 → 确认后 Bangumi 收藏真实变更 + decision_log 留痕。

---

## 3. S 线 · 会话与验证

### S1 · 会话持久化 + 多会话管理（"demo"与"产品"的分水岭）

**目标**："昨天聊到哪了，继续"。刷新不丢、重启不丢、可切换历史会话。

**现状**：前端 `sessionId = useRef("")`（[page.tsx:275](../frontend/app/page.tsx)）刷新即重置；后端 `app.state.sessions: dict[str, AgentState]`（[app.py:48](../backend/otomo/api/app.py)）重启即清空；无会话列表 UI。

**做法**：
1. 后端新建 `otomo/session_store.py`：sqlite（`cache/sessions.sqlite3`，复用 auth 的 sqlite 模式）。表 `sessions(id, auth_session_id, title, created_at, updated_at)` + `messages(session_id, role, content, attachments_json, ts)`。`AgentState` 的短期状态（spoiler_mode/progress_episode/claim 证据池）序列化进 sessions 行的 `state_json`。
2. 运行时策略：内存 dict 作为 LRU 热缓存（现状不变），**每轮结束后写穿到 sqlite**；进程启动不预载，按 session_id 惰性恢复。TTL 过期清理（复用 `session_ttl_seconds`）。
3. API：`GET /sessions`（当前 auth 用户的会话列表，含标题+更新时间）、`GET /sessions/{id}/messages`（恢复渲染，含 evidence 面板所需的持久化 observation data——消息表里连 typed data 一起存）、`DELETE /sessions/{id}`、`PATCH /sessions/{id}`（改标题）。**会话必须绑定 `auth_session_id` 校验归属，多用户不可互看**。
4. 前端：侧栏会话列表（新建/切换/删除；标题默认取首条消息前 20 字）；`localStorage` 记住上次活跃 session_id，刷新自动恢复；「新对话」逻辑不变。
5. 与 S2 联动：claim 证据池挂在 session state 里一起持久化。

**验收**：发几轮对话（含出面板的）→ 刷新页面 → 会话与面板完整恢复；重启后端 → 前端继续该会话，spoiler 进度仍生效；用户 B 拿不到用户 A 的会话。
**新增 golden case**：多轮 case 雏形——`spoiler_persist_across_turns`（同 session 两轮，第二轮不越界）。这条同时是未来多轮 eval 的第一块砖（RL 侧复用，但现在动机是功能质量）。

### S2 · 跨轮 claim 证据池

**目标**：修一个真实误判——多轮对话第二轮常见"刚才那部是哪年的？"，模型直接用上一轮 observation 回答、不再调工具；当前 [obs.py](../backend/otomo/obs.py) 的 observations 池**每轮从空开始**，正确答案会被判"无据/推测"，甚至被 auto revision 错改。

**做法**：
1. `AgentState` 增 `evidence_pool: list[dict]`——每轮 `traced_stream` 结束把当轮 `_obs_for_verifier` 结果 append（带 `turn` 序号 + `ts`），随 S1 持久化。
2. `verify_answer_claims(answer, observations)` 的入参改为 `当轮 observations + 历史池`；命中历史证据的 claim 输出加 `evidence_turn: N`，时效敏感类（评分/排名，现有 claim 分类已区分数值类）标注"截至第 N 轮查询"。
3. 控制规模：池子保留最近 8 轮或 ≤ 60 条 observation 摘要，超出裁剪最旧的（claim 对齐是文本匹配，池子过大既慢又引入误配）。
4. auto revision 的触发阈值对"仅历史证据支持"的 claim 放宽（有据但旧 ≠ 无据）。

**验收**：两轮对话——第一轮查某番详情，第二轮问"它是哪年播的"（模型不调工具直接答）→ ClaimCheckPanel 显示该 claim `supported=true, evidence_turn=1`，不触发 auto revision。
**新增 golden case**：`claim_cross_turn`（依赖 S1 的多轮 case 机制）。

---

## 4. X 线 · 体验/性能

### X1 · 重工具并发化（ROI 最高的体验修复）

**目标**：recommend / review / season / user_analysis 的多源 enrich 全是串行 await（核验：四个文件无一处 `gather`/`Semaphore`）。缓存命中时无感，但**缓存冷的首查**（恰是 demo 给人看的时刻）延迟全额叠加。目标：冷缓存首查延迟砍半以上。

**做法**：
1. 新建 `otomo/tools/_concurrency.py`：per-host 信号量表（尊重各源限流），
   ```python
   HOST_LIMITS = {"bangumi": 6, "egs": 2, "vndb": 3, "anilist": 3, "bilibili": 2, "yuc": 2, "musicbrainz": 1}
   async def gather_limited(coros, host: str): ...   # Semaphore + asyncio.gather(return_exceptions=True)
   ```
   `return_exceptions=True` + 逐项降级（单源失败不拖垮整答，沿用现有"优雅降级"哲学）。
2. 改造点（各自半天内）：
   - `recommend` 第 3 步 Evidence Enrich：候选循环 → 按 host 分组 gather。
   - `review`：Bangumi/EGS/VNDB/AniList/MusicBrainz 五源天然独立 → gather。
   - `season`：yuc 匹配后的逐条 Bangumi enrich → gather。
   - `user_analysis`：好友页/私评批量抓取 → gather（小并发 2，好友页是 HTML 抓取，保守）。
   - F1 的 `get_airing_progress` 直接用该原语起步。
3. **量化验收**（做到位，不拍脑袋）：清缓存跑 `recommend 治愈 galgame` / `如何评价装甲恶鬼村正` / `2026年7月新番` 各 3 次，记录 before/after P50 延迟进 PR 描述；MusicBrainz（官方限 1 req/s）不并发，确认无 429/风控。

### X2 · 工具进度事件

**目标**：recommend 十几秒内部黑盒 → 前端能看到"召回 32 候选 → 证据补全 12/32 → 重排 → 组织解释"。

**做法**：事件协议加 `ProgressEvent(tool, stage, current?, total?, note)`（挂在现有 AG-UI 风格分类的 State 类下，序列化进 SSE 流）；工具侧通过已有的事件回调通道上抛（runner 已有 emit 通路，工具入参加可选 `progress_cb`，不改契约的默认为 None）；recommend 五步各上抛一次、enrich 每完成 25% 上抛、review 每源完成上抛。前端 TracePanel 顶部渲染当前 stage + 细进度条，替代干等。
**验收**：冷缓存跑一次推荐，能看到 ≥4 个阶段更新，无进度倒退。

---

## 5. D 线 · 部署（阿里云）

### D1 · 容器化 + CI

**做法**：
1. `backend/Dockerfile`：`python:3.12-slim` + **装 ffmpeg**（F3 的 yt-dlp/ASR 依赖，一并进镜像）+ `pip install -e .` + `CMD uvicorn otomo.api.app:app --host 0.0.0.0 --port 8000`（无 `--reload`，单 worker，见 D2）。
2. `frontend/Dockerfile`：`node:20-alpine` 两段构建；next.config 开 `output: "standalone"`，运行段只拷 standalone 产物。
3. `docker-compose.yml`：`backend` + `frontend` + `caddy`（反代 + 自动 HTTPS）三服务；**第四个服务 `weekly`**：同 backend 镜像、`command: python -m otomo.weekly_daemon`（见 D2）。volumes：`./cache:/app/cache`（ltm/auth/uploads/sessions 全在这）、`./models:/app/models`（bge-reranker 大文件不进镜像）、`./backend/.env`。
4. `.dockerignore`：`node_modules/.next/models/cache/trajectories/__pycache__`。
5. `.github/workflows/ci.yml`：
   - `backend`：`pip install -e .[dev]` → `pytest -m "not external"`。**前置小改**：给 `test_external_sources.py` 等外部抓取/LLM 依赖用例打 `@pytest.mark.external`（pyproject 注册 marker），CI 只跑纯逻辑测试，外部用例本地跑。
   - `frontend`：`npm ci && npm run build`。
   - 可选手动 job `golden-eval`：`workflow_dispatch` 触发，secrets 注入 LLM key，跑 golden cases 出报告 artifact——"eval 即回归门禁"的工程叙事落在 CI 里。

**验收**：全新机器 `docker compose up -d` 一条命令起全套；push 触发 CI 绿。

### D2 · 生产运行形态（先想清楚再上云）

**结论先行：单 worker uvicorn 起步**——otomo 是异步 IO 密集型，单 worker 撑个人+朋友规模绰绰有余，且绕开全部共享状态问题。多 worker 的三个坑（**现在不修，但写进文档防未来踩**）：`app.state.sessions` 内存 dict 不共享（S1 落 sqlite 后热缓存不一致仍在）；weekly scheduler 每 worker 各起一份（`last_run_key` 幂等有竞态窗口）；LTM JSON 文件写无锁。真要横向扩：session/缓存上 Redis（config 里 `cache_ttl` 注释早已预留"换 Redis"）+ LTM 迁 sqlite/加 `filelock`。

**现在要做的一件事**：把 weekly scheduler 从 API 进程剥离成 `otomo/weekly_daemon.py`（`asyncio.run(WeeklyDigestService(...).run_forever())` 的薄入口），compose 单独 service。理由：即使单 worker，API 进程重启/重载也会打断 `run_forever`；剥离后 API 无状态化更干净，`weekly_scheduler_enabled` 默认改 False（API 进程内不再跑）。

### D3 · 限流与成本护栏（公网可访问前的 blocking 项）

**目标**：挂公网当天起，一个爬虫不能刷爆 DeepSeek/VLM 账单。

**做法**：
1. 请求限流：[slowapi](https://pypi.org/project/slowapi/)（FastAPI 生态标准）——`/chat` 按 IP `10/minute`、按 auth_session `30/hour`；`/uploads` `5/minute`；`429` 带 `Retry-After`，前端 toast 提示。
2. 匿名降级：未 OAuth 登录——禁多模态与写回、单会话最多 8 轮、不落 LTM。登录用户全功能。
3. LLM 花费熔断：`otomo/quota.py`——按 (user, day) 累计 LLM/VLM token 用量（chat completion 响应的 usage 字段已有，obs 层顺手记），`cache/quota.json` 落盘；超 per-user 日限或全局日限 → 拒绝并明示"今日配额已用完"。config：`daily_token_budget_user` / `daily_token_budget_global`。
4. Provider 侧兜底：DeepSeek/百炼后台设月度充值上限（控制台操作，写进部署 checklist）。

**验收**：脚本连发 30 次 /chat → 第 11 次起 429；伪造 usage 触发熔断 → 明确报错不再调 LLM。

### D4 · 生产配置清单 + 备份（checklist，部署日逐项打勾）

| 项 | 动作 |
|---|---|
| 域名与备案 | 阿里云国内节点公网提供 Web 服务**必须 ICP 备案**（流程 1~3 周，**最早启动**）；不想备案则选香港/海外节点（pixiv 顺带可达，但到国内延迟略高） |
| HTTPS | Caddy 反代自动签 Let's Encrypt；防火墙/安全组只开 80/443 |
| Cookie/CSRF | `cookie_secure=true`；`auth_encryption_key` 显式设置固定 Fernet key（默认自动生成的 dev key 重建容器即失效 → 全员登录态丢失） |
| OAuth | Bangumi 开发者后台 redirect_uri 换正式域名，`.env` 同步；`frontend_base_url`/`cors_allowed_origins` 收紧为正式域名 |
| 通知 | SMTP（或只用 webhook）配置；`notification_email_enabled` 按需 |
| 备份 | 宿主机 cron 每日 `tar cache/{ltm,auth,sessions} → ossutil cp oss://otomo-backup/`，保留 14 天；**部署周内做一次恢复演练**（新容器还原备份，登录态/记忆完好才算数） |
| 磁盘巡检 | `cache/uploads` 按大小滚动清理（上传图已限 6MB/张，但量会积累） |
| 日志 | uvicorn access log + `logs/` 挂 volume；Langfuse 若启用换生产 key |

### D5 · 周报推送最后一公里

**现状**：scheduler/inbox/webhook/email 全实现，`ConfigureWeeklyDigestArgs` 连时区都有。差两件事：

1. **webhook 格式适配**：`notifications.py::_send_webhook` 发的是 generic JSON payload，而个人最顺手的两个接收端参数格式不同——Server酱 Turbo 要 `POST https://sctapi.ftqq.com/{SENDKEY}.send` 带 `title`/`desp`（表单/查询参数）；Telegram 要 `POST https://api.telegram.org/bot{TOKEN}/sendMessage` 带 `chat_id`/`text`。做法：`WeeklyDigestSubscription` 增 `webhook_format: Literal["generic","serverchan","telegram"] = "generic"`，`_send_webhook` 按 format 组装 payload（各 ~10 行），digest 渲染成 markdown 摘要文本作为 desp/text。
2. **内容升级**：F1 完成后，「本周放送」section 进周报——"你在追的 X 周四更新第 8 集；想看的 Y 本周开播"。这才是你要的"**当期新番状态**"推送；没有 F1 的周报只是收藏队列复读。

**验收**：订阅 `channels=["inbox","webhook"], webhook_format="serverchan"` → 周一 9 点手机收到微信推送，含本周放送对齐内容；`weekly` 容器重启不重复推送（`last_run_key` 幂等）。

---

## 6. 明确后置（不是不做，是现在不做）

- **RL 数据侧**（docs/15 C 线：rejection sampling 规模化 / 用户模拟器 / decision_log→DPO 导出）：等确定训练用 policy（qwen3.5 级）与 A100 再启动——届时生成侧 API 换成目标模型族，现在产的数据分布不匹配。**但 S1 的多轮 session 机制、golden case 的 turns 雏形是它们的直接地基，第一波顺手铺好。**
- **RL 训练本身**：维持既有判断（弱 policy 负优化风险 + 算力未就绪）。
- **Redis / 多 worker**：单 worker 形态够用之前不引入（D2 已写清触发条件）。
- **pixiv 公网版**：合规与网络评估通过前，仅本地 demo（`pixiv_enabled` 开关）。
- **大规模全文 RAG 抓站**：维持 docs/15 §4 判断，按需 URL 摘要已覆盖。

## 7. 依赖关系与建议执行序

```
F1 放送日历 ──┬─→ D5 周报推送(内容)      X1 并发化 ──→ F1 的 airing_progress 复用
              └─→ F4 一键写回(日历卡也加)  S1 会话持久化 ──→ S2 跨轮claim池 ──→ (远期)多轮eval/RL
D1 Docker/CI ──→ D2 运行形态 ──→ D3 限流 ──→ D4 清单 ──→ D5 推送(通道)
F3 ASR(dashscope版) 依赖 D4 完成(OSS/百炼北京地域就绪)；本地 faster-whisper 版无依赖可先行
备案(D4 首行) 流程最长 ──→ 部署日之前 1~3 周启动
```

**一句话执行序**：备案先递（等待期免费）→ F1 + X1 + S1（第一波，每项 1~3 天）→ D1~D5（第二波，集中一周上云）→ F3/S2/X2/F4/F2（第三波，按兴趣与网络条件排）。

---

> Sources（2026-07-04 联网核实）：
> [Bangumi API 文档](https://bangumi.github.io/api/)（/calendar 与 /v0/episodes 已实测）·
> [pixivpy（upbit）](https://github.com/upbit/pixivpy) · [PixivPy-Async](https://pypi.org/project/PixivPy-Async/) · [gppt 取 token](https://pypi.org/project/gppt/) · [Pixiv OAuth Flow（ZipFile）](https://gist.github.com/ZipFile/c9ebedb224406f4f11845ab700124362) ·
> [百炼录音文件识别（Fun-ASR/Paraformer）](https://help.aliyun.com/zh/model-studio/recording-file-recognition) · [Paraformer Python SDK](https://help.aliyun.com/zh/model-studio/paraformer-recorded-speech-recognition-python-sdk) · [Paraformer 计费（0.00008元/秒）](https://help.aliyun.com/zh/model-studio/developer-reference/billing-for-paraformer) ·
> [yt-dlp](https://github.com/yt-dlp/yt-dlp)（B站 bestaudio 抽取，新版自带 Referer 处理）·
> [slowapi](https://pypi.org/project/slowapi/) · [Server酱 Turbo](https://sct.ftqq.com/) · [Telegram Bot API sendMessage](https://core.telegram.org/bots/api#sendmessage)
