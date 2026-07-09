# 18 · 看番通路路线（在线 × 离线，"在哪看 → 怎么追 → 怎么拿"）

> 来源：2026-07-05 讨论两轮。用户点名蜜柑 RSS 订阅/下载链路，并给出**看番决策逻辑**（本文组织原则，见 §0）；
> 竞品调研（AutoBangumi / ANI-RSS / BangumiToday / AniSearch / AnilistBot）+ **七个数据源逐一实测**（本文所有结构样例均为 2026-07-05 真实抓取，非文档转述）。
> 定位：Otomo 已完成"推荐看什么"（docs/11 Phase 6）与"管理在追的"（docs/17 F1 日历/周报）；本文补最后一环——**帮你看上**。对照同类 bot 确认这是当前最大的功能空白。

---

## 0. 用户看番决策树（一切设计的组织原则）

真实的看番路径分**在线 / 离线**两个世界，优先级自上而下：

```
想看某部番
├─ 在线看
│   ① 正版直达：B站/爱奇艺等平台买了版权 → 给正版页链接直接看
│   │   （数据源：yuc 当季配信 + bangumi-data 历季全量）
│   ② 站内搜索兜底：平台没买 → B站站内搜索通常能找到（用户上传）
│   │   （永远可用，但明确标注"非官方源"）
└─ 离线看（载体：RSS / 磁力链 / 种子）
    ③ 蜜柑动漫：新番 + 大部分老番的最优路径（条目级 RSS + 字幕组 + 种子）
    ④ 老番 BD-ray 收藏向：最优路径 VCB-Studio
    │   （实测捷径：dmhy/acgnx 搜 "VCB-Studio {番名}" 磁力直出，无需解析 vcb-s.com）
    ⑤ dmhy / acgnx：蜜柑的同类平替（三者同为 BT 资源索引），
        蜜柑未收录时的兜底，关键词 RSS 搜索、磁力直出
```

**产品原则**：①→⑤ 是回答顺序也是展示顺序——正版永远排最前；离线层全部是"第三方资源索引的链接聚合"，Otomo 不碰下载本体（合规红线见 §6）。

---

## 1. 数据源实测档案（2026-07-05，写实现前先读这节）

### 1.1 bangumi-data（正版放送平台库，①的历季全量源）
- **取用**：`https://unpkg.com/bangumi-data@0.3/dist/data.json`（npm CDN，~2MB；unpkg 会 302 到具体版本，httpx 需 `follow_redirects=True`）。
- **结构（实测）**：`siteMeta` 定义各平台 `urlTemplate/regions/type`——onair 类覆盖 bilibili（含**大陆/港澳/台湾分区**，四个独立 site key）、爱奇艺/腾讯/优酷/芒果/AcFun、Netflix/Niconico/動畫瘋：
  ```json
  {"siteMeta": {"bilibili": {"title": "哔哩哔哩", "urlTemplate": "https://www.bilibili.com/bangumi/media/md{{id}}/", "regions": ["CN"], "type": "onair"},
                "bangumi": {"urlTemplate": "https://bangumi.tv/subject/{{id}}", "type": "info"}},
   "items": [{"title": "...", "begin": "...", "sites": [{"site": "bangumi", "id": "..."}, {"site": "bilibili", "id": "..."}]}]}
  ```
- **关联**：`items[].sites` 中 `site=="bangumi"` 的 id 即 subject_id → 加载时建 `bangumi_id → sites` 反查索引。
- **限制**：社区维护，新番收录可能滞后数天（正好由 yuc 补）；只有 anime。

### 1.2 yuc.wiki（①的当季最快源，已确认含 B站正版链接）
- **实测**：`https://yuc.wiki/202607/` HTML 含 78 处 `bilibili`，番剧卡片内直接嵌 `<a href="https://www.bilibili.com/bangumi/play/ep2224392">`（**正版播放页**）。
- **现状缺口**：[yuc/tool.py](../backend/otomo/tools/yuc/tool.py) 的 `YucAnime` 只解析 `broadcast/official_url/pv_url/studio/staff/cast`——**B站正版链接被丢弃**。
- **价值**：当季配信比 bangumi-data 更快更准（长门有C人工维护）；解析器与 `match_confidence` 体系现成（docs/11 Phase 4），加字段即可。

