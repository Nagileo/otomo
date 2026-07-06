# 19 · 产品化闭环路线（热播导视 / 场景推荐 / 驾驶舱 / 推送矩阵）

> 来源：2026-07-06 用户与朋友的四问讨论（附完整代码核对，行号引用准确）+ 我的差异观点 + 联网核实。
> 共同结论：**方向对，但"新番导视 / 推荐 / 推送"各差一层产品化闭环**——碎片能力已齐（docs/17/18 落地后共 78 工具），本文的主题是"把碎片拼成产品"，几乎不新增数据源。

---

## 0. 四问四答（结论 + 差异点）

| 问题 | 共识 | 我的差异/补充 |
|---|---|---|
| ① 新番导视合理吗 | 基础版合理，缺"**2026年7月热播追什么**"的热榜产品 | 不需要新数据源和新大工具：热度信号**全在手**（trending 榜、calendar 的 `collection.doing` 在追人数、分集讨论增速），做**融合模式**而非独立工具（工具已 78 个，克制） |
| ② 推荐系统合理吗 | 管线完整非玩具，缺场景化/结构化解释/反馈学习/离线评测 | 场景化**不拆多个推荐器**（工具爆炸），用 `scenario` 参数预设；**反馈学习是真缺口**——feedback/decision_log 存了但 recall/rerank 根本不读；离线评测直连求职叙事 |
| ③ 还缺什么 | 驾驶舱/单集雷达/档案页/补番路线/跨媒介链路/报告/URL摘要 | 7 条里 5 条是**聚合级**（碎片全有，拼视图）；URL 摘要已完成；单集雷达自动化=接晨报一行 |
| ④ 推送怎么做 | inbox+email+webhook 稳 → Discord → TG → Server酱；QQ/微信缓 | Discord/飞书 webhook 就是现有 `webhook_format` 的**十行级变体**，一次补齐；QQ 官方 bot/个人微信**明确不做主线**（写死）；真正缺的是**订阅管理 UX**（现在只能对话配置）；Web Push 部署后做 |

