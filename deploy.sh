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
release_services=(backend frontend)
if [[ "$(env_value ASR_PROVIDER)" == "worker" ]]; then
  COMPOSE+=(--profile asr)
  release_services+=(asr)
fi

before_pull_sha="$(git rev-parse --verify HEAD)"
echo "==> git pull --ff-only"
git pull --ff-only
after_pull_sha="$(git rev-parse --verify HEAD)"
if [[ "$before_pull_sha" != "$after_pull_sha" && "${OTOMO_DEPLOY_REEXEC:-0}" != "1" ]]; then
  echo "==> 部署脚本已更新，使用新版本重新执行"
  exec env OTOMO_DEPLOY_REEXEC=1 bash "$0"
fi

# Source and containers must represent the same immutable commit. `latest`
# allowed a successful git pull to silently keep running an older image while
# the asynchronous build-images workflow was still publishing.
source deploy/release_image.sh
otomo_export_release_tag

frontend_url="$(env_value FRONTEND_BASE_URL)"
frontend_url="${frontend_url%/}"
cookie_secure="$(env_value COOKIE_SECURE)"
auth_key="$(env_value AUTH_ENCRYPTION_KEY)"
oauth_client_id="$(env_value BANGUMI_OAUTH_CLIENT_ID)"
oauth_redirect="$(env_value BANGUMI_OAUTH_REDIRECT_URI)"
webpush_enabled="$(env_value WEBPUSH_ENABLED)"
webpush_public="$(env_value WEBPUSH_VAPID_PUBLIC_KEY)"
webpush_private="$(env_value WEBPUSH_VAPID_PRIVATE_KEY)"
webpush_subject="$(env_value WEBPUSH_VAPID_SUBJECT)"

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
if [[ "${webpush_enabled,,}" == "true" ]]; then
  if [[ -z "$webpush_public" || -z "$webpush_private" || -z "$webpush_subject" ]]; then
    echo "ERROR: WEBPUSH_ENABLED=true 时必须同时配置 VAPID 公钥、私钥和 subject" >&2
    echo "可运行: bash deploy/configure_webpush.sh admin@example.com" >&2
    exit 1
  fi
  if [[ "$frontend_url" != https://* ]]; then
    echo "ERROR: 浏览器 Web Push 生产环境必须使用 HTTPS" >&2
    exit 1
  fi
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

# Remember the actually running immutable release before changing containers.
# This is independent from Git HEAD and lets a failed switch restore service.
previous_backend_id="$("${COMPOSE[@]}" ps -q backend 2>/dev/null || true)"
previous_release_tag=""
if [[ -n "$previous_backend_id" ]]; then
  previous_backend_image="$(docker inspect --format '{{.Config.Image}}' "$previous_backend_id")"
  previous_release_tag="${previous_backend_image##*:}"
fi

restore_previous_release() {
  if [[ ! "$previous_release_tag" =~ ^[0-9a-f]{40}$ || "$previous_release_tag" == "$OTOMO_IMAGE_TAG" ]]; then
    echo "ERROR: 没有可自动恢复的上一版完整 SHA，请按容器日志人工处理。" >&2
    return 1
  fi
  local failed_tag="$OTOMO_IMAGE_TAG"
  echo "==> 新版本 ${failed_tag} 未通过上线检查，自动恢复 ${previous_release_tag}" >&2
  export OTOMO_IMAGE_TAG="$previous_release_tag"
  if ! "${COMPOSE[@]}" up -d --remove-orphans; then
    echo "ERROR: 自动恢复命令失败，请立即运行 bash deploy/rollback.sh ${previous_release_tag}" >&2
    return 1
  fi
  local restored_id status
  restored_id="$("${COMPOSE[@]}" ps -q backend)"
  for _ in $(seq 1 60); do
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$restored_id")"
    if [[ "$status" == "healthy" ]]; then
      echo "==> 已恢复上一版: ${previous_release_tag}" >&2
      return 0
    fi
    if [[ "$status" == "unhealthy" || "$status" == "exited" || "$status" == "dead" ]]; then
      break
    fi
    sleep 2
  done
  echo "ERROR: 上一版也未恢复健康，请立即检查服务日志。" >&2
  return 1
}

# CI runs before build-images, so a deployment may begin a few minutes before
# the SHA-tagged images exist. Wait without touching the currently running
# containers; a failed/timeout build therefore cannot replace production.
otomo_wait_for_release_images "${release_services[@]}"

echo "==> 拉取其余基础镜像"
"${COMPOSE[@]}" pull

echo "==> 重启服务"
if ! "${COMPOSE[@]}" up -d --remove-orphans; then
  restore_previous_release || true
  exit 1
fi

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
  restore_previous_release || true
  exit 1
fi

verify_service_image() {
  local service="$1"
  local expected_image="$2"
  local container_id actual_image
  container_id="$("${COMPOSE[@]}" ps -q "$service")"
  if [[ -z "$container_id" ]]; then
    echo "ERROR: ${service} 容器未创建" >&2
    return 1
  fi
  actual_image="$(docker inspect --format '{{.Config.Image}}' "$container_id")"
  if [[ "$actual_image" != "$expected_image" ]]; then
    echo "ERROR: ${service} 镜像版本不一致: expected=${expected_image}, actual=${actual_image}" >&2
    return 1
  fi
}

echo "==> 校验运行版本"
expected_backend_image="ghcr.io/nagileo/otomo-backend:${OTOMO_IMAGE_TAG}"
if ! verify_service_image backend "$expected_backend_image" \
  || ! verify_service_image scheduler "$expected_backend_image" \
  || ! verify_service_image frontend "ghcr.io/nagileo/otomo-frontend:${OTOMO_IMAGE_TAG}"; then
  restore_previous_release || true
  exit 1
fi
if [[ -n "$(env_value DISCORD_BOT_TOKEN)" ]]; then
  verify_service_image discord "$expected_backend_image" || {
    restore_previous_release || true
    exit 1
  }
fi
if [[ "$(env_value ASR_PROVIDER)" == "worker" ]]; then
  verify_service_image asr "ghcr.io/nagileo/otomo-backend-asr:${OTOMO_IMAGE_TAG}" || {
    restore_previous_release || true
    exit 1
  }
fi

echo "==> 状态"
"${COMPOSE[@]}" ps

# This append happens only after health and exact-image checks. The file lives
# in the persistent cache volume and becomes the source of truth for rollback.
mkdir -p cache
deployment_log="cache/deployments.log"
last_sha="$(tail -n 1 "$deployment_log" 2>/dev/null | awk '{print $2}' || true)"
if [[ "$last_sha" != "$OTOMO_IMAGE_TAG" ]]; then
  printf '%s %s deploy\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$OTOMO_IMAGE_TAG" >> "$deployment_log"
fi
echo "==> 已记录成功部署: ${OTOMO_IMAGE_TAG}"
