# 02 · 数据源：能力边界与许可证红线

> **本文是硬约束，不是参考。** 所有工具/RAG/训练设计都必须落在这些边界内。结论来自 2026-06 对各 API 的实测与政策核对。

---

## 1. Bangumi API（结构化知识图谱 · 项目脊柱）

- Base：`https://api.bgm.tv`（v0 REST，OpenAPI 文档：https://bangumi.github.io/api/ ）
- **强制 User-Agent**：格式 `{developer_id}/{app_name}/{version} (platform)`，例如 `otomo-dev/otomo/0.1 (https://github.com/.../otomo)`。**通用 UA（如 `Bangumi/1.0`）会被直接拒绝**——默认 HTTP 客户端必踩。

### 1.1 四实体知识图谱（多跳的根基）

```
Subject(番/书/游戏/音乐/三次元) ──characters──> Character ──actors/persons──> Person(声优/staff)
        │                                                                         │
        ├──persons(staff: 原作/导演/脚本/制作公司...)───────────────────────────────┘
        ├──subjects(关联: 续作/前传/改编/系列)
        └──episodes(章节)
```

关系边**带上下文**，所以多跳一次调用即可拿全：
- `Subject→Character` 的 `actors[]` 直接是 Person 对象（番→角色→声优 一跳到位）。
- `Character→Person` / `Person→Character` 边带 `subject_id/subject_name/staff(角色/职位)`——拿到人、角色、作品、职责四元组。
- `Subject→Person` 带 `relation`（职位）；`Subject→Subject` 走系列/改编。

### 1.2 关键端点

| 用途 | 端点 |
|---|---|
| 搜索番（可过滤） | `POST /v0/search/subjects`（body: `keyword`,`sort∈{match,heat,rank,score}`,`filter{type,meta_tags,tag,-tag,air_date,rating,rank,nsfw}`；`limit≤50`,`offset`） |
| 搜索角色/人物 | `POST /v0/search/characters`、`POST /v0/search/persons`(`filter.career`) |
| 浏览/排行 | `GET /v0/subjects?type=&sort=rank&year=&month=` |
| 番详情 | `GET /v0/subjects/{id}`（含 `rating{score,rank,count 1-10 直方图}`,`tags[]`,`collection{...}`,`infobox`） |
| 番→角色/staff/关联 | `GET /v0/subjects/{id}/characters` · `/persons` · `/subjects` |
| 角色 | `GET /v0/characters/{id}` · `/{id}/subjects` · `/{id}/persons` |
| 人物（声优/staff） | `GET /v0/persons/{id}` · `/{id}/subjects` · `/{id}/characters`（演过/配过的角色） |
| 章节 | `GET /v0/episodes?subject_id=&type=&limit≤200` |
| 用户收藏 | `GET /v0/users/{username}/collections`、`GET /v0/me`（需 token） |
| 放送日历（legacy） | `GET /calendar`（按星期分桶，非日期范围查询） |

### 1.3 鉴权

- 读接口**免 token**（NSFW/R18 条目除外，匿名会被过滤——R18 需 token）。
- 写操作（改收藏/进度）需 token。单用户场景建议用 **Personal Access Token**（bgm.tv 个人令牌页直接签发，免 OAuth 流程）；多用户走 OAuth2 authorization code（access_token 7 天，带 refresh）。
- token 走 `Authorization: Bearer`，**禁止放 query string**。

### 1.4 限制（务必在设计时绕开）

- ❌ **无语义/全文搜索**：`keyword` 只匹配标题/别名。"找讲 XX 的番"必须自建向量层。
- ❌ **无推荐 / 无"相似作品" / 无"正在热播"接口**：相似度靠共享 tag/staff/共现自算；热度靠 `heat/score/rank` 排序近似。
- ⚠️ **评论正文（短评≤380字 / 长评 / 吐槽）v0 未正式文档化**，但**网站可见、可获取**（legacy API / 页面解析；社区先例：bangumi-takeout 导出收藏+短评）。→ **口碑质性分析可做**，具体取法在 Track A 确认，注意礼貌 + 缓存 + 来源。
- ⚠️ staff 一部分只在自由文本 `infobox` 里，需解析。
- ⚠️ 无硬性限流数字，但**必须礼貌**（几 req/s）+ 缓存（服务端已缓存 60–300s）。`/oauth/access_token` 有 ~10–20% 瞬时 500，需重试。

### 1.7 类型模型（ACGN 全覆盖，见 [00 覆盖范围原则](00-vision.md)）

Bangumi `subject_type`：`1`=书籍 / `2`=动画 / `3`=音乐 / `4`=游戏 / `6`=三次元。
**坑**：ACGN 的 comic/novel/galgame 不是顶层类型——

