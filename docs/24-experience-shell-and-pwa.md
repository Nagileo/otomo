# Otomo Experience Shell 与 PWA 设计

## 目标

这一阶段把 Otomo 从一组独立产品页升级为连续的个人 ACGN 工作台。新增能力必须满足三条约束：

1. 外观偏好不污染推荐记忆；壁纸原图不上传服务器。
2. 通知、保存视图和清单按 Bangumi OAuth 用户隔离，并可跨设备恢复。
3. PWA 只缓存应用壳和公开静态资源，绝不缓存带 Cookie 的私人 API 响应。
4. Web Push 使用长期固定的 VAPID 密钥；浏览器 endpoint 按 OAuth 用户隔离，过期 endpoint 自动清理。

## 信息架构

- 全局命令面板：`Ctrl/Cmd + K` 打开，统一承担页面跳转、Bangumi 条目搜索和快捷动作。
- 通知中心：读取长期记忆 inbox，支持单条已读和全部已读；订阅配置仍由订阅页负责。
- 追番快捷抽屉：复用 `/today`，允许查看今日/落后条目，并经过二次确认写回下一集。
- 比较托盘：最多保留 3 个条目，调用统一的 `compare_subjects` 产品接口；刷新页面后仍保留候选。
- 工作区：保存发现页筛选条件，维护跨媒介自定义清单；数据由 SQLite 按用户持久化。
- 任务中心：记录产品请求的运行、成功、失败和因刷新中断状态；失败任务可回到原页面重试。

## 外观系统

- 主题：跟随系统、浅色、深色。首屏内联脚本在 React 水合前设置 `data-theme`，避免闪烁。
- 壁纸：JPEG/PNG/WebP，最大 12 MB，存入 IndexedDB。支持开关、明暗遮罩、模糊和定位。
- 密度：舒适、紧凑，通过 `data-density` 调整列表与表单间距，不缩放字体。
- 可访问性：高对比度、减少动态效果；尊重系统 `prefers-reduced-motion`。

## 后端契约

- `GET /product/search`：统一条目搜索，供命令面板使用。
- `POST /product/compare`：2~3 个 Bangumi subject 的结构化比较。
- `GET /product/inbox`、`PATCH /product/inbox/{id}`、`POST /product/inbox/read-all`：通知读取状态。
- `/workspace/views`：保存、列出、删除命名视图。
- `/workspace/lists` 与 `/workspace/lists/{id}/items`：自定义清单与条目管理。

所有写接口要求 OAuth 登录与 CSRF；工作区记录使用 `owner_key=user:<username>` 隔离。

## PWA 与离线策略

- service worker 对导航使用 network-first，仅在网络失败时回退已缓存应用页面。
- `/_next/static/`、图标和 manifest 使用 stale-while-revalidate。
- `/api`、`/auth`、跨域后端、POST/PATCH/DELETE、含 `Authorization` 的请求不缓存。
- 离线时仍可打开曾访问的应用壳、查看本机外观与比较候选；需要账户数据的操作明确显示离线。
- Service Worker 同时接收 Web Push，并把通知点击统一带回订阅中心；生产环境必须使用 HTTPS。

## 验收

- 深浅主题首屏无闪烁，桌面与移动端均无横向溢出。
- 壁纸刷新后恢复，清除后 IndexedDB 不再保留 Blob。
- 命令面板键盘可达，搜索失败不会阻断本地命令。
- inbox 已读状态跨刷新保留；未登录用户看不到私人数据。
- 比较托盘最多 3 项，能渲染现有 CompareSubjectsPanel。
- service worker 不缓存任何私人 API JSON。