### 1.3 蜜柑计划 Mikan（③离线主路）
- **条目级 RSS（实测，普通 UA 直抓无拦截）**：`https://mikanani.me/RSS/Bangumi?bangumiId=3644`：
  ```xml
  <item><title>[Prejudice-Studio] 莉可丽丝：... [01-06][Bilibili WEB-DL 1080P AVC 8bit AAC MP4][简日内嵌]</title>
    <link>https://mikanani.me/Home/Episode/{hash}</link>
    <torrent xmlns="https://mikanani.me/0.1/"><contentLength>206254896</contentLength><pubDate>...</pubDate></torrent>
    <enclosure type="application/x-bittorrent" url=".../Download/....torrent"/></item>
  ```
  - 不带 `subgroupid` 为全字幕组混合流；`&subgroupid={id}` 过滤单组。
  - **title 约定** `[字幕组] 番名 [集数][画质][字幕]` → 字幕组分组可直接从 title 前缀提取（v1 免抓番剧页）。
  - 实测含 2025 年历史合集条目——有存量数据；映射表低位 id 段（"183"→"139317" 等）显示老番覆盖广，与用户经验一致（"新番+大部分老番一定能解决"）。
- **Bangumi → Mikan 映射（关键零件，现成）**：
  `https://github.com/xiaoyvyv/bangumi-data/raw/main/data/mikan/bangumi-mikan.json`（GitHub Actions 每日同步；**注意在 xiaoyvyv/bangumi-data 仓库**，不是同名 bangumi-mikan 仓库）。
  平面 dict `{"mikan_id": "bangumi_subject_id"}`（实测）——**方向需反转**；同一 bangumi id 可能对应多个 mikan id（剧场版/季度拆分），反转用 `dict[str, list[str]]`。失败重试 jsDelivr 镜像：`cdn.jsdelivr.net/gh/xiaoyvyv/bangumi-data@main/data/mikan/bangumi-mikan.json`。

### 1.4 dmhy 动漫花园（⑤平替之一）
- **关键词 RSS（实测）**：`https://share.dmhy.org/topics/rss/rss.xml?keyword={q}`，zh-cn，item 结构：
  ```xml
  <item><title>[MingY] 莉可丽丝：友谊是时间的窃贼 [01-06][WebRip][1080p][简繁日内封]</title>
    <link>http://share.dmhy.org/topics/view/696097_....html</link>
    <enclosure url="magnet:?xt=urn:btih:6FWFNDJH..." type="application/x-bittorrent"/></item>
  ```
  **enclosure 即磁力链直出** ✓。
- **可达性**：大陆网络对 dmhy 时有不稳（社区常态）→ 与 acgnx 互为 failover。

### 1.5 acgnx 末日动漫资源库（⑤平替之二，与 dmhy 同构）
- **关键词 RSS（实测）**：`https://share.acgnx.se/rss.xml?keyword={q}`，zh-tw，"Project AcgnX Torrent Asia"；item 同样 **enclosure=magnet**（自带 `opentracker.acgnx.se` tracker）。
- **与 dmhy 的关系**：内容高度重叠（含互相搬运/整理），**一个解析器两个 endpoint**即可，按可达性自动切换。

### 1.6 VCB-Studio（④老番 BD 收藏向）
- **实测捷径（本文最重要的实现发现）**：acgnx 搜 `VCB K-ON` 直接命中 `[喵萌奶茶屋&VCB-Studio ...]`、`[动漫国字幕组&VCB-Studio ...]` 的 BD 资源——**VCB 的发布本来就走 dmhy/acgnx/nyaa 等 BT 站**。所以"VCB 层"= 在 1.4/1.5 的 RSS 搜索上把关键词加前缀 `VCB-Studio {番名}`，磁力直出，**不需要解析 vcb-s.com 文章**（其站内搜索 `vcb-s.com/?s={q}` 仅作参考外链保留）。

### 1.7 Bangumi indices（策展，顺带收编）
- 实测可用：`/v0/indices/{id}`（标题/简介/创建者）+ `/v0/indices/{id}/subjects`（条目分页）；实测 15045 = "日本动画最高收视率TOP100"。
- v0 **无目录搜索**端点 → 发现方式：用户贴链接/ID，或预置精选清单；另实测 `/v0/trending/*` 不存在（404），趋势数据死心。

---

