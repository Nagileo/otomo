# 20 · 分享页 / 跨媒介源路由 / 主动订阅路线

> 来源：2026-07-07 对 docs/19 完成后的下一阶段讨论。
> 共同判断：Otomo 现在已经不缺单点工具，下一步最有展示价值的不是继续堆 RAG，而是把结构化结果变成**可分享、可复用、可订阅**的产品闭环。

---

## 0. 总目标

把 Otomo 从“聊天框里能回答”推进到“能生成可传播的 ACGN 产品页 + 能按媒介自动选源 + 能持续提醒”的产品形态。

三条主线：

1. **S 线 · 分享型产品页**：作品档案、补番路线、月报、新番导视生成公开但脱敏的分享页。
2. **R 线 · 跨媒介源路由**：anime 之外，把 comic / light novel / music / galgame 的 canonical / metadata / reputation / discourse / navigation 分层显性化。
3. **N 线 · 主动订阅产品化**：把周报、每日追番、RSS、生日、UP 新视频等做成订阅规则系统。

优先级：**S 线最高**。它复用现有结构化工具，工程风险低，对项目展示提升最大。

---

## 1. S 线 · 分享型产品页

### 1.1 为什么要做

聊天回答难以展示项目价值：

- 需要用户进入 app、发 prompt、等待工具调用。
- 面试/开源展示时，别人很难快速理解 Otomo 的能力。
- 对话记录混杂用户私有状态，不适合传播。

分享页解决的是“结果产品化”：

- 作品档案页像 Bangumi + Otomo 评价矩阵 + 补番/资源/音乐的一页式聚合。
- 补番路线页可以直接发给朋友。
- 月报页像 ACGN Wrapped。
- 新番导视页可以作为季度追番清单。

### 1.2 ShareSnapshot 后端模型

新增快照存储，先本地文件或 SQLite，后续迁 DB：

```json
{
  "id": "share_xxx",
  "type": "subject_dossier",
  "title": "摇曳露营 作品档案",
  "summary": "无剧透作品档案与补番路线",
  "payload": {},
  "sources": [],
  "visibility": "public_unlisted",
  "created_by": "sunshineclover",
  "created_at": "2026-07-07T12:00:00+08:00",
  "updated_at": "2026-07-07T12:00:00+08:00",
  "expires_at": null,
  "schema_version": 1,
  "spoiler_level": "none",
  "personalized": true,
  "redaction": {
    "profile_private_fields_removed": true,
    "token_fields_removed": true,
    "webhook_fields_removed": true
  }
}
```

建议类型：

```python
ShareType = Literal[
    "subject_dossier",
    "watch_order",
    "monthly_report",
    "season_guide",
    "watch_cockpit",
]
```

### 1.3 API 设计

```text
POST   /share/snapshots
GET    /share/snapshots/{id}
DELETE /share/snapshots/{id}
GET    /share/mine
```

`POST /share/snapshots` 输入：

```json
{
  "type": "subject_dossier",
  "title": "摇曳露营 作品档案",
  "payload": {},
  "sources": [],
  "include_personalized_reason": false,
  "expires_in_days": null
}
```

输出：

```json
{
  "ok": true,
  "id": "share_abc",
  "url": "http://localhost:3000/share/subject_dossier/share_abc",
  "snapshot": {}
}
```

### 1.4 脱敏规则

默认原则：**分享结构化结论，不分享用户隐私**。

必须移除：

- OAuth token / auth session / csrf。
- email / webhook URL / Web Push endpoint。
- 私有收藏原始 comment，除非用户显式选择公开。
- 用户名以外的身份敏感字段。
- trace 原始工具参数中可能包含的本地路径、key、URL token。

个性化解释分三级：

| 模式 | 行为 |
|---|---|
| `public_generic` | 不显示“你喜欢/你看过”，只保留泛化结论 |
| `public_personalized` | 显示“按该用户公开画像”，但隐藏具体私评 |
| `private_preview` | 仅当前用户可看，保留更详细解释 |

剧透规则：

- 默认分享页 `spoiler_level=none`。
- 若 payload 含 mild/full，需要页面顶部显式 spoiler warning。
- 分享页不能自动展开剧透区。

### 1.5 前端路由

Next.js 路由：

```text
/share/[type]/[id]
```

初期支持：

- `/share/subject_dossier/{id}`
- `/share/watch_order/{id}`
- `/share/monthly_report/{id}`
- `/share/season_guide/{id}`

共享布局：

- 顶部：标题、生成时间、来源数、剧透等级、Otomo 标识。
- 主体：按类型渲染专用页面。
- 底部：sources / caveats / schema version / “由 Otomo 生成”。

### 1.6 面板入口

每个产品面板加动作：

- `生成分享页`
- `复制链接`
- `重新生成`
- `撤销分享`（仅 mine 列表或当前会话生成者）

首批加入口的面板：

- `SubjectDossierPanel`
- `WatchOrderPanel`
- `MonthlyWatchReportPanel`
- `SeasonGuidePanel`

### 1.7 页面设计要求

分享页不是聊天 UI，不能直接复用聊天气泡。