**联网核实的关键修正**：music 补源别指望 VGMdb——[vgmdb.info 第三方 API 已停摆一年+](https://github.com/jellyfin/jellyfin-plugin-vgmdb/issues/30)（2025-11 确认），[官方 API 2024-12 宣布至今未发](https://web-cdn.bsky.app/profile/vgmdb.net/post/3lcvclcpny22d)；替代 = [AnimeThemes](https://github.com/LetrixZ/animethemes-api)（OP/ED 官方 API）+ AniSongDB，且优先级本就低。

---

## 1. H 线 · 本季热播导视（"现在追什么"）

### H1 · 热度信号融合（零新数据源）
三个已有信号拼成 `hotness`：
1. **`collection.doing`**（calendar API 每番自带"在追人数"）——最稳、官方、每季全量；
2. **trending 排名**（p1 非正式端点，[discovery/tool.py](../backend/otomo/tools/discovery/tool.py) 已标注结构可能变动）——作加分项不作依赖，挂了自动降级到 doing；
3. **分集讨论增速**（`get_subject_episodes` 的 comment 数 / 已播集数）——开播后的"口碑活性"，首集爆/崩在这里。
归一加权（doing 0.5 / trending 0.3 / 讨论增速 0.2），输出 `hotness` + `hotness_evidence`（三个原始数字都给，source/confidence 惯例）。

### H2 · `season_guide_brief` 加 `mode: "guide" | "hot"`
- `hot` 模式：候选按 hotness 排序 → 用户画像 fit 重排（已有 `_fit_item`）→ 输出"本季热播 TopN + 你该追哪几部 + 为什么（热度证据 × 口味命中分开陈述）"。
- B站评论维持 opt-in（同意用户判断：默认不读，慢 + 剧透风险）。
- prompt 路由："7月热播/大家都在追什么/现在追什么" → `mode=hot`；"7月有什么番"（全量盘点）→ 原模式。
**验收 & eval**：`2026年7月现在什么番最热？追哪个？` → 热度榜 + 口味重排双段呈现，热度证据可见；trending 端点 mock 失效时结果仍有序（doing 兜底）。

### H3 · 面板热度徽章
SeasonGuidePanel 的卡片加 hotness 徽章（🔥 分级）+ 在追人数；周表格（WeekGrid）同步。

---

## 2. R 线 · 推荐进化

### R1 · `scenario` 参数（场景化但不拆工具）
`recommend_subjects` 加 `scenario: Literal["general","tonight","season","backlog","gal_intro","cross_media"]`——每个场景是**参数预设**（复用既有开关）：
- `tonight`：完结优先 + 短篇加权（eps≤13）+ 高分保底，输出"今晚一部"；
- `season`：当季过滤 + H1 热度信号进 rerank；
- `backlog`：想看列表内排序（结合口味/时长/完结度，`backlog_cleaner` 的实现落点）；
- `gal_intro`：galgame 三源 + 入门友好（时长/无 R18 偏好过滤）；
- `cross_media`：跨媒体召回权重拉满。
prompt 给一张场景路由表。**不新增工具**。

### R2 · 解释五元组 schema 整形
每个候选固定输出：`why_recalled`（召回原因）/ `fit_points`（适合你的点）/ `risks`（雷点/剧透等级）/ `heat`（当前热度，来自 H1）/ `next_step`（加想看/查渠道/看巡礼）。现有 reasons/evidence/quality_badges 重组即可，前端 RecommendPanel 分区渲染。

### R3 · 反馈闭环接入重排（本线真缺口）
现状：`record_recommendation_feedback` / `decision_log` **只写不读**。接入：
1. recall 前读 memory：`dislike`/`别推这种` 的 subject 及其强关联 tag → 硬 exclude / 降权；
2. `like` 的 subject → 其 tags/staff 进画像加权（低权重，`derived_from_feedback` 低置信惯例）；
3. decision_log 的 reject 理由若含 tag 词（"太长""致郁"）→ 当轮 rerank 惩罚。
**验收 & eval**：`别再推校园恋爱` 后下一轮推荐零校园恋爱标签；`这部不错` 后相似候选排名上升。

### R4 · 离线 leave-one-out 评测（直连求职叙事）
`backend/scripts/eval_recommend.py`：把自己收藏中评分 ≥8 的作品随机 hold-out N 部 → 用其余收藏建画像跑推荐 → 统计 **HR@K / NDCG@K**（被藏起来的高分作能否被召回）。对比三组：纯标签 / +图谱 / +CF+aspect 全开——**量化各召回通道的贡献**，比主观调 prompt 可靠，也是 recsys-offline 方法论在在线系统上的复活（面试可讲）。

### R5 · 多媒介补源（低优先，数据源已核实）
- music：~~VGMdb~~（API 已死，见 §0）→ [AnimeThemes API](https://api.animethemes.moe)（OP/ED 官方）补主题曲元数据；MusicBrainz 已有；
- book/comic/LN：ISBN/连载状态等元数据缓做，口碑仍以 Bangumi 为主（同意用户判断）。

---

## 3. C 线 · 驾驶舱 / 档案 / 跨媒介（聚合级，碎片全有）

### C1 · 本季追番驾驶舱 `watch_cockpit`
一个聚合工具 + 一屏面板：**今天更新**（calendar）｜**我的进度/落后**（airing_progress）｜**哪集炸了**（episode_buzz_radar 对在看条目的最新集）｜**继续/弃坑建议**（copilot + abandon 信号）｜**本周新资源**（watch_plan RSS 水位）。
实现注意：内部并发调既有工具时**信号量分层**（外层独立 host，docs/18 死锁教训第三次写进文档）。前端 CockpitPanel 五区一屏。
**这一条做完，"打开 Otomo 第一句问什么"就有了默认答案。**

### C2 · 作品档案页 `subject_dossier`
单 subject 全息：基本信息+评价矩阵（review）｜观看/购买渠道（where_to_watch）｜离线资源（release feeds）｜分集口碑曲线（radar）｜staff/CV 网络入口｜关联作品/系列顺序｜圣地巡礼入口｜"下一步"行动条（加想看/订阅 RSS/推送下载器）。
实现：聚合工具并发编排（分层信号量）+ DossierPanel；或轻量版=prompt 教 LLM 固定编排 6 工具 + inline 锚定组合（先做轻量版验证形态，聚合工具做性能优化时再上）。

### C3 · 补番路线升级
`plan_watch_order` 现状核查 + 升级：剧场版/OVA/总集篇的观看顺序判定（relations 的 relation 类型细分）、"跳过总集篇"建议、每步标时长与必要性（本传/外传/可跳过）。

### C4 · 跨媒介 IP 地图 `franchise_map`
显式工具：一个 IP 的全媒介结构（动画→漫画/轻小说原作→galgame→剧场版→音乐），relations 图谱数据现成，输出树状 + 各媒介 Bangumi 评分对比——图谱强项的展示型产品，配 FranchisePanel。

### C5 · 单集雷达进晨报
`DailyAiringService`：在看条目当日有新集 → 顺手跑 episode_buzz_radar 最新集 → "昨晚第 8 集讨论量 3 倍于平均，无剧透摘要：……"进晨报。防剧透沿用 progress 硬过滤。

### C6 · 月度报告
年度报告（taste report）加 `period: "month"`：本月看完/新增/弃坑（含原因信号）/口味漂移对比上月。晨报月初推一次入口。

（点子池存目，低优先：弹幕高能词云｜今日番签｜ACGN quiz｜年度 Wrapped 分享图——见 docs/18 尾注。）

---

## 4. P 线 · 推送矩阵

**现状**（已强于讨论稿的认知）：inbox/webhook(generic/serverchan/telegram)/email 三通道 + WeeklyDigest + DailyAiring 双服务 + weekly_daemon 独立进程，全部就绪。

### P1 · webhook format 补齐（十行级/个）
`webhook_format` 加 `discord`（POST `{content}` 到 [incoming webhook URL](https://discord.com/developers/docs/resources/webhook)，长文切 2000 字符；进阶用 embeds 卡片）+ `feishu`（`{"msg_type":"text","content":{"text":...}}`）。钉钉/企业微信如需同理。**QQ 官方 bot（公网+域名+IP 白名单+审核）与个人微信（封号风险）写死为"不做主线"**——用户要 QQ/微信触达时，答案是 Server酱→微信 或 邮件。
**验收**：订阅 `webhook_format=discord` → 频道收到晨报；超长内容正确分段。

### P2 · 订阅管理 UX（真缺口）
现在配置订阅只能靠对话。前端加"订阅设置"面板：开关（周报/每日）、时间、时区、渠道多选、webhook URL/email 填写、**测试推送按钮**（调 `generate_weekly_digest_now`）。后端端点已全，纯前端工作。

### P3 · Web Push（部署后）
浏览器原生推送：Service Worker + VAPID（pywebpush）+ PushSubscription 存 memory。前置 = HTTPS + 常驻 worker（weekly_daemon 形态已就绪）——排在部署上线之后，价值是"不装任何 IM 也能收到"。

### P4 · 推送内容分级
沉淀原则：**日更类**（晨报：更新/生日/新资源/单集爆点）走轻渠道（inbox+webhook）；**周报类**（总结/推荐）可走 email；**即时类**（暂无，未来如"你追的番评分暴跌"）谨慎克制，默认不做。

---

## 5. 优先级与节奏

```
第一波（把"追什么"answered）：H1+H2+H3 热播导视 · R3 反馈闭环 · C1 驾驶舱
第二波（推荐深化+推送补齐）：R1 scenario · R2 解释五元组 · P1 discord/feishu · P2 订阅面板 · C5 雷达进晨报
第三波（展示与评测）：C2 档案页 · C4 franchise 地图 · R4 离线评测 · C3 补番升级 · C6 月度报告
部署后解锁：P3 Web Push · R5 AnimeThemes
```
依赖：H1 的 hotness 被 R1(season)/C1 复用，先做；R3 与 R1 同文件顺手；C1 面板量大放第一波尾。
**与部署的关系**：本路线全部可在本地完成；但 C1/H2 这类"日常打开就用"的能力，价值在部署后才真正释放——**产品化闭环与上线应并行推进，不互相等待**。

## 6. 明确不做 / 缓
- QQ 官方 bot / 个人微信机器人（成本与风险不匹配，Server酱/邮件覆盖触达）。
- 不拆多个推荐工具（scenario 参数化替代）；不做即时告警类推送。
- VGMdb 集成（API 已死）；社区全文 RAG（URL 级摘要已覆盖，维持 docs/15 判断）。

---

> Sources：
> [vgmdb.info API 停摆确认（Jellyfin issue, 2025-11）](https://github.com/jellyfin/jellyfin-plugin-vgmdb/issues/30) ·
> [VGMdb 官方 API 预告（Bluesky, 2024-12，未发）](https://web-cdn.bsky.app/profile/vgmdb.net/post/3lcvclcpny22d) ·
> [hufman/vgmdb（自托管需 Cloudflare cookie）](https://github.com/hufman/vgmdb) ·
> [AnimeThemes API](https://github.com/LetrixZ/animethemes-api) · [AniSongDB / AMQ-Artists-DB](https://github.com/xSardine/AMQ-Artists-DB) ·
> [Discord Incoming Webhooks](https://discord.com/developers/docs/resources/webhook) · [Telegram Bot API](https://core.telegram.org/bots/api) · [Server酱](https://sct.ftqq.com/) ·
> AniList GraphQL（airing/trending 辅助源，不替代 Bangumi 中文圈主源）