## 2. 决策树 → 工具设计（两个工具对应两种意图）

| 用户意图 | 工具 | 覆盖决策树 |
|---|---|---|
| "在哪看 / B站有吗 / 哪个平台" | `where_to_watch` | ①② 在线 |
| "下载 / 资源 / RSS / 种子 / BD / 收藏" | `get_release_feeds` | ③④⑤ 离线 |

两工具 schema 互相引用（在线结果带 `offline_hint`，离线结果顶部固定"正版渠道见 where_to_watch"），LLM 按意图路由，prompt 工具导航各补一句。

---

## 3. O 线 · 在线看（决策树 ①②）

### O1 · bangumi-data 接入
1. 新建 `backend/otomo/tools/watch/data.py`：惰性下载 data.json 到 `cache/bangumi_data.json`（TTL 7 天，`_cache` 惯例；下载失败用旧缓存标 stale）。建 `bangumi_id → sites` 与 `title → item` 双索引。
2. 渲染：`siteMeta[site].urlTemplate` 替换 `{{id}}`；`type=="onair"` 过滤；`regions` 分组（**CN 优先展示，港澳台/日本折叠为"其他地区"**）。
**验收**：莉可丽丝 subject_id → B站正版 media 页 URL；未收录条目返回空不报错。

### O2 · yuc 配信字段增强
`YucAnime` 增 `bili_url: str | None`（预留 `stream_urls: list[dict]`）；解析器提取 `bilibili.com/bangumi/play/` 与 `/bangumi/media/` 形态（UP 主 space 链接**不算配信**，跳过）；沿用 `match_confidence`，弱匹配 UI 标"待确认"。[season/tool.py](../backend/otomo/tools/season/tool.py) enrich 顺手透进 `SeasonGuideItem`。
**验收**：`list_yuc_season(2026, 7)` 中 B站引进的番至少一半带出 `bili_url`。

### O3 · `where_to_watch` 聚合工具 + 面板
1. 新工具 `where_to_watch(subject_id | title)`，输出：
   ```json
   {"subject_id": 1, "title": "...",
    "official": [{"site": "bilibili", "label": "哔哩哔哩", "url": "...", "regions": ["CN"], "source": "bangumi_data"}],
    "yuc": {"bili_url": "...", "match_confidence": 0.9, "source": "yuc"},
    "search_fallback": [{"label": "B站站内搜索", "url": "https://search.bilibili.com/all?keyword={q}", "note": "含用户上传，非官方源"}],
    "offline_hint": true,
    "caveats": ["渠道信息截至查询时；独占/下架以平台页为准"]}
   ```
   合并顺序 = 决策树顺序：bangumi-data（权威）→ yuc（当季补充，冲突时并列标 source）→ B站搜索兜底（永远给，明确"非官方源"）。`offline_hint=true` 时 LLM 可衔接"需要离线资源吗？"。
2. 前端 `WhereToWatchPanel`：平台徽章卡（正版绿 / 搜索灰），regions 折叠；接 `_PANEL_TOOLS` + `panel_data_from_payload`（[_common.py](../backend/otomo/agent/_common.py) 既有模式）。
**eval**：`莉可丽丝在哪能看？`→ 官方源含 B站；`《某未引进番》在哪看？`→ 官方源空 + 搜索兜底 + **不编造平台**（claim verifier 盯平台断言）。

---

## 4. D 线 · 离线看（决策树 ③④⑤）

### D1 · 蜜柑资源卡（③主路，先做）
1. 新建 `backend/otomo/tools/release/tool.py`：
   - 映射加载：bangumi-mikan.json → `cache/`（TTL 1 天，GitHub raw → jsDelivr failover）→ 反转 `bangumi_id → [mikan_id]`。
   - 工具 `get_anime_release_feeds(subject_id, prefer="auto", subgroup_filter?, limit=12)`：
     a. 映射命中 → 抓条目级 RSS（`_cache` TTL 30min；`gather_limited` host="mikan"——**HOST_LIMITS 补 `"mikan": 2, "dmhy": 2, "acgnx": 2`**）；
     b. 按 title 前缀 `[字幕组]` 分组 → `{subgroup, rss_url, latest_items: [{title, episode_page, torrent_url, size, pub_date}]}`（v1 rss_url 给条目级 RSS + 组名过滤说明；subgroupid 精确 RSS 留 v2 解析番剧页）；
     c. 未命中 → 进 D2 平替链。
   - 顶层带 `mapping_confidence`（映射表命中 0.95 / 标题模糊 0.6 提示确认）+ caveats（"第三方资源索引，请优先正版渠道"）。