#### 作品档案页

结构：

1. Hero：封面、标题、年份、类型、评分、标签。
2. 一句话结论：无剧透评价摘要。
3. 评价矩阵：Bangumi / 外部源 / 评论方面。
4. 补番路线入口。
5. OP/ED/音乐。
6. 观看/购买入口。
7. RSS/离线资源入口。
8. 分集热度雷达。
9. 关系 / IP 图谱。

#### 补番路线页

结构：

1. 系列标题。
2. 主线顺序。
3. OVA / 番外 / 剧场版必要性。
4. 总集篇可跳过候选。
5. 不同演绎 / 重制 / 替代路线。
6. 预计时长和注意事项。

#### 月报页

结构：

1. 月份 summary。
2. 本月完成 / 本月更新。
3. 评分分布。
4. 标签漂移。
5. Staff / CV / Studio。
6. 搁置 / 抛弃观察。
7. caveat：Bangumi updated_at 不是严格观看完成日期。

#### 新番导视页

结构：

1. 季度标题。
2. 热播榜 / 口味榜切换。
3. 每部作品：评分、热度、fit、制作、放送。
4. 圈层导视源：百合 / 芳文 / 数据向 / 泛用漫评。
5. B站导视命中：已命中视频 vs 仅导航入口。
6. sources/caveats。

### 1.8 验收标准

- 从任一产品面板点击“生成分享页”能得到 URL。
- 未登录用户打开分享页也能看。
- 分享页不暴露 token / webhook / email / session。
- 关闭后重新打开 URL，内容稳定。
- 删除分享后 URL 返回 404 或 “已撤销”。
- `npm run build` 通过。
- 后端测试覆盖 snapshot redaction。

---

## 2. R 线 · 跨媒介源路由

### 2.1 目标

现在 anime 新番已经有百合/芳文/数据向/漫评 UP 的圈层路由。下一步要把这套思想推广到所有媒介：

```text
用户问题 + subject_type + intent
→ canonical / metadata / reputation / discourse / navigation
→ agent 只在正确层级使用来源
```

避免两个问题：

- 把导航源当事实源。
- 把某圈层适合的讨论源误说成已经覆盖具体作品。

### 2.2 统一工具

新增：

```text
route_subject_sources(subject_id?, title?, subject_type, intent)
```

参数：

```python
subject_type: anime | book | music | game | real
intent: fact | review | recommendation | guide | resource | image | music | discourse
```

输出：

```json
{
  "subject": {},
  "source_layers": {
    "canonical": [],
    "metadata": [],
    "reputation": [],
    "discourse": [],
    "navigation": []
  },
  "recommended_tools": [],
  "blocked_uses": [],
  "caveats": []
}
```

### 2.3 各媒介源策略

#### anime

| 层级 | 来源 |
|---|---|
| canonical | Bangumi |
| metadata | yuc, AnimeThemes, AniList 辅助 |
| reputation | Bangumi 评分/短评/分集讨论 |
| discourse | B站导视评论、论坛/贴吧 URL 摘要 |
| navigation | B站、蜜柑、VCB、DMHY、ACGNX |

#### galgame / game

| 层级 | 来源 |
|---|---|
| canonical | Bangumi game, VNDB |
| reputation | Bangumi, 批判空间 |
| metadata | VNDB release/staff/tags |
| discourse | 绯月、月幕、galgame 吧 URL 摘要 |
| navigation | Steam / DLsite / FANZA / 批判空间入口 |

原则：Bangumi 仍是中文圈主锚点；VNDB/批判空间是增强证据，不替代 Bangumi。

#### comic / light novel / book

| 层级 | 来源 |
|---|---|
| canonical | Bangumi book |
| metadata | Open Library / Google Books / ISBN |
| reputation | Bangumi 评分/短评 |
| discourse | 轻之国度、真白萌、贴吧、S1/NGA URL 摘要 |
| navigation | BOOK☆WALKER、Amazon、B漫、MangaDex |

注意：`book` 里要区分漫画 / 轻小说 / 小说，优先用 Bangumi tags 和 relation。

#### music

| 层级 | 来源 |
|---|---|
| canonical | Bangumi music |
| metadata | AnimeThemes, MusicBrainz, AniSongDB |
| reputation | Bangumi 评分/短评 |
| discourse | 网易云/QQ音乐只导航或用户显式 URL 摘要 |
| navigation | 网易云、QQ音乐、YouTube/B站搜索 |

注意：MusicBrainz 是元数据源，不是口碑源。

#### fanart / image

| 层级 | 来源 |
|---|---|
| source search | SauceNAO, trace.moe |
| metadata | Pixiv, OCR/VLM |
| canonical anchor | Bangumi |
| navigation | ascii2d/IQDB/Pixiv/X |

### 2.4 前端表现

新增 `SourceRoutingPanel`：

- 每层源分栏。
- `can_answer_fact=true/false`。
- `risk`: low / medium / high。
- `recommended_next_tool`。
- `why_not_used`。

### 2.5 验收标准

