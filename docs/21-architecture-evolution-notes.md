# 21 · 架构演进实录（2026-07 W2）：工具披露 / 写回信任 / 评测闭环 / 数据飞轮

> 定位：**不是 roadmap，是记录**。docs/17–20 的规划在这一周全部落地收尾，但过程中出现了四个
> 规划里没有的架构级演进——工具上下文经济学、写操作信任模型、评测三层闭环、部署期数据飞轮。
> 本文写给两类读者：未来的自己（为什么当时这么改），以及阅读这个作品集的人（工程判断是怎么做出来的）。
> 所有数字都是真机实测，非估算。

---

## 1. 渐进式工具披露：96 个工具的上下文经济学

**问题**。docs/17–20 落地后注册工具达 96 个，[registry.openai_tools()](../backend/otomo/agent/registry.py)
无过滤地把全部 schema 塞进每轮 LLM 调用：实测 **77,552 字符 ≈ 26k token/轮**，一次多跳查询仅工具
schema 就烧掉 130–200k；且 function-calling 准确率随工具目录增长而下降（"热海查询狂调 14–16 个工具"
的 thrash 即症状），OpenAI/Anthropic/Google 还有 128 工具硬上限在前面等着。

**调研**（2026-07，联网核实）：业界统一原则是 progressive disclosure——Anthropic Tool Search Tool
（按需取 schema，省 85%）、RAG-MCP（向量预筛，选择准确率 3×）、Cloudflare Code Mode（工具包成 SDK）。

**选型判断**：不引入向量库。Otomo 的工具**本就按域组织**、adaptive runner 已有查询分类 router——
用"粗到细 + 逃生舱"落地即可（[tool_router.py](../backend/otomo/agent/tool_router.py)）：

- **CORE 23 个常驻**：实体图谱解析 + 高频意图入口 + 记忆读 + 剧透护栏 + 知识兜底；
- **15 个域工具组**：vision / watch_resource / pilgrimage / music / season_hot / memory_plan…，
  查询词法命中关键词才注入；
- **`load_tool_group` 逃生舱**：词法漏选时模型自己把组拉进上下文——**没有硬失败路径**，这是敢上线的关键。

**实测**：典型查询从 94 工具/25.5k token 降到 **23–32 工具/6.5–9k token（-65%～74%）**；
生产轨迹（cache/trajectories）里已观察到模型真实调用逃生舱后完成任务。附带优化：阶段 2 的
compose 调用不再发送工具 schema（本就 tool_choice=none）。

**沉淀原则**：分组的**覆盖完整性必须用测试锁死**（每个非写工具必须可达 CORE 或某组，见
test_tool_selector_coverage_and_subset）——渐进披露最大的风险不是慢，是把某个能力永久藏没了。

---

## 2. 写回信任模型：从"模型不可执行"到"口头确认即执行"

**v1（docs/17 设计）**：写工具标 `is_write`、对模型隐藏、`dispatch` 拦截，执行只能走前端按钮。
安全，但用户实测直接暴露了摩擦成本：批量加 9 部"在看"= 9 次按钮点击；用户说"你是不是没加入，
重新加入一下"时模型只会再 prepare 一个重复动作——待确认列表堆到 4 个、决策日志出现同一作品
重复写回、记忆面板在一条回复里叠了 4 份快照。**安全设计的摩擦成本必须在真实使用里校准。**

**v2（现行）**：护栏从"藏工具"下移为三层，换取"确认"语义在对话层完成：

1. **参数层**：`execute_bangumi_write_action(confirmed=true)` 必须显式传，且只能执行**已 prepare** 的动作；
2. **通道层**：默认 `dispatch()` 仍拦截写工具（HTTP 面兜底），仅 agent 循环 `allow_write=True`；
3. **策略层**（prompt）：只有用户**当前消息明确确认**（"确认/直接加/写回吧"）才可执行；
   "你没加入/再加一下"= 执行已有 pending（视为催促），**绝不重复 prepare**。

配套：prepare 按 `(operation, subject_id, payload)` 去重复用 pending；记忆快照是累积状态，
前端只渲染最新一份。真机验收：两轮对话"标记在看"→"确认，直接写回吧"，模型自主调用执行工具、
Bangumi 真实写回成功。

---

## 3. 评测三层闭环

| 层 | 测什么 | 不测什么 | 载体 |
|---|---|---|---|
| 单元/回归（136） | 工具内部逻辑、并发护栏、解析方向 | LLM 行为 | pytest，CI 每次 push |
| golden 行为验收（47） | agent 级路由/面板/越界（expect_tools / expect_panels / forbid_tools） | 易变事实（评分/热度） | eval/golden_cases.yaml，CI 手动 job |
| 离线推荐评测 | 召回通道的量化贡献（HR@K / NDCG@K） | 在线行为 | scripts/eval_recommend.py |

**golden 方法论**要点：断言行为不变量而非事实；多轮 TurnSpec 共享 AgentState（反馈闭环 case：
turn1 "别再推校园恋爱"→ turn2 金丝雀作品《败犬女主》必须消失——金丝雀按测试账号画像校准）；
**新招牌能力必带 ≥1 条 golden** 是硬规矩。

**离线推荐评测（R4）**：HoldoutClient 包装真实 client，使 hold-out 作品同时从画像构建与已看过滤中
消失（零生产代码改动）；续作是 easy win，按系列主干标记并单列 `HR@K(去续作)`。真机 3 试验：
纯标签 HR@10 0.267 → 全开 0.333，NDCG 0.410 → 0.459；归因链可指——《玉响》标签召回漏掉，
图谱通道经"系列构成·佐藤順一"捞回 @8，CF（"看过 more aggressive 的人也在看"）提到 @4。
**这是把 recsys-offline 的方法论复活到在线系统的证据。**

