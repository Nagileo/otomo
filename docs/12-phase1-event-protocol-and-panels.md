# 12 · Phase 1 设计稿 · 结构化 state 事件协议 + 前端证据面板

> 配套 docs/11 §Phase 1。目标：把后端**已经返回**的 typed 结构化证据透到前端，做成对标 AG-UI 的事件协议 + 证据面板。
> 设计原则：**单点改后端、零改 api、前端按 `tool name` 分发**。先垂直打通一个面板，再水平铺开。

---

## 0. 现状（已核对代码，不是臆测）

| 层 | 现状 | 关键文件 |
|---|---|---|
| 事件契约 | `ObservationEvent` 只有 `name/ok/summary/sources/entities`，**无 typed data** | [contracts.py:123](../backend/otomo/agent/contracts.py#L123) |
| 工具结果 | `ToolResult.data` 是 typed model，`to_observation()` 已 dump 给 LLM（数据在管道里，只是没给前端） | [contracts.py:52](../backend/otomo/agent/contracts.py#L52) |
| emit 点 | `step_tools` 三 runner 共用，`result` 在手 → emit ObservationEvent | [_common.py:269](../backend/otomo/agent/_common.py#L269) |
| 另一 emit | langgraph_runner 单独一处 | [langgraph_runner.py:75](../backend/otomo/agent/langgraph_runner.py#L75) |
| SSE | `{"event": ev.type, "data": ev.model_dump_json()}` —— **加字段自动带出，api 不用改** | [app.py:86](../backend/otomo/api/app.py#L86) |
| 前端 | `handleEvent` switch by `ev.type`；`observation` 只存 `name/ok/summary` 进 trace；只有对话/trace 两栏 | [page.tsx:90](../frontend/app/page.tsx#L90) |

---

## 1. 后端改动

### 1.1 `contracts.py` — ObservationEvent 加 data + 新增 StateEvent
```python
class ObservationEvent(BaseModel):
    type: Literal["observation"] = "observation"
    name: str
    ok: bool
    summary: str
    sources: list[Citation] = Field(default_factory=list)
    entities: list[EntityRef] = Field(default_factory=list)
    data: dict[str, Any] | None = None   # ← 新增：typed result 的 json dump，前端按 name 知道 schema


# 对标 AG-UI 的 State Management 类（Otomo 唯一缺的一类事件）
class StateEvent(BaseModel):
    type: Literal["state"] = "state"
    scope: Literal["spoiler", "memory", "profile"]  # Phase 1 先打通 spoiler；memory→Phase 5
    snapshot: dict[str, Any] = Field(default_factory=dict)   # 当前完整状态（= AG-UI STATE_SNAPSHOT）
    # delta 留待后续；Phase 1 先只用全量 snapshot，简单可靠


AgentEvent = (
    PlanEvent | ToolCallEvent | ObservationEvent | ReflectEvent
    | AnswerDeltaEvent | FinalEvent | FollowupEvent | ErrorEvent
    | StateEvent   # ← 加入 union
)
```

### 1.2 `_common.py` — step_tools 把 data 塞进 ObservationEvent（**单点，三 runner 生效**）
```python
        yield ObservationEvent(
            name=tc.function.name, ok=result.ok, summary=summarize(result),
            sources=result.sources, entities=extract_entities(result),
            data=_panel_data(tc.function.name, result),   # ← 新增
        )
```
新增 helper（同文件）：
```python
# 哪些工具的 data 值得给前端做面板（白名单，避免把超大/无意义 payload 全推前端）
_PANEL_TOOLS = {"review_subject", "compare_user_taste", "season_guide_brief", "recommend_subjects"}

def _panel_data(name: str, result: ToolResult) -> dict | None:
    if name not in _PANEL_TOOLS or not result.ok or result.data is None:
        return None
    # 不能直接全量 dump；要按工具裁剪字段、限制评论/样本数量。
    return panel_data_from_payload(name, result.data.model_dump(mode="json", exclude_none=True))
```
> 决策：用**白名单 + 字段裁剪**而非全量——只有四个有面板的工具透 data，其余工具 `data=None`（前端照旧走 trace）。后续加面板就加进 `_PANEL_TOOLS`，并补对应裁剪函数。

### 1.3 `langgraph_runner.py` — 解析 ToolMessage 后透出 data
LangGraph 路径拿不到原始 `ToolResult`，只拿到 `ToolMessage.content`（即 `ToolResult.to_observation()` 的 JSON 字符串）。因此不能简单复用 `_panel_data(result)`；应解析 payload 后走同一套裁剪：
```python
payload = safe_json(str(m.content))
data = panel_data_from_payload(name, payload)
```
这样手搓 runner 与 LangGraph runner 都遵守同一套面板白名单/裁剪策略。

### 1.4 StateEvent 的发出（Phase 1 最小实现：spoiler snapshot）
在 runner `stream` 开头、`update_spoiler_state_from_input()` 之后、首个 LLM 调用前，若 `state.short_term.get("spoiler")` 非空，发一次：
```python
sp = state.short_term.get("spoiler") if state else None
if sp:
    yield StateEvent(scope="spoiler", snapshot={
        "mode": sp.get("mode", "none"),
        "progress_episode": sp.get("progress_episode"),
    })
```
> Phase 1 只要"State 这一类管道跑通"即可（spoiler 已有数据现成）。完整剧透 UI/followup 是 Phase 3，memory snapshot 是 Phase 5——它们都只是往这条已通的管道里加 scope。

### 1.5 api — **不用改**
`ev.model_dump_json()` 自动序列化 `data` 与新 `StateEvent`。前端会多收到 `event: state` 和带 `data` 的 `observation`。

---

## 2. 前端改动（page.tsx）

### 2.1 事件消费：observation 存 data、新增 state
```tsx
// 新增两个 state
const [evidence, setEvidence] = useState<Record<string, any[]>>({}); // name -> data[]（同名工具可多次调用）
const [spoiler, setSpoiler] = useState<{mode: string; progress_episode?: number} | null>(null);

// handleEvent 内：
case "observation":
  setTrace((t) => [...t, { kind: "obs", name: ev.name, ok: ev.ok, summary: ev.summary }]);
  if (ev.data) setEvidence((e) => ({ ...e, [ev.name]: [...(e[ev.name] ?? []), ev.data] })); // ← 新增
  break;
case "state":                                                        // ← 新增
  if (ev.scope === "spoiler") setSpoiler(ev.snapshot);
  break;
```
记得 `send()` 开头清空：`setEvidence({})`（`setSpoiler` 按需保留跨轮）。

### 2.2 渲染：对话栏 answer 下方插入证据面板，按 name 分发
```tsx
{evidence["review_subject"]?.map((x, i) => <ReviewEvidencePanel key={i} data={x} />)}
{evidence["compare_user_taste"]?.map((x, i) => <TasteAffinityPanel key={i} data={x} />)}
{evidence["season_guide_brief"]?.map((x, i) => <SeasonGuidePanel key={i} data={x} />)}
{evidence["recommend_subjects"]?.map((x, i) => <RecommendPanel key={i} data={x} />)}
```
顶栏 spoiler badge：
```tsx
{spoiler && <span className="badge">🚫剧透模式: {spoiler.mode}{spoiler.progress_episode ? ` · 看到第${spoiler.progress_episode}集` : ""}</span>}
```

### 2.3 四个面板组件 — 消费字段映射（来自各工具 result_model）

**ReviewEvidencePanel** ← `review_subject` / `ReviewFusionResult`
| UI 块 | data 字段 |
|---|---|
| 标题/类型/置信度 | `title` `subject_type` `confidence` `consensus` |
| 各源评分卡 | `ratings[]`：`source` `score`/`scale` `count` `signal`(strong/positive/mixed/weak) `rank` `url` |
| 方面褒贬条 | `aspect_summary[]`：`label` `dominant_sentiment` `positive`/`negative`/`mixed` `confidence` `spoiler_risk` |
| 来源矩阵 | `source_matrix[]`：`source` `role` `status`(used/hidden/unavailable/link_only) |
| 剧透提示 | `caveats[]` `spoiler_level` |

**TasteAffinityPanel** ← `compare_user_taste` / `TasteCompareResult`（核心在 `affinity`）
| UI 块 | data 字段 |
|---|---|
| 相似度雷达 | `affinity.rating_similarity` `collection_similarity` `user_space_similarity` `peer_space_similarity` `extreme_similarity` |
| 置信度 | `affinity.confidence` `confidence_reasons[]` `common_rated` |
| 共同高分/低分/分歧 | `affinity.liked_together[]` `disliked_together[]` `biggest_disagreements[]`（各项 `name`/`user_rate`/`peer_rate`/`image`） |
| 结论 | `affinity.explanation` |

**SeasonGuidePanel** ← `season_guide_brief` / `SeasonGuideBriefResult`
| UI 块 | data 字段 |
|---|---|
| 季度头 | `season` `personalized` `profile_tags[]` `notes[]` |
| 作品卡流 | `items[]`：`title` `image` `bangumi_score` `fit`(strong/maybe/wait) `reason` `match_tags[]` `broadcast` `studio` `pv_url` `official_url` `match_confidence` |
| 证据/弱匹配标注 | `items[].evidence[]`（含"弱匹配需谨慎引用"） |
| 导视入口 | `guide_videos[]`：`label` `up_name` `up_url` `confidence`；`guide_comment_digests[]`（可选） |

**RecommendPanel** ← `recommend_subjects` / `RecommendResult`
| UI 块 | data 字段 |
|---|---|
| 模式/依据 | `mode`(normal/niche/explore) `based_on_tags[]` `notes[]` |
| 推荐卡 | `items[]`：`name` `image` `bangumi_score` `score` `reasons[]` `explicit_tag_matches[]` `quality_badges[]` `review_consensus` |
| 外部映射 | `items[].external_mappings[]`：`source` `external_title` `mapping_confidence`（弱映射要弱化展示） |

> 组件骨架风格沿用现有 `src-card`（[page.tsx:170](../frontend/app/page.tsx#L170)）：卡片 + 缩略图 + 标签。signal/fit/confidence 用色块（strong/used=绿、mixed/maybe=黄、weak/wait/hidden=灰）。**生动 = 让 signal/confidence/弱匹配可见，不是炫特效。**

---

## 3. 落地顺序（先垂直一刀，再水平铺开）

1. **垂直切片（先跑通一条端到端）**：后端 1.1+1.2 → 前端 2.1 + **只做 ReviewEvidencePanel**。
   问 `如何评价孤独摇滚？`，确认前端收到 `observation.data` 并渲染出评分卡 + aspect 条。**这一步验证整条管道，最重要。**
2. **水平铺开**：补 TasteAffinity / SeasonGuide / Recommend 三个面板（字段映射见 §2.3）。
3. **State 类打通**：1.4 + 前端 2.1 的 `state` case + spoiler badge（为 Phase 3/5 铺管道）。
4. langgraph_runner 补 `data`（1.3），保持双 runner 一致。

---

## 4. 验收 & eval

**验收**
- [ ] 问评价/同步率/季番/推荐，前端出对应面板（不再只有文字+来源图）。
- [ ] 面板上能看到 `confidence`/`signal`/`fit`/弱匹配标注——**低置信不被文本掩埋**（呼应 docs/11 §0.2 主线）。
- [ ] 带 `spoiler_mode` 请求时，顶栏出 spoiler badge。
- [ ] 非面板工具 `data=None`，前端照旧只走 trace（不报错）。

**配套 eval（加进 `eval/cases.jsonl`，Phase 1.5）**
- `如何评价装甲恶鬼村正？` → `should_call: [review_subject]`，断言 `observation.data.ratings` 含 Bangumi+EGS/VNDB、`aspect_summary` 非空。
- `我和 sai 口味像不像？` → `compare_user_taste`，断言 `data.affinity.confidence_reasons` 非空。
- `2026年7月有什么适合我的番？` → `season_guide_brief`，断言 `data.items[].fit` 存在。

---

## 5. 不做 / 边界
- 不引入 AG-UI 库依赖（只借鉴事件分类，保持手搓契约可控）。
- 不重写 runner、不动召回/打分逻辑（那是 Phase 6）。
- data 走白名单（§1.2），不无脑全量推；超大 payload 后续可在 `_panel_data` 里裁字段。
- StateEvent 仅 snapshot、先 spoiler scope；delta / memory / profile 留给 Phase 3/5。
