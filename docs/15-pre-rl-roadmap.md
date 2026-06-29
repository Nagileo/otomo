# 15 · Agentic-RL 前置工作路线（不训练，用 DeepSeek v4 API 做数据侧）

> 来源：2026-06-29 用户 + 朋友按主路径通读代码后的讨论。
> 共识：现在最该做的**不是再接几个网站**，而是把 Otomo 从"会回答"推进到"**真正可用的 ACGN 生活助手**"，同时把 Agentic-RL 的前置（可操作环境 / 逐条 reward / 轨迹数据 / 行为评测）打牢。
> RL 本身仍缓（开源 policy 弱于 DeepSeek-v4、A100 未就绪）；**数据与 reward 侧先用 DeepSeek v4 API 做**，把后训练从"空中楼阁"变成有地基。承接 docs/11（Phase 1–6 已闭环），细化 Phase 7 的前置。

---

## 0. 定位

**现状盘点（已很完整）**：Bangumi 图谱 / 多源 review / 推荐(画像+aspect+协同) / 季番导视 / B站导视+评论 / EGS·VNDB·MusicBrainz·AniList / 用户画像 / 同步率·好友推荐 / 弃坑分析 / 长期记忆 / 剧透约束 / 证据面板 / golden eval / 探索网络·评分预测·萌点检索·分集雷达。~50 工具、Phase 1–6 闭环。

**缺的不是数据源，是四件让 RL 成立的东西**：
1. **可操作闭环**——现在只读不写，推荐完停在"你可以看这些"，没有"帮你加入想看 / 排观看计划"。
2. **逐条 reward**——回答里的事实还没 claim 级绑定证据，没法给"每条事实对不对"打分。
3. **轨迹数据**——query/tools/obs/answer/反馈没存成可训练/可评测格式。
4. **主动性与体验**——只被动应答；前端信息杂乱、图片堆页尾。

> 这四件**全是 Agentic-RL 的前置**：RL 缺的从来不是算法，是稳定的环境、证据、反馈、评测。把它们做好，DeepSeek v4 API 就能开始产出 RL 数据。

---

## 1. 五条工作线（按"为 RL 铺什么"组织）

| 线 | 为 RL 铺什么 | 子项 |
|---|---|---|
| **A 闭环与状态** | 真实可操作环境（action space） | A1 Bangumi 写回（强制确认）· A2 计划板 / 决策日志 |
| **B Reward 信号** | 每个行为可评分 | B1 Claim-level 证据校验 · B2 系统化行为评测 |
| **C 数据生产** | 用 DeepSeek v4 API 产 RL 数据 | C1 轨迹采集 · C2 rejection sampling 管线 |
| **D 能力补全** | 更强的环境 / 状态 | D1 多模态识番 · D2 B站字幕/按需摘要 · D3 媒介专家 · D4 收藏仪表盘 · D5 主动周报 |
| **E 体验** | demo 与可用性 | E1 前端信息架构重构 · E2 延迟/流式体验 |

---

## 2. 各项详述

### A1 · Bangumi 写回能力（带强制确认）
**目标**：在用户确认后写回 Bangumi——标记想看/在看/看过/搁置/抛弃、更新看到第几集、打分、写短评、把推荐加入想看、批量整理 backlog。让产品闭环（"要不要我帮你加入想看 / 生成本周计划"）。

**现状 / API（回答"有 api 吗、写错代价大"）**：
- **Bangumi v0 有写接口**：`POST /v0/users/-/collections/{subject_id}`（新建/改收藏：`type`/`rate`/`comment`/`tags`/`ep_status`/`private`）、`PATCH /v0/users/-/collections/{subject_id}`（改已有）、`PATCH /v0/users/-/collections/-/episodes/{episode_id}`（单集进度）。需要 **带写权限的 access token**（你的个人 token 在 next.bgm.tv 生成时即含读写）。
- client 底层 `_request_json(method, …, json_body=…)` **已支持 PATCH/POST**，写回不用改底层。
- **写错可逆但有代价**：能再改回，但会污染收藏、可能触发时间线动态。

**关键设计（硬约束）**：
- 工具 `is_write=True`；**前端二次确认**：写前弹"将把《X》标记为想看 / 打 8 分，确认？"，用户点确认才执行。
- **审计 + 可撤销**：每次写记录旧值到 decision_log，支持"撤销上一步"。
- 失败/冲突优雅返回；token 无写权限时明确提示。

