# backend/otomo/data

## i2i_anime.json
在线 `recommend_subjects` 的**协同召回 provider** 数据——item-item 相似度表。

- **来源**：由 `recsys-offline` 自采的 Bangumi 用户公开收藏，训练 ALS 后用 `similar_items` 导出。
  详见 [recsys-offline/README](../../../recsys-offline/README.md) 的「Bangumi 原生 CF」一节。
- **生成方式**：
  ```bash
  cd recsys-offline
  python -m recsys_offline.run_bangumi_cf --export-model als
  cp data/bangumi/i2i_anime.json ../backend/otomo/data/
  ```
- **隐私**：仅含「Bangumi `subject_id` → 相似 `subject_id` + 分数」的**聚合结果，不含任何 user_id / 用户隐私**。
  原始 user-item 收藏数据仅本地训练用、已 gitignore、不发布。
- **格式**：`{"meta": {…}, "items": {"<subject_id>": [[nbr_id, score], …]}}`，key 与在线 Bangumi ID 一致。
- 文件缺失时，在线协同召回这一路静默跳过（优雅降级，不影响标签/图谱召回）。
