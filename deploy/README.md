# Otomo 部署手册（没服务器 / 不买域名版）

三件事分开看：**CI 现在就能跑（不用服务器）**、**没买服务器时怎么先预览**、**买了台小机器后怎么免域名上线**。

---

## 0. 现在就能做：CI 验证（零成本，无需服务器）

`.github/workflows/ci.yml` 已就绪。把代码 push 到 GitHub，Actions 会自动：
- `backend`：`pytest -m "not external"`（纯逻辑测试）
- `frontend`：`npm ci && npm run build`（**这就是本地验证不了的前端 build——CI 替你验**）
- `golden-eval`：手动触发（Actions 页面 "Run workflow"），需在仓库 Settings→Secrets 配 `LLM_API_KEY` 等，跑招牌能力回归、出 `golden-report.json` artifact。

> 光是把仓库推上去开 Actions，就能回答"前端到底能不能 build"——这是上线前最该先做的一步。

---

## 1. 没服务器时：Cloudflare Quick Tunnel（临时公网 URL，任何机器都行）

想给朋友发个链接看看、或面试演示，连服务器都不用买——在**任何**能跑 Docker 的机器（甚至你自己电脑）上：

```bash
cp deploy/production.env.example backend/.env   # 填 LLM_API_KEY 等；URL 相关先留默认
docker compose --profile tunnel up -d --build
docker compose logs -f cloudflared              # 日志里会打印 https://xxxx.trycloudflare.com
```

- Cloudflare 免费给一个 `*.trycloudflare.com` 的 HTTPS 公网 URL，**不用开端口、不用公网 IP、不用域名**（隧道是往外拨的）。
- 缺点：URL **每次重启都会变**，所以 Bangumi OAuth 登录会不稳（回调地址对不上）。演示够用；要长期稳定登录就上第 2 节。
- 用了隧道，`.env` 里 `FRONTEND_BASE_URL` / `CORS_ALLOWED_ORIGINS` 填当次打印的 trycloudflare URL；`COOKIE_SECURE=true`。

---

## 2. 买了台小机器后：nip.io 免域名 + 真 HTTPS（推荐长期方案）

**关键点：不用买域名也能拿到真 Let's Encrypt 证书**——靠 `nip.io` 这个免费通配 DNS。
`1-2-3-4.nip.io` 会自动解析到 IP `1.2.3.4`，Caddy 就能为它申请真证书。

### 2.1 买什么机器
- 一台有**公网 IP**的最便宜 VPS 即可（2c/2G 足够；阿里云轻量香港/海外、或任意 $5 VPS）。
- 香港/海外节点的好处：**免 ICP 备案**（你不想买域名多半也不想备案），且 pixiv 等能直连。
- 安全组/防火墙放行 **80 和 443**。

### 2.2 上线步骤
```bash
# 服务器上
git clone <your-repo> && cd otomo
cp deploy/production.env.example backend/.env
# 编辑 backend/.env：把 1-2-3-4 换成你的公网 IP（点→横线），填 LLM_API_KEY、AUTH_ENCRYPTION_KEY

export OTOMO_DOMAIN=1-2-3-4.nip.io     # 你的 IP.nip.io
export COOKIE_SECURE=true
docker compose up -d --build           # 起 backend + scheduler + frontend + caddy
```
Caddy 自动为 `1-2-3-4.nip.io` 申请证书，几秒后 `https://1-2-3-4.nip.io` 就能访问，全程免域名、免备案、免费证书。

### 2.3 OAuth（让登录能用）
- Bangumi 开发者后台把「回调地址」设成 `https://1-2-3-4.nip.io/auth/bangumi/callback`，和 `.env` 里 `BANGUMI_OAUTH_REDIRECT_URI` 完全一致。
- IP 稳定则这个 URL 就稳定，登录态长期可用。

---

## 3. 服务组成（docker-compose）
- **backend**：FastAPI（`/health` 健康检查）。不跑调度器。
- **scheduler**：`weekly_daemon` 只是统一订阅调度器的进程入口（单实例，避免重复推送）；周报、每日追番、RSS、生日和月报都由同一套 `SubscriptionService` 产生。
- **frontend**：Next.js standalone。`NEXT_PUBLIC_BACKEND=/api`（浏览器走反代）；`INTERNAL_BACKEND=http://backend:8000`（分享页 SSR 服务端直连后端）。
- **caddy**：反代 + 自动 HTTPS；覆盖 `X-Forwarded-For` 防伪造绕限流。
- **cloudflared**（可选 `--profile tunnel`）：临时公网隧道。

生产默认发布和拉取 `otomo-backend`（Docker `core` target），包含网站、调度器、Discord、推荐和浏览器能力，但不携带体积很大的本地 ASR / Pixiv 可选依赖。需要这些能力时，在 Actions 手动运行 `build-images` 生成 `otomo-backend-full`，或本地显式使用 `docker build --target full`；普通 main push 不会自动发布 full 镜像。

> 单机单实例足够个人/朋友规模。要多实例横向扩才需要 Redis（会话/缓存）+ 调度器 leader lock + LTM/share/subscription 迁 Postgres——现在不用管。

---

## 4. 浏览器推送（Web Push）

Web Push 不是在阿里云控制台申请 key。它使用你自己长期持有的一对 **VAPID** 密钥；服务器负责推送，浏览器负责向用户请求通知权限。

首次配置只需在服务器仓库目录运行：

```bash
cd ~/otomo
bash deploy/configure_webpush.sh 你的邮箱@example.com
bash deploy.sh
```

脚本会等待并使用当前 Git commit 对应的 CI backend 镜像生成公私钥，再写入服务器本地的 `backend/.env`。默认最多等待 15 分钟；构建尚未完成时不会误用旧 `latest`：