- 用户问“推荐 galgame”时，能解释 Bangumi / EGS / VNDB 各自角色。
- 用户问“某轻小说评价”时，不会只用 anime 源。
- 用户问“OP 谁唱的”时，优先 Bangumi music + AnimeThemes，不用 B站评论当事实。
- 用户问“下载哪有”时，明确资源导航不代表托管/播放。

---

## 3. N 线 · 主动订阅产品化

### 3.1 目标

把现有周报/每日提醒扩展为统一订阅规则系统。

不是“多几个开关”，而是：

```text
订阅对象 + 触发条件 + 过滤器 + 渠道 + 模板 + 推送记录
```

### 3.2 订阅模型

```json
{
  "id": "sub_xxx",
  "kind": "airing_update",
  "enabled": true,
  "title": "今日追番提醒",
  "filters": {},
  "schedule": {
    "timezone": "Asia/Shanghai",
    "hour": 9,
    "weekday": null
  },
  "channels": ["inbox", "webhook"],
  "template": "brief",
  "quiet_hours": {"start": "23:00", "end": "08:00"},
  "last_run_at": "",
  "last_hit_key": "",
  "created_at": "",
  "updated_at": ""
}
```

### 3.3 订阅类型

首批：

- `weekly_digest`：周报。
- `daily_airing`：每日追番提醒。
- `monthly_report`：月报入口。
- `rss_release`：订阅作品的 Mikan/VCB/DMHY/ACGNX 新资源。
- `birthday`：角色生日提醒。
- `bili_up_video`：白名单 UP 新导视/漫评视频提醒。

后续只有在内容生成器完成后才加入 `SubscriptionKind`，不提前暴露占位规则：

- `episode_buzz`：某部作品分集讨论异常高。
- `score_shift`：某部作品评分明显变化。
- `watch_plan_due`：计划板到期提醒。

### 3.4 前端设置页

当前已有推送设置雏形，下一步升级成完整订阅页：

```text
/settings/subscriptions
```

页面结构：

1. 订阅总览。
2. 新建订阅。
3. 每条订阅卡片：启用/暂停、渠道、频率、最近一次推送。
4. 测试推送。
5. 推送记录 / inbox。

### 3.5 推送渠道

现有：

- inbox
- email
- webhook generic/serverchan/telegram/discord/feishu

后续：

- Web Push：部署 HTTPS 后做。
- QQ / 微信：不做主线。需要时走 Server酱 / 邮件 / webhook 中转。

### 3.6 调度与部署

本地：

- backend 或 daemon 开着才会跑。
- 适合 demo。

服务器：

- 常驻 scheduler。
- 多实例需要 leader lock。
- token/session/LTM/share snapshot 都迁 DB。

### 3.7 验收标准

- 可以新建/暂停/删除订阅。
- 测试推送能立即触发。
- 推送记录可追踪。
- 后端重启后订阅仍存在。
- 多个订阅不会重复推同一条内容。
- 本地和服务器部署配置分离。

---

## 4. 推荐实施顺序

### 第一波：分享页 MVP，但按最终架构做

1. `ShareSnapshot` 模型和 store。
2. `/share/snapshots` API。
3. 脱敏/redaction。
4. `/share/[type]/[id]` 页面。
5. `SubjectDossierPanel` / `WatchOrderPanel` / `MonthlyWatchReportPanel` / `SeasonGuidePanel` 加分享按钮。
6. 测试 snapshot redaction。

### 第二波：分享页体验深化

1. 分享页 SEO / OpenGraph metadata。
2. 复制链接 toast。
3. 我的分享列表。
4. 删除/过期。
5. 个性化公开级别选择。

### 第三波：跨媒介源路由

1. `route_subject_sources` 工具。
2. `SourceRoutingPanel`。
3. 接入 prompt。
4. galgame / book / music 三类先做。
5. 加 golden cases。

### 第四波：主动订阅系统

1. 统一 `SubscriptionRule`。
2. 设置页从“单一周报配置”升级成订阅列表。
3. `monthly_report` / `birthday` / `rss_release` 三类订阅。
4. 推送记录。
5. 部署化 scheduler。

---

## 5. 不做或暂缓

- 暂不做全文论坛 RAG。先做 URL 摘要和显式来源路由。
- 暂不把 B站导视评论长期缓存/持续追踪。只在订阅 `bili_up_video` 阶段做视频元数据追踪，评论仍按需读取。
- 暂不做个人微信机器人，风险和收益不匹配。
- 暂不做 Web Push，等 HTTPS 部署后再上。
- 暂不公开用户私评原文，除非后续做明确授权开关。

---

## 6. 对求职/开源展示的价值

这份路线的核心亮点：

- **产品闭环**：从回答到可分享页面，再到订阅提醒。
- **源可信度工程**：不是简单 RAG，而是按媒介、意图、可信层级路由。
- **隐私/脱敏设计**：多用户 OAuth 场景下可公开分享。
- **可观测和可验证**：snapshot 可复现，订阅可追踪，源路由可解释。
- **ACGN 领域深度**：百合/芳文/galgame/music/book 等圈层能力不是泛用 Agent 套壳。

优先把 S 线做漂亮。它最容易让别人一眼看懂 Otomo 的价值。