**评测基建的三个教训**（全部来自 CI 首跑翻车）：
1. **naive datetime 是时区炸弹**：`astimezone()` 按运行机器时区解释裸时间，本地 UTC+8 全绿、
   CI UTC 全红。修法：裸时间一律视为"规则所在时区的墙钟"（`replace(tzinfo=)`）。
2. **错误可见性先于重试**：DeepSeek 402 余额耗尽导致 26/47 后全部级联失败，而 runner 静默丢弃
   ErrorEvent——日志里一个错误字都没有。修：错误即时打印+计入结果、区分 infra 故障与断言失败、
   退避重试（402/401 不重试）、连续 3 条 infra 死亡即熔断（别对着空钱包烧 token）。
3. **CI 可以当 typechecker**：本机无 node 时，前端大重构（3418 行面板拆分）走分支 + PR，
   让 51 秒的 frontend job 做类型把关，绿了再合入——本地验证不了 ≠ 不能安全重构。

---

## 4. 部署期数据飞轮（docs/15 pre-RL 的桥）

RL 数据侧一直等 qwen3.5 级策略模型 + 算力，但**真实分布的轨迹语料不必等**——部署后的每轮对话
就是未来拒绝采样 / SFT / DPO 的原料（[trajectory.py](../backend/otomo/trajectory.py)）：

- **采集**：每轮完整 message 列表（含工具调用/观察，单条 6k 字符截断）+ 工具清单 + 真实 token
  用量 + owner 加盐哈希（伪匿名），落 `cache/trajectories/YYYY-MM-DD.jsonl`；失败静默，绝不影响对话。
- **反馈**：每条回答 👍👎（`/feedback/answer`，按 meta 事件下发的 turn_id 关联），匿名可反馈。
- **出口**：`python -m scripts.export_trajectories --sft --dpo`——SFT 剔除 👎、默认剥 system、
  脱敏（email / Bearer / URL 秘钥 / webhook）；DPO 把同一问题的 👍/👎 回答配成偏好对。

设计取舍：turn_id 不持久化进 session store（重载后的历史消息不能补反馈）——v1 接受，反馈本就
应该在阅读答案的当下发生。

---

## 5. 产品面四件（简记）

- **匿名冷启动速配**：recommend_subjects guest 模式（无 token 时跳过收藏/画像/图谱/CF/记忆，
  纯会话标签 + 冷启动标签召回）+ 欢迎屏 3 问速配 chips。行业调研确认 "no-signup 第一分钟体验"
  是 2026 同类产品的质量线。prompt 硬规则：**绝不以"需要登录"拒绝推荐**。
- **MCP Server**（[mcp_server.py](../backend/otomo/mcp_server.py)）：31 个只读公共知识工具经
  MCP stdio 暴露给 Claude Desktop / Cursor；openai schema → MCP inputSchema、执行复用
  registry.dispatch，零重复实现。**用户态（记忆/写回/个性化）刻意不暴露**——外部宿主没有
  Otomo 的用户会话概念。
- **年度 Wrapped**：monthly_watch_report 加 `period=year`（时间窗参数化，分区标题随动）+
  前端 canvas 年度卡导出 + `yearly_wrapped` 分享页类型。
- **UX 原则沉淀**：面板按**交付物 vs 佐证**分治——交付物（报告/驾驶舱/档案/图谱/对比/巡礼行程）
  默认展开 + 醒目可收起栏（收起后是全宽高亮入口，绝不缩成灰 chip）；佐证维持 chip 折叠。
  新问题锚顶滚动（流式回答在视口内展开）。

---

## 6. 踩坑登记簿（本周新增）

| 坑 | 根因 | 沉淀原则 |
|---|---|---|
| CI 26/47 后全级联、日志零错误 | runner 静默丢 ErrorEvent；DeepSeek 402 | 错误可见性先于一切重试逻辑 |
| 本地全绿 CI 全红 | naive datetime 按机器时区解释 | 调度语义时间一律显式时区 |
| Wrapped 卡按钮在、函数没进文件 | 追加守卫被"按钮引用了函数名"误触发 | 存在性守卫要检查**定义**而非字符串 |
| 分享页 SSR fetch failed | server component 用了浏览器相对路径 /api | SSR 与浏览器的 BACKEND 必须分开配（INTERNAL_BACKEND） |
| 公开分享页 404 风险 | Caddy 裸 /share/* 会把前端页面路由劫给后端 | 反代规则里写明"前端路由与 API 前缀不许裸段重叠" |
| eval 污染真实用户画像 | build_registry(ltm=None) 落到真实 cache/ltm | eval 一律一次性沙箱 LTM |
| "记忆分裂"疑云 | 两个 Bangumi 账号（网页 OAuth vs .env token）各一份记忆 | 设计使然；排查时先核对身份键再怀疑存储 |

---

## 7. 当前状态与下一步

**状态**：136 单元测试绿；47 条 golden（自余额中断后未完整重跑）；CI backend+frontend 绿；
轨迹飞轮已在真实使用中落盘（含逃生舱调用记录）；👍👎 反馈链路待真实点击验证。

**排序**：golden 全量重跑验收本周改动 → 部署（nip.io 方案就绪，见 deploy/README；share 链接 /
订阅推送 / 匿名速配第一分钟体验 / MCP 名片全部在等公网 URL）→ 部署后项（Web Push、dashscope ASR、
听歌识曲、点子池）。
