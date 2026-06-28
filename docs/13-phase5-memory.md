# 13 · Phase 5 设计稿 · Memory v1（显式结构化 + consolidation）

> 配套 docs/11 §Phase 5。目标：长期记住对话中的**软偏好/避雷/剧透偏好/推荐反馈**——Bangumi 收藏负责"看过什么"，Otomo memory 负责"对话里说的偏好"。
> 设计原则：**显式结构化 JSON（不上向量库）；每条带 `source`+`confidence`；写入走 consolidation（ADD/UPDATE/DELETE/NOOP）而非 append。**

---

## 0. 现状（已核对代码）

| 层 | 现状 | 文件 |
|---|---|---|
| 存储 | `LongTermMemory` 只有 `get/set(namespace, key)` → JSON 文件 | [memory/store.py](../backend/otomo/memory/store.py) |
| 注入点 | `runtime_state_prompt/events` 只注入 spoiler；StateEvent 已支持 `scope="memory"` | [_common.py:81](../backend/otomo/agent/_common.py#L81) / [contracts.py:137](../backend/otomo/agent/contracts.py#L137) |
| 装配 | `build_registry(client, moegirl, ltm)` 有 ltm 参数，**但 `build_runner`/`app.py` 没传 → 每次新建、未共享** | [factory.py:88](../backend/otomo/factory.py#L88) |
| profile | 构造接了 `ltm` 但**没存没用** | [profile/tool.py:40](../backend/otomo/tools/profile/tool.py#L40) |
| 前端 | `StateEvent scope="spoiler"` 已消费；memory 待加 | [page.tsx](../frontend/app/page.tsx) |

> 结论：管道（StateEvent memory scope、ltm 参数）都预留好了，Phase 5 是**把它接通 + 加 typed schema + consolidation + 工具**。

---

## 1. 数据模型 — `memory/models.py`（新建）

```python
from typing import Literal
from pydantic import BaseModel, Field

MemSource = Literal["explicit_user", "bangumi_profile", "derived_from_feedback"]
SpoilerDefault = Literal["none", "mild", "full"]

class MemoryItem(BaseModel):
    value: str                       # 如 "百合" / "党争"
    source: MemSource = "explicit_user"
    confidence: float = 0.6          # 推断的低、明说的高
    ts: str = ""                     # ISO8601

class FeedbackItem(BaseModel):
    subject_id: int | None = None
    name: str = ""
    signal: Literal["like", "dislike", "more", "less"]
    note: str = ""
    source: MemSource = "explicit_user"
    confidence: float = 0.8
    ts: str = ""

class UserMemory(BaseModel):
    username: str
    likes: list[MemoryItem] = Field(default_factory=list)
    dislikes: list[MemoryItem] = Field(default_factory=list)
    spoiler_default: SpoilerDefault = "none"
    progress: dict[str, int] = Field(default_factory=dict)        # 作品名 → 看到第几集
    feedback: list[FeedbackItem] = Field(default_factory=list)
    affinity_cache: dict[str, dict] = Field(default_factory=dict) # peer_username → 同步率摘要
    profile_snapshot: dict = Field(default_factory=dict)          # get_taste_profile 摘要
    updated_at: str = ""
```

> `source`+`confidence` 是 0.2 主线在 memory 的落点：明说=高置信，推断=低置信、不当事实。

---

## 2. `store.py` 扩展 — typed user memory 读写

在 `LongTermMemory` 加（复用现有 `get/set`，namespace 固定 `"user_memory"`，key=username）：
```python
from .models import UserMemory

class LongTermMemory:
    ...
    def load_user(self, username: str) -> UserMemory:
        raw = self.get("user_memory", username)
        return UserMemory.model_validate(raw) if raw else UserMemory(username=username)

    def save_user(self, mem: UserMemory) -> None:
        mem.updated_at = _now_iso()
        self.set("user_memory", mem.username, mem.model_dump(mode="json", exclude_none=True))
```

---

## 3. Consolidation — `memory/consolidate.py`（新建，**Phase 5 的核心**）

对标 Mem0：写入不是 append，而是先**比对已有 → 决策 ADD/UPDATE/DELETE/NOOP**。v1 用词法匹配（精确/包含），语义匹配留后续。

```python
Action = Literal["ADD", "UPDATE", "DELETE", "NOOP"]

def _match(items: list[MemoryItem], value: str) -> MemoryItem | None:
    v = value.strip()
    return next((it for it in items if it.value == v or v in it.value or it.value in v), None)

def consolidate_preference(
    mem: UserMemory, polarity: Literal["like", "dislike"], value: str,
    source: MemSource, confidence: float,
) -> Action:
    """喜欢/不喜欢的一致化：处理'改主意'(反向列表移除) + 去重(UPDATE) + 新增(ADD)。"""
    same = mem.likes if polarity == "like" else mem.dislikes
    opposite = mem.dislikes if polarity == "like" else mem.likes

    # 1) 用户改主意：反向列表里有同义项 → 删掉（"喜欢百合"后"不想看百合了"）
    opp = _match(opposite, value)
    if opp:
        opposite.remove(opp)

    # 2) 同列表已有 → 取较高 confidence + 刷新来源/ts（UPDATE）；完全相同且更低置信 → NOOP
    cur = _match(same, value)
    if cur:
        if confidence > cur.confidence or source == "explicit_user":
            cur.confidence = max(cur.confidence, confidence)
            cur.source = source
            cur.ts = _now_iso()
            return "UPDATE"
        return "NOOP" if not opp else "DELETE"   # 仅因移除了反向项也算有变更

    # 3) 新增
    same.append(MemoryItem(value=value.strip(), source=source, confidence=confidence, ts=_now_iso()))
    return "ADD"
```

> 关键效果："喜欢百合" → likes;　随后"最近不想看百合" → 从 likes 删、加进 dislikes（**不是两条矛盾记录**）。这是面试官会追问的点。

---

## 4. 四个工具 — `tools/memory/tool.py`（新建）

| 工具 | 入参 | 行为 |
|---|---|---|
| `get_user_memory` | `username?` | 读 `UserMemory` → 返回 + 发 `StateEvent(scope="memory")`；agent 推荐/评价前调它拿长期偏好 |
| `remember_user_preference` | `kind`(like/dislike/spoiler/progress), `value`, `subject?`, `episode?`, `username?` | 走 consolidation 写回；like/dislike→`consolidate_preference`，spoiler→`spoiler_default`，progress→`progress[subject]=episode` |
| `forget_user_memory` | `kind`, `value?`, `username?` | 删除指定偏好/某项/清空某类；保证可遗忘（红线） |
| `record_recommendation_feedback` | `subject_id?`/`name`, `signal`(like/dislike/more/less), `note?`, `username?` | 追加 `FeedbackItem`；v1 只存反馈，**派生偏好（note→aspect）留 Phase 6 ABSA** |

- `username` 不传 → 用当前 token 账号（`get_me`），与 profile/recommend 一致。
- 都 `is_write=False`（A1 只读边界外的"记忆写"先放行；记忆只写 cache/ltm，不写 Bangumi）。
- 工具结果带 `sources=[]`（memory 非外部源），但 `get_user_memory` 额外触发 StateEvent。

`build_memory_tools(client, ltm)` → 注册进 factory。

---

## 5. 接入 Agent

### 5.1 共享 ltm 实例（修现状 gap）
- [app.py](../backend/otomo/api/app.py)：`app.state.ltm = LongTermMemory()`；`build_registry(..., app.state.ltm)`。
- [factory.py](../backend/otomo/factory.py)：`build_registry` 把 `ltm` 也传给 `build_memory_tools(client, ltm)` 和 `build_profile_tools(client, ltm)`；`build_runner` 签名加 `ltm` 透传。

### 5.2 注入（两条腿，先工具后自动）
- **主路（v1）**：prompt 引导 agent **主动调 `get_user_memory`**——推荐/评价/"按我口味/别推X"类问题前先拿长期偏好。该工具发 `StateEvent(scope="memory")`，前端可见。
- **增强（可选）**：runner 开头若已知 username（token 账号），`runtime_state_prompt` 注入一行 memory 摘要（"长期偏好：百合/日常；避雷：党争；默认无剧透"）。需要在 runner 异步拿 username——v1 可暂缓，靠主路。

### 5.3 prompts.py 增补（引导）
- 推荐/评价前先 `get_user_memory`；命中避雷项要在推荐里排除/降权并说明。
- 用户出强信号（"我喜欢X/别推X/以后别剧透/我看到第N集"）→ `remember_user_preference`。
- 用户对推荐表态（"这个不错/不想看这种"）→ `record_recommendation_feedback`。
- 边界：只记 ACGN 偏好；不臆测；推断偏好标低置信。

### 5.4 recommend 接入避雷（与 Phase 6 衔接的最小钩子）
`recommend_subjects` 在 Rerank 阶段读 memory 的 `dislikes` 做**避雷惩罚**（命中则降权）。v1 给个最小实现：run 开头 `mem = ltm.load_user(username)`，对候选 tags 命中 dislikes 的减分。（完整 aspect 雷区留 Phase 6。）

---

## 6. 前端（page.tsx + evidence-panels.tsx）

- `handleEvent` 的 `state` case 加 `scope==="memory"` → 存 `memory` state。
- 顶栏/侧栏一个 `MemoryBadge`：显示 likes/dislikes（前 3）+ spoiler_default。点击可展开 `MemoryPanel`（likes/dislikes/feedback 列表，每项标 source/confidence 色块——复用 `clsBySignal`：explicit_user=绿、derived=灰）。
- 与 source/confidence 主线一致：**推断的偏好视觉上弱化**（灰、标"推测"）。

---

## 7. 落地顺序 + 验收 + eval

**顺序（先纯逻辑、可单测，再接线）**
1. `models.py` + `store.py` 扩展 + `consolidate.py` → **加 pytest 单测**（consolidation 的 ADD/UPDATE/DELETE/NOOP + "改主意"场景，纯函数零网络）。
2. 四个工具 + `build_memory_tools`。
3. factory/app ltm 共享 + 注册。
4. prompts 引导 + recommend 避雷钩子。
5. 前端 MemoryBadge/Panel。
6. eval case。

**验收**
- [ ] "我喜欢百合" → likes 有"百合"(explicit_user, 高置信)；随后"最近不想看百合" → likes 移除、dislikes 新增（consolidation 生效，不矛盾累积）。
- [ ] "以后别给我剧透" → `spoiler_default="none"` 持久化，跨会话生效。
- [ ] "别再推党争番" → dislikes 有"党争"；之后 `recommend_subjects` 结果里党争 tag 候选被降权/排除。
- [ ] `get_user_memory` 触发前端 MemoryBadge 显示，推断项标灰。
- [ ] `forget_user_memory("dislike","党争")` 后该项消失。

**eval（加进 golden_cases.yaml，复用 Phase 1.5 机制）**
```yaml
- id: mem_remember_dislike
  question: 我特别讨厌党争番，以后别给我推这种。
  expect_tools: ["remember_user_preference"]
  note: 强避雷信号应写入 memory（dislike 党争）
- id: mem_recall_panel
  question: 看看你记住了我哪些口味偏好？
  expect_tools: ["get_user_memory"]
  expect_panels: ["get_user_memory"]
  note: 应调 get_user_memory 并产出 memory 面板
```

---

## 8. 边界 / 红线（硬约束）
- **只记 ACGN 推荐/评价相关**：偏好/避雷/剧透/进度/反馈/同步率；不记敏感个人信息。
- **必须可遗忘**：`forget_user_memory` 是一等公民。
- **推断低置信**：`derived_from_feedback` 的项 confidence≤0.5，不当事实、可被显式覆盖。
- **provenance**：每条带 `source`，回答用到 memory 时可溯源。
- **隐私**：memory 落 `cache/ltm`（gitignored）；evolving memory 有"错误累积/敏感滞留"风险（Mem0 governance），故 forget + 低置信 + 只记 ACGN 是底线。

---

## 9. 不做 / 留后续
- 向量记忆 / LLM 语义去重（v1 词法匹配够用）。
- note→aspect 偏好派生（留 Phase 6 ABSA）。
- runner 自动注入 memory（v1 靠工具主路；增强再做）。
- 完整 aspect 雷区惩罚（Phase 6）。
