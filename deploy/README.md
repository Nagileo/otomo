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

> 单机单实例足够个人/朋友规模。要多实例横向扩才需要 Redis（会话/缓存）+ 调度器 leader lock + LTM/share/subscription 迁 Postgres——现在不用管。

---

## 4. 上线检查清单
- [ ] CI 绿（尤其 frontend build）
- [ ] `AUTH_ENCRYPTION_KEY` 固定（换了全员登录失效）
- [ ] `COOKIE_SECURE=true` + `CORS_ALLOWED_ORIGINS` 收敛到你的公网 URL
- [ ] Bangumi OAuth 回调地址 = `FRONTEND_BASE_URL/auth/bangumi/callback`
- [ ] `DAILY_TOKEN_BUDGET_*` 按预算设（防爬虫刷爆 LLM 账单）
- [ ] LLM/VLM provider 后台设月度充值上限（第二道熔断）
- [ ] 备份 cache/（auth/sessions/share/subscriptions/ltm）：`deploy/backup_cache.sh` 挂 cron，可选传 OSS
- [ ] 部署后做一次备份恢复演练（新容器还原备份，登录态/记忆完好才算数）

---

## 5. 常见坑
- **分享页打不开/404**：Caddyfile 的 `@api` 千万别放裸 `/share/*`——那是前端分享页路由；API 一律走 `/api/share/*`。
- **分享页服务端报 fetch failed**：确认 frontend 服务有 `INTERNAL_BACKEND=http://backend:8000`（SSR 不能用浏览器相对 `/api`）。
- **`NEXT_PUBLIC_BACKEND` 改了不生效**：它是 build 期内联的，改了要 `--build` 重建 frontend 镜像。
- **证书申请失败**：确认 80/443 放行、`OTOMO_DOMAIN` 是能解析到本机的名字（nip.io 需公网 IP 可达）。
- **pixiv/B站 ASR 用不了**：国内 IP 直连 pixiv 不可达（选海外节点或挂代理）；B站 ASR 需 cookies（见 ASR_COOKIES_*）。