**风险**：误写是主要风险 → 确认弹窗是不可跳过的门槛；先只放"加入想看 / 更新进度 / 打分"几个低风险动作，批量整理后做。

### A2 · 持久化计划板 / 决策日志
**目标**：Otomo 自己的"计划层"，长期追踪而非停在对话里。
- `watch_plan`：待看 / 在看 / 补番 / 搁置复活
- `recommendation_list`：某次推荐的候选、理由、**被拒原因**
- `decision_log`：用户为什么接受 / 拒绝某部（**这是 RL 的偏好信号金矿**）
- `season_watchlist`：本季追番表
- `backlog_cleaner`：想看太多时按口味/时长/完结度帮你排优先级

**现状**：长期记忆 store（`memory/`）已有结构化 JSON + consolidation，**扩字段即可**，不用新建存储。
**与 RL**：decision_log 的"接受/拒绝 + 理由"直接是偏好对（chosen/rejected），喂 DPO。

### B1 · Claim-level 证据校验（**RL 过程 reward 的核心**）
**目标**：最终回答里**每条事实 claim 绑定证据来源**——"制作公司=A-1 Pictures"必须来自 Bangumi staff；"中文圈口碑"来自短评；"日本 gal 圈"来自 EGS；"国际 VN 圈"来自 VNDB；"我推测你会喜欢"标为**推理**、不伪装成事实。明显减少"银之匙说成 8-bit"这类错。

**回答你的时效性疑问**——分两层，都能解：
- **校验延迟**（怕逐条查证拖慢）：**不额外查**。做"回答后对齐"——把答案抽出的 claim **对齐到本轮已有的 observation 证据**（工具这一轮已经查到的数据），匹配上=有据、匹配不上=标推测/降级。零额外 API、快。
- **数据时效**（评分会变）：claim 绑定的是"**本次查询的证据快照 + 时间戳**"，评分类 claim 标"截至查询时"。不承诺永久正确。

**关键设计**：复用图谱级 verifier（canonical 实体）+ source_matrix；新增"答案 claim → observation 证据"对齐层；输出每条 claim 的 `{source, confidence, supported}`。
**与 RL**：claim 支持率 = 过程 reward（事实正确 + 不幻觉 + 来源对）。

### B2 · 系统化行为评测
**目标**：比 golden cases 更像产品回归 + RL 评测。补这些维度：
- 多轮剧透状态是否保持（"看到第5集"后不查后续分集）
- 推荐约束（避雷/critique）是否真生效
- EGS/Bangumi 对齐是否错配
- B站/yuc 挂了是否优雅降级
- memory 删除后是否不再引用
- 事实 claim 是否都有证据
**现状**：golden harness（`eval/`）+ forbid_tools/expect_panels 已有，扩"多轮/状态/降级/claim"维度即可。

### C1 · RL 轨迹采集
**目标**：把每次交互存成结构化轨迹：`{query, tool_calls, observations, answer, verifier_metrics, user_feedback}` → jsonl。这是 DAPO/GRPO 的训练/评测格式。
**现状**：已有本地 trace JSONL + Langfuse 钩子（`obs/`），扩成完整轨迹 schema + 落盘即可。
**红线**：脱敏（不存 token/隐私）；用户可关。

### C2 · rejection sampling 管线（**用 DeepSeek v4 API**）
**目标**：不训练，先**产数据**——DeepSeek v4 API 对一批 query 生成**多候选轨迹** → 用 B1/verifier 筛高质量（claim 全有据 / set-F1 高 / 无越界剧透）→ 沉淀成 SFT/DPO 数据集。
**与 RL**：等开源 policy + A100 就绪，直接拿这批数据冷启动 SFT、再 DPO/GRPO。**这是"靠 API 做 RL 前置"的生产线。**

### D1 · 多模态识番
**目标**：截图 → 识别角色/作品/字幕 OCR/可能是哪一集 → 落 `subject_id`/`character_id` → 接现有图谱与讨论。
**关键**：**只调现成 VLM**（Claude / GPT-4V / Qwen-VL API），**不训练**；识别结果回锚到 Bangumi canonical（识别是入口，事实仍走图谱）。前端加截图上传。

### D2 · B站字幕 / 按需 URL 摘要
**目标**：补"现在只有链接、没摘要、链接还不一定对"的短板。
- B站公开字幕 / ASR → 导视/漫评**内容摘要**（不只元数据/评论）。
- **按需 URL 摘要**：给定 URL（百合会/S1/NGA/小红书…）读单页 → 摘要，**严格标 discourse source、不参与事实判断**。
- **不做**大规模全文 RAG 抓站（风险/噪声/维护高）；按需读单页够用。