```dotenv
WEBPUSH_ENABLED=true
WEBPUSH_VAPID_PUBLIC_KEY=<浏览器订阅使用的公钥>
WEBPUSH_VAPID_PRIVATE_KEY=<只保存在服务器的私钥>
WEBPUSH_VAPID_SUBJECT=mailto:你的邮箱@example.com
```

部署后，在网页执行两步：

1. `订阅设置 -> 浏览器设备 -> 允许当前浏览器`，接受浏览器通知权限。
2. 新建或编辑订阅规则，在渠道中勾选 `浏览器推送`。

backend 和 scheduler 都从同一个 `backend/.env` 读取密钥，因此 `bash deploy.sh` 会同时更新。不要把真实私钥提交到 GitHub，也不要随意轮换密钥；轮换后所有浏览器都需要重新授权。确需轮换可执行：

```bash
bash deploy/configure_webpush.sh --rotate 你的邮箱@example.com
bash deploy.sh
```

要求：公网 HTTPS、浏览器允许通知、scheduler 常驻。电脑关闭不影响服务器产生推送，但接收设备需要联网。

生产 Compose 使用不可变的 commit SHA 镜像。日常更新只运行 `bash deploy.sh`；如需手动执行 Compose，先设置：

```bash
export OTOMO_IMAGE_TAG="$(git rev-parse HEAD)"
```

每次通过健康检查和镜像版本核对后，部署脚本会把成功版本写入
`cache/deployments.log`。新版本在容器切换后若启动失败、健康检查失败或
实际镜像不一致，`deploy.sh` 会自动恢复切换前的完整 SHA。也可以手动回到
上一个成功版本（不改 Git、不删除数据库）：

```bash
bash deploy/rollback.sh
```

也可以指定一个已经发布的完整 commit SHA：

```bash
bash deploy/rollback.sh 0123456789abcdef0123456789abcdef01234567
```

默认 backend 镜像不再包含 Playwright/Chromium 和 Whisper。若启用
`ASR_PROVIDER=worker`，先在 GitHub Actions 手动运行 `build-images`，为同一
commit 发布 `otomo-backend-asr`，再执行 `bash deploy.sh`；否则脚本会在触碰
当前容器前等待并安全超时。

---

## 5. 上线检查清单
- [ ] CI 绿（尤其 frontend build）
- [ ] `AUTH_ENCRYPTION_KEY` 固定（换了全员登录失效）
- [ ] `COOKIE_SECURE=true` + `CORS_ALLOWED_ORIGINS` 收敛到你的公网 URL
- [ ] Bangumi OAuth 回调地址 = `FRONTEND_BASE_URL/auth/bangumi/callback`
- [ ] 如需浏览器推送，运行 `bash deploy/configure_webpush.sh 你的邮箱@example.com`，然后重新部署并在订阅设置授权浏览器
- [ ] 如需 qBittorrent，填写 `QBITTORRENT_URL/USERNAME/PASSWORD`；URL 必须从 backend 容器可达，并在管理页执行一次只读连接检测
- [ ] `DAILY_TOKEN_BUDGET_*` 按预算设（防爬虫刷爆 LLM 账单）
- [ ] LLM/VLM provider 后台设月度充值上限（第二道熔断）
- [ ] 备份整个 cache：`deploy/backup_cache.sh` 会对所有 SQLite 做在线一致快照和 `integrity_check`，并保留 auth 密钥、LTM 等非数据库文件；可挂 cron、可选传 OSS
- [ ] 每月做一次恢复演练：解压备份到临时目录后运行 `python3 deploy/cache_backup.py verify --snapshot <解压目录>`，再运行 `python3 deploy/cache_backup.py restore-drill --snapshot <解压目录> --target <新的空目录>`；该命令拒绝覆盖非空目录
- [ ] 部署后做一次备份恢复演练（新容器还原备份，登录态/记忆完好才算数）

---

## 6. 常见坑
- **分享页打不开/404**：Caddyfile 的 `@api` 千万别放裸 `/share/*`——那是前端分享页路由；API 一律走 `/api/share/*`。
- **分享页服务端报 fetch failed**：确认 frontend 服务有 `INTERNAL_BACKEND=http://backend:8000`（SSR 不能用浏览器相对 `/api`）。
- **`NEXT_PUBLIC_BACKEND` 改了不生效**：它是 build 期内联的，改了要 `--build` 重建 frontend 镜像。
- **证书申请失败**：确认 80/443 放行、`OTOMO_DOMAIN` 是能解析到本机的名字（nip.io 需公网 IP 可达）。
- **浏览器推送按钮不可用**：确认公网 HTTPS、backend 与 scheduler 都读取同一份 `backend/.env`，且 VAPID 公私钥及 `WEBPUSH_VAPID_SUBJECT` 配置完整；授权后还需在具体规则里勾选“浏览器推送”。
- **qBittorrent 检测失败**：容器里的 `127.0.0.1` 指向 backend 自己，不是宿主机；改用 Docker 可达的宿主机/LAN 地址，并检查 qB WebUI 的 Host 白名单。Otomo 会发送 qB 5.x 要求的同源 `Origin/Referer`，并核对 Web API 版本。
- **B站登录态**：登录 Otomo 后到“账号与集成”扫码；登录态按 Bangumi 用户加密隔离，不再使用一份全站共享 Cookie。升级自旧版时需要每位用户重新连接一次。
- **pixiv/B站本地 ASR 用不了**：先确认使用的是手动发布的 `otomo-backend-full`；管理页会同时检查 `yt-dlp`、`faster-whisper` 和本地模型，不再只因 `ASR_PROVIDER=local` 就显示绿色。国内 IP 直连 pixiv 仍需海外节点或代理。