| ACGN 细分 | Bangumi 映射 | 子分方式 |
|---|---|---|
| anime | type=2 | 直接 |
| music | type=3 | 直接 |
| **comic 漫画** | type=1（书籍） | 靠 tag（"漫画"）/平台再分 |
| **novel 小说/轻小说** | type=1（书籍） | 靠 tag（"小说/轻小说"）再分 |
| **galgame** | type=4（游戏） | 靠 tag（"Galgame/AVG"）/platform 再分 |

→ "全覆盖"≠ 简单传 5 个 type id；comic/novel/galgame 需在 type=1/4 内按 tag/平台子过滤。当前默认 anime，子分待 anime 稳定后增量补。

### 1.5 可复用构件 —— 仅参考，工具自建

> 决策：**工具 / Skills / MCP server 全部自己手搓**（更有趣、更能讲「工」层叙事，且规避 AGPL）。下列仅作 API 用法的学习参考，**不接入其代码**。

- 📖 **Bangumi-MCP**（`github.com/etherwindy/Bangumi-MCP`，MIT，Python，50+ 工具覆盖 v0 全表）——读源学「v0 端点→工具」的映射方式，**不 import**。
- ⚠️ **bgm-cli**（`github.com/aronnaxlin/bgm-cli`，**AGPL-3.0**，Node）——只看设计，**禁止 import/链接**（否则全项目被 AGPL 传染）。
- 我们的 client：手写 thin async httpx 封装（或从公开 `open-api/v0.yaml` 代码生成 typed client——用公开规范不算用其代码）。一两百行、可控，便于自己加缓存 / 限流 / 强制 UA。

### 1.6 纯 Bangumi 即可回答的多跳问题（→ 可验证 RL 任务）

- 角色→声优→该声优其他角色/作品；"哪些番共用这个 CV"；"这个角色历代 CV"。
- 番→监督/作曲/制作公司→其他作品。
- 系列遍历（续作/前传/spinoff）、补番顺序。
- 共现/重叠分析（tag、评分分布、收藏数）：如"2024 年 SF 标签、评分≥8 的高排名番"。
- 用户收藏→评分→口味画像（无原生推荐，但原始数据足以自建）。

---

## 2. 萌娘百科（非结构化 RAG · 设定/梗）

> ⚠️ **被锁得很死**，与通用 MediaWiki 文档不同。实测入口 `https://zh.moegirl.org.cn/api.php`（MediaWiki 1.43，Cloudflare 后）。

### 2.1 API 白名单（实测）

| ✅ 可用 | ❌ 被封（`action-notallowed`） |
|---|---|
| `action=opensearch`（标题/别名补全，处理 redirect） | `list=search`（全文搜索） |
| `prop=extracts`（**纯文本正文**，`explaintext=1`，`exsectionformat=plain` 保留小节标题行） | `action=parse`（sections/wikitext/html 全封） |
| `prop=info&inprop=url`（pageid/lastrevid/length/fullurl） | `prop=revisions`（rvprop=content 原始 wikitext） |
| `prop=categories`（**已知页面**的分类） | `list=categorymembers`（枚举分类成员） |
| `&redirects=1`、`meta=siteinfo` | `list=allpages` / `prefixsearch` |

**直接后果：**
- 取不到 wikitext/HTML/infobox 结构——**唯一内容通道是 `prop=extracts`（已是 server 端清洗后的纯文本，模板/ref 已剥离，含 spoiler 展开）**。结构化字段（CV/生日/所属）改由 Bangumi 提供，萌娘只供**叙事/设定/梗**文本。
- **无全文搜索**——"哪个页面讲 X"必须**先用 Bangumi 解析实体名**，再按标题取萌娘页（`opensearch` 仅能标题前缀/别名）。
- 浏览器/文章路径对非浏览器客户端 403（Cloudflare），但 `api.php` 可达。低并发无 CAPTCHA，高并发会被挡——**串行 1–2 req/s、单连接、带描述性 UA、429/403 退避**。

### 2.2 内容结构与切分

- 页面：infobox + `简介/人物经历/角色相关/能力/梗/轶事` 等小节；大量 `{{Heimu}}`(spoiler)、furigana、`-{zh-hans:…}-` 转换。
- 切分：按 `extracts` 的小节标题行切，再按 ~300–800 token 限长；chunk 元数据带 `pageid`、标题、`lastrevid`、小节名。长页（如"初音未来"≈86KB）务必按小节切。

### 2.3 许可证红线（CC BY-NC-SA 3.0 CN + llms.txt）