2. 前端 `ReleaseFeedPanel`：字幕组分组卡；**RSS 一键复制**；torrent/磁力为外链不代理；顶部固定"正版渠道 → WhereToWatch"导流条。
**eval**：`莉可丽丝有什么下载资源？`→ 字幕组分组 + 种子外链 + RSS 可复制。

### D2 · dmhy / acgnx 平替层（⑤，同一解析器）
1. 同文件加 `_bt_rss_search(keyword)`：**一个 RSS 解析器、两个 endpoint**（1.4/1.5 结构同构，enclosure=magnet），dmhy 超时/失败自动切 acgnx（`return_exceptions` 逐项降级哲学）。检索词 = `name_cn or name`（Bangumi 中文名在两站命中率高，实测均可中文检索）。
2. 触发条件：蜜柑映射未命中，或 `prefer="bt"`；输出并入 `get_anime_release_feeds` 的统一 schema，条目标 `source: "dmhy"|"acgnx"` + `magnet`。
3. 全灭兜底：蜜柑搜索页 + dmhy/acgnx 搜索页 + VCB 站内搜索外链（复用 `get_vertical_links` download 分组，标"未检索到，转外部搜索"）。
**eval**：`《蜜柑未收录的某老番》资源` → dmhy/acgnx 磁力条目或搜索外链，优雅降级不报错。

### D3 · BD 收藏向（④，VCB 关键词策略）
`prefer="bd"`（或 query 含"BD/收藏/高清/蓝光"时 LLM 传参）→ D2 的检索词改为 **`VCB-Studio {番名}`**（实测磁力直出）→ 无果再退 `{番名} BDRip`。结果标 `quality: "bd"`；说明文案"VCB-Studio 为收藏级 BD 压制，体积大"。vcb-s.com 站内搜索仅作参考外链。
**eval**：`轻音少女 BD 收藏资源` → 命中 `[**&VCB-Studio]` 条目 + 磁力。

### D4 · RSS 订阅进 watch_plan（追更闭环）
1. [memory/models.py](../backend/otomo/memory/models.py) `WatchPlanItem` 增：`rss_url: str = ""`、`subgroup: str = ""`、`last_seen_pub_date: str = ""`。
2. 对话"订阅喵萌的莉可丽丝"→ memory 写入（复用现有工具，不新增存储）。
3. 周报/追番日历融合：对带 rss 条目抓一次 RSS（缓存内），`pub_date > last_seen` 的新条目渲染"你订阅的 X 更新了：[字幕组] 第 8 集（种子链接）"并更新水位。
**验收**：订阅后下次周报含新种子；`forget` 后不再出现。

### D5 · 单集更新日推（竞品标配，只差一个日更 job）
[weekly_daemon.py](../backend/otomo/weekly_daemon.py) 只启动统一 `SubscriptionService`：`daily_airing` 规则跑 `get_airing_progress`（+D4 RSS 检查）→ 当日更新/新种子 → inbox + 既有 webhook/email 通道（`webhook_format` 适配已有）。幂等复用 subscription `last_hit_key` 模式；不再保留旧版独立 DailyAiringService。
**验收**：模拟 airdate=今天 → inbox"今日更新"；同日重跑不重复。

### D6 · qBittorrent 推送（opt-in，最后做）
config `qbittorrent_url/username/password`（默认空=禁用）；工具 `push_to_downloader(torrent_url|magnet)` 标 `is_write=True`，走 `pending_write_actions` 强确认流（复用 [writeback](../backend/otomo/tools/writeback/tool.py) 模式）；qB WebUI `POST /api/v2/torrents/add`。**公网部署默认关**，decision_log 留痕。README 明示"推送目标是用户自己的下载器，Otomo 不下载不存储任何资源内容"。

---

## 5. C 线 · 策展（Bangumi indices，顺带收编）

