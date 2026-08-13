# 22 · Today Cockpit 与推荐反馈飞轮

## 目标

这一阶段吸收社区“每日放送 + 喜欢/隐藏 + 越用越懂”的产品思路，但以 Otomo
现有 Bangumi OAuth、工具系统、长期记忆和推荐器为基底实现，不依赖或复制第三方脚本代码。

## Today Cockpit

固定入口：`/today`。

`TodayCockpitService` 是唯一领域服务，聊天工具、网页固定页和 `daily_airing`
订阅都应消费它。结果严格取以下交集：

1. Bangumi 本周放送日历；
2. 当前登录用户的在看/想看收藏；
3. 用户仅作用于日历的隐藏/置顶偏好。

隐藏不会写入推荐雷区。`本季隐藏` 带季度键，到下一季度自动失效；置顶保持。
“看完下一集”仍走 prepare → 用户确认 → execute 的 Bangumi 写回链路。

## 推荐反馈

每次 `recommend_subjects` 生成 `recommendation_set_id`，服务端保存当次请求与候选。
前端可直接记录：

- impression / open；
- wishlist / started / watched；
- more / less；
- dismiss，并区分不感兴趣、已看、题材、画风、节奏、长度和仅本次不要。

“换一批”调用 `/recommendations/next`，复用原请求并排除上批候选，不经过 LLM
重新解释用户意图。持久负反馈会参与后续排除；`temporary` 只保留 7 天；“已看过”
不被写成口味厌恶。

推荐卡片的“想看”继续沿用 prepare → confirm → execute 写回链路；只有 Bangumi
写回确认成功后才记录 wishlist 转化，取消或失败不会污染在线指标与长期画像。相反反馈
按条目采用最新明确选择，事件表仍保留历史用于审计。

线上指标由 `/recommendations/metrics` 提供，模型注册状态由
`/recommendations/models` 提供。SQLite 文件均位于 `cache/`，沿用 Docker cache volume。

## 排序与系列策略

排序先计算相关性，再在竞争候选头部执行 MMR。`diversity_strength` 可调整相关性和
多样性的平衡。系列去重使用 Bangumi relation 回溯得到的 root，而不只依赖标题正则。

`series_policy=auto` 按场景解释：

- general / gal_intro / cross_media：发现新坑，已看系列续作不挤占推荐位；
- season / backlog：允许适合继续追的续作；
- tonight：已有前作则保留后续，否则回到系列入口。

## 多媒介 CF 模型

运行时按 `i2i_{anime|book|music|game|real}.json` 加载；缺模型时该路召回明确跳过，
旧于 `CF_MODEL_MAX_AGE_DAYS` 时降权并在结果数据说明中提示。

离线目录提供可恢复流水线：

```powershell
cd recsys-offline
./refresh_models.ps1 -Start 1 -End 20000
```

Linux：

```bash
cd recsys-offline
START=1 END=20000 bash refresh_models.sh
```

采集器按媒介分别续传，原始用户交互只留本地；`train_all` 验证后原子发布聚合 i2i
和 `cf_models.json`。新数据写 `updated_at`，评测默认 temporal leave-one-out；旧 CSV
没有时间戳时明确降级为固定随机切分。

## 部署边界

- 新增状态仍使用 `./cache:/app/cache`，现有 Docker / GHCR / `deploy.sh` 链路无需新卷。
- i2i 模型随 backend 镜像发布；训练不在在线容器中执行。
- 公开部署应定期运行离线刷新任务，并在发布前检查 `/recommendations/models`。