萌娘 `https://zh.moegirl.org.cn/llms.txt` 明文规定 AI 系统：
- ✅ **可**：读取并**总结有限片段**；用于**非商业**研究/教育/分析。
- ⚠️ **必须**：署名 **"萌娘百科 (Moegirlpedia)" + 可见原文链接**；声明是摘要非全文；引导用户访问原页；衍生摘要沿用同协议。
- ❌ **不可**：用于**商业** AI 训练/变现；**复制整页或等价结构化数据集**；以摘要完全替代原文。
- `robots.txt`：`Content-Signal: ai-train=no`（**不可用于模型训练**），`api.php` 不在 Disallow（是预期编程入口）。

**工程落地（强约束）：**
1. 语料**只按需取 + 本地缓存**（无 dump、不可批量），缓存按 `pageid` 存 `lastrevid+touched`，用 `prop=info` 比对版本再决定是否重取。
2. **缓存属运行时产物，永不入 git**（已在 `.gitignore`）；仓库只发代码。
3. 回答**必须渲染可见 "来源：萌娘百科 — 〈标题〉" 链接**，标注为摘要。
4. **萌娘文本绝不进 RL/训练数据**（`ai-train=no`）。本项目 RL 真值只来自 **Bangumi 图谱 + 公开推荐数据集**——红线天然分离。
5. 项目保持非商业（无付费/广告）。代码 MIT，但明确标注检索内容仍属 CC BY-NC-SA。

---

## 3. 补充源

| 源 | 协议 | 用途 | 备注 |
|---|---|---|---|
| **中文维基百科** | CC BY-SA 4.0（可商用） | 中性事实、**有全文搜索 + 官方 dump** | 萌娘缺全文搜索/dump/可商用时的 license 干净兜底 |
| **AniList**（GraphQL） | 站点条款 | 丰富英文元数据、staff/CV/studio/tags | Bangumi 的英文补充 |
| **Fandom 各番 wiki** | CC BY-SA | 深度英文 lore，有 dump | 广告多、质量不一 |
| **trace.moe** | 见其条款 | 截图识番（多模态彩头） | 包其 API，不自训 |

### 3.1 "在哪看 / 资源" 外链源（仅 link-out，不抓取/不托管）

> 边界：我们**只构造搜索/订阅/深链 URL 指向这些站**，不抓取其内容、不托管任何资源。优先正版（Bilibili），资源/论坛站仅作外链补充。这些是第三方资源/论坛站（部分为 BT/盗版或成人向论坛），作为个人学习项目仅外链，使用者自负其责。

| 源 | 类型 | 用途 |
|---|---|---|
| **蜜柑计划 mikanani.me** | 新番表 + RSS/BT 订阅，`/Home/Bangumi/{id}` 深链 | "在哪下/订阅"外链、放送订阅 |
| ACGNX `share.acgnx.se` | 资源索引 | "在哪下"搜索外链 |
| Bilibili | 正版平台 | "在哪看"正版外链（优先） |
| south-plus / kfpromax 等论坛 | BBS 资源 | 可选外链，注意内容分级 |

---

## 4. 推荐系统离线数据集（仅离线训练/评测，不重分发原始用户数据）

| 数据集 | 规模 | 协议 | 用途 |
|---|---|---|---|
| **Anime Recommendations DB**（Kaggle CooperUnion） | 73,516 用户 × 12,294 番 + 评分 | 未明确 OSS（仅离线/作品集） | 入门基线首选 |
| **Anime Dataset 2023**（Kaggle dbdmobile） | ~24,905 番 + ~109M 评分 | 同上 | 双塔/MF 上规模 |
| **bangumi-data/bangumi-data** | 结构化番数据 | **CC BY 4.0**（最干净，可分发） | 元数据 |
| **Bangumi15M**（mojimoon/bangumi-anime-ranking） | 8,573 番 / ~7.77M 投票 | 见仓库 | **域内** CF/MF 训练 |

> 公开 MAL 数据集多无明确协议且源自 MAL API：**仅用于离线研究/训练与作品集 repo，不重分发原始抓取用户数据**。线上产品按用户授权用 Bangumi API 取其本人数据。

---

## 关键来源

- Bangumi：https://bangumi.github.io/api/ · https://github.com/bangumi/api · https://github.com/etherwindy/Bangumi-MCP (MIT) · https://github.com/aronnaxlin/bgm-cli (AGPL)
- 萌娘：`https://zh.moegirl.org.cn/api.php` · `/llms.txt` · `/robots.txt` · License https://creativecommons.org/licenses/by-nc-sa/3.0/cn/ · TextExtracts https://www.mediawiki.org/wiki/Extension:TextExtracts
- 数据集：https://www.kaggle.com/datasets/CooperUnion/anime-recommendations-database · https://github.com/bangumi-data/bangumi-data · https://github.com/mojimoon/bangumi-anime-ranking