### C1 · 目录读取工具
`get_bangumi_index(index_id | index_url)`：解析 `bgm.tv/index/{id}` → `/v0/indices/{id}` + `/subjects`（分页走 client `_TTLCache`）→ 目录标题/简介/**创建者署名致谢** + 条目卡（与用户收藏 join 标"已看"）。发现方式：用户贴链接，或预置精选 `data/curated_indices.json`（人工挑 10~20 个高质量目录）。
**eval**：贴目录链接 → 条目卡；`给我一个入坑百合清单` → 精选目录命中则引用（带署名），未命中回退 recommend。

### C2 · 策展召回进推荐
`recommend` Recall 加第六路：候选 ∈ 精选目录 → `curation_bonus`（权重低于 CF；解释"入选『XX』目录"，与 `quality_badges` 并列）。**只用预置清单**，不动态爬全站目录。

---

## 6. 合规红线（永不越）

1. **正版优先**是展示顺序也是产品立场；离线层永远排在在线层之后并明确标注性质。
2. **链接层**：RSS/磁力/种子均为第三方资源索引的**外链聚合**——不内置 BT 客户端、不代理/存储/转发种子与磁力内容本体、不做在线播放、不做网盘转存。
3. **D6 是唯一触碰"下载动作"的层**：目标是用户自己的本地下载器、强确认流、公网默认关。
4. source/confidence 主线：渠道带 `source`；蜜柑映射带 `mapping_confidence`；渠道时效标"截至查询时"；平台/资源断言进 claim verifier 视野不得编造。
5. 抓取礼仪：三站 RSS 均限频（Semaphore 2）+ 缓存（30min）+ UA 合规；失败优雅降级不重试轰炸。

## 7. 优先级与依赖

```
O1 bangumi-data ─┐
O2 yuc 增强 ─────┼→ O3 where_to_watch ─→ eval ×2     ┐ 第一波（2~3 天）
D1 蜜柑资源卡 ── D2 dmhy/acgnx 平替 ── D3 BD 策略 ──┘ （D1~D3 同一文件同一 schema，一起做）
D4 订阅 → D5 日推（依赖 weekly_daemon 既有架构）        第二波（约 2 天）
C1 目录工具 ──→ C2 策展召回                             第二波尾
D6 qB 推送（依赖本地 qB + 强确认流）· 小件（倒计时前端化/随机 roll） 第三波
```

**建议节奏**：第一波做完，"在哪看 + 资源卡"同时上线，①~⑤ 决策树全通，闭环即完整；每波补 golden case（各节已内嵌）。

## 8. 明确不做 / 缓
- 不自建全站资源索引（蜜柑/dmhy 已是聚合器，套娃无意义）；不碰网盘转存（版权风险最高形态）。
- nyaa.si 接入——dmhy/acgnx 已覆盖中文字幕组资源，nyaa 生肉/英字场景弱，缓。
- 动态目录发现（爬 bgm.tv/index 热门页）——先预置清单。
- 随机 roll / 倒计时前端化 / 资讯流——小件见缝插针。

---

> Sources（2026-07-05 实测 + 调研）：
> [bangumi-data](https://github.com/bangumi-data/bangumi-data)（unpkg data.json：siteMeta/regions 已实测）·
> [xiaoyvyv bangumi↔mikan 映射](https://github.com/xiaoyvyv/bangumi-data)（`data/mikan/bangumi-mikan.json` 结构已实测）·
> [Mikan Project](https://mikanani.me/)（条目级 RSS：torrent 扩展/enclosure/title 约定已实测）·
> [動漫花園 dmhy](https://share.dmhy.org/)（keyword RSS + enclosure=magnet 已实测）·
> [AcgnX 末日動漫資源庫](https://share.acgnx.se/)（同构 RSS + opentracker 已实测；VCB BD 资源命中已实测）·
> [VCB-Studio](https://vcb-s.com/)（发布走 BT 站，关键词直出策略）·
> [yuc.wiki](https://yuc.wiki/)（202607 页 B站正版 play/ep 链接已实测）·
> [Bangumi API](https://bangumi.github.io/api/)（/v0/indices 可用、/v0/trending 404 已实测）·
> 竞品：[AutoBangumi](https://github.com/EstrellaXD/Auto_Bangumi) · [ANI-RSS](https://www.huluohu.com/posts/1257/) · [BangumiToday](https://github.com/BTMuli/BangumiToday) · [AniSearch](https://discord.bots.gg/bots/737236600878137363) · [AnilistBot](https://fazendaaa.github.io/AnilistBot/)