### D3 · 媒介推荐专家深化
**目标**：anime / comic / light novel / music / galgame 不共用一套排序。Phase 6 已分型（book/music 分型 + galgame 三源），继续强化——尤其 **galgame 的 Bangumi + EGS + VNDB 融合**做成招牌。

### D4 · 收藏分析仪表盘（用户"一定要有"）
**目标**：年度观看、评分严格度、标签漂移、最爱 staff/CV/studio、弃坑分布、媒介偏好。
**现状**：`build_taste_report` + 画像数据已有，**做前端仪表盘可视化**（雷达/趋势/分布图）即可，后端补聚合。

### D5 · 主动周报（内容先，推送后）
**目标**：在追的番本周更新了哪些、想看里哪些开播了、7月新番哪些合口味、高同步好友最近高分了啥、每周观看总结。
**回答"是不是像 openclaw、架构合理吗"**：
- 拆两步：**周报内容生成**（一个聚合工具：读在追/想看/放送/好友）——**现在架构就能做**（按需，用户问"这周看什么/我的本周总结"时生成）。
- **主动定时推送**（cron + 通知渠道）——需要常驻 scheduler（APScheduler/cron）+ 推送（前端轮询/邮件/webhook），这是**新架构组件**，**排到后面**（demo 阶段先做按需周报，部署后再做主动推送）。

### E1 · 前端信息架构重构（你提的"杂乱"）
**目标**：
- **图片 inline**：作品封面掺进文字/卡片中（如推荐项、评价对象旁），不只堆页尾 sources。
- **trace 分级**：默认给用户**简化视图**（在做什么），开发者细节（args/raw observation）折叠/开关；隐藏不该展示的内部字段。
- 信息密度收敛：证据面板按"答案 → 支撑证据"组织，而非平铺所有工具输出。

### E2 · 延迟 / 流式体验
**目标**：推荐类查询要十几秒——给**流式中间反馈**（"正在召回/打分/补证据…"）、骨架屏/进度，而非干等。recommend 的 enrich 可并发（docs 早提过）。

---

## 3. 优先级（三波）

- **第一波（闭环 + reward + 体验地基）**：A1 写回(带确认) · A2 计划板/decision_log · B1 claim 校验 · E1 前端重构。
  → 拿到"可操作环境 + 逐条 reward + 能看的界面"，这是 RL 数据有意义的前提。
- **第二波（数据 + 评测 + 高价值能力）**：C1 轨迹采集 · B2 评测扩充 · D4 仪表盘 · D5 周报内容 · E2 延迟体验。
- **第三波（更强环境 + 数据生产）**：D1 多模态 · D2 字幕/按需摘要 · D3 媒介深化 · C2 rejection sampling（DeepSeek API 产 RL 数据）。
- **缓**：D5 主动推送(cron/通知)、大规模全文 RAG、实际 RL 训练(等 policy+A100)。

> 依赖：A1 是 A2(写回执行)、D5(周报里"帮你加想看")的基础；B1 是 C2(rejection 筛选)的 reward 函数；C1 是 C2 的数据来源。所以 **A1 + B1 + C1 是三个地基**。

---

## 4. 明确不做 / 缓（与朋友一致）
- 不大规模抓百合会/S1/NGA/贴吧/小红书/Pixiv/X 做全文 RAG——风险、维护、噪声都高；改为**按需 URL/搜索摘要**，严格标 discourse source、不参与事实判断（见 D2）。
- 不现在上 RL——先把 A1/B1/C1 地基打牢；缺的是稳定轨迹/证据/反馈/评测，不是训练算法。
- 多模态**只调现成 VLM、不训练**。

## 5. 与 Agentic-RL 的接口
- **环境**：A1 写回 + A2 计划板 = action space 与 state；B2 评测 = 环境正确性。
- **reward**：B1 claim 证据支持率（过程）+ 既有 set-F1/路径有效率（结果）+ source routing 合理命中 + 剧透不越界 + aspect 命中 + decision_log 偏好。
- **数据**：C1 轨迹 + C2 DeepSeek v4 API rejection sampling → SFT/DPO 冷启动数据。
- 等 policy（更强开源模型）+ A100 就绪 → 直接接 DAPO/GRPO，前置全部复用。
