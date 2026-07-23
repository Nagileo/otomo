#!/usr/bin/env bash
# 一键更新:拉代码/配置 + 拉 CI 构建好的新镜像 + 重启。服务器不再本地 build。
# 用法:bash deploy.sh
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f backend/.env ]]; then
  echo "ERROR: backend/.env 不存在；先按 deploy/production.env.example 配置生产环境。" >&2
  exit 1
fi

env_value() {
  local key="$1"
  local value
  value="$(sed -n "s/^${key}=//p" backend/.env | tail -n 1 | tr -d '\r')"
  value="${value%\"}"
  value="${value#\"}"
  value="${value%\'}"
  value="${value#\'}"
  printf '%s' "$value"
}

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)
if [[ -n "$(env_value DISCORD_BOT_TOKEN)" ]]; then
  COMPOSE+=(--profile discord)
fi

echo "==> git pull --ff-only"
git pull --ff-only

frontend_url="$(env_value FRONTEND_BASE_URL)"
frontend_url="${frontend_url%/}"
cookie_secure="$(env_value COOKIE_SECURE)"
auth_key="$(env_value AUTH_ENCRYPTION_KEY)"
oauth_client_id="$(env_value BANGUMI_OAUTH_CLIENT_ID)"
oauth_redirect="$(env_value BANGUMI_OAUTH_REDIRECT_URI)"

if [[ -z "$frontend_url" ]]; then
  echo "ERROR: backend/.env 必须配置 FRONTEND_BASE_URL" >&2
  exit 1
fi
if [[ "$frontend_url" == https://* && "${cookie_secure,,}" != "true" ]]; then
  echo "ERROR: HTTPS 部署必须设置 COOKIE_SECURE=true" >&2
  exit 1
fi
if [[ "${cookie_secure,,}" == "true" ]]; then
  if [[ -z "$auth_key" || "$auth_key" == generate-* ]]; then
    echo "ERROR: 生产环境必须生成并固定 AUTH_ENCRYPTION_KEY，不能使用示例占位值" >&2
    exit 1
  fi
fi
if [[ -n "$oauth_client_id" && "$oauth_redirect" != "$frontend_url/auth/bangumi/callback" ]]; then
  echo "ERROR: BANGUMI_OAUTH_REDIRECT_URI 必须等于 ${frontend_url}/auth/bangumi/callback" >&2
  exit 1
fi

# Compose only interpolates the shell/root .env, not backend/.env. Derive the
# Caddy site address so later SSH deployments cannot silently fall back to localhost.
if [[ -z "${OTOMO_DOMAIN:-}" ]]; then
  domain="${frontend_url#*://}"
  domain="${domain%%/*}"
  domain="${domain%%:*}"
  export OTOMO_DOMAIN="$domain"
fi
echo "==> 部署域名: ${OTOMO_DOMAIN}"

echo "==> 校验 Compose 配置"
"${COMPOSE[@]}" config --quiet

echo "==> 拉取最新镜像"
"${COMPOSE[@]}" pull

echo "==> 重启服务"
"${COMPOSE[@]}" up -d --remove-orphans

echo "==> 等待 backend 健康检查"
backend_id="$("${COMPOSE[@]}" ps -q backend)"
if [[ -z "$backend_id" ]]; then
  echo "ERROR: backend 容器未创建" >&2
  exit 1
fi
healthy=false
for _ in $(seq 1 60); do
  status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$backend_id")"
  if [[ "$status" == "healthy" ]]; then
    healthy=true
    break
  fi
  if [[ "$status" == "unhealthy" || "$status" == "exited" || "$status" == "dead" ]]; then
    break
  fi
  sleep 2
done
if [[ "$healthy" != "true" ]]; then
  echo "ERROR: backend 未通过健康检查" >&2
  "${COMPOSE[@]}" ps
  "${COMPOSE[@]}" logs --tail=120 backend
  exit 1
fi

echo "==> 状态"
"${COMPOSE[@]}" ps
