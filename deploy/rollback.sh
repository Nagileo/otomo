#!/usr/bin/env bash
# Roll back to an immutable, previously successful image without changing Git
# history or touching cache/SQLite data. Usage: bash deploy/rollback.sh [SHA]
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -f backend/.env ]]; then
  echo "ERROR: backend/.env 不存在。" >&2
  exit 1
fi

env_value() {
  local key="$1" value
  value="$(sed -n "s/^${key}=//p" backend/.env | tail -n 1 | tr -d '\r')"
  value="${value%\"}"; value="${value#\"}"
  value="${value%\'}"; value="${value#\'}"
  printf '%s' "$value"
}

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)
services=(backend frontend)
if [[ -n "$(env_value DISCORD_BOT_TOKEN)" ]]; then
  COMPOSE+=(--profile discord)
fi
if [[ "$(env_value ASR_PROVIDER)" == "worker" ]]; then
  COMPOSE+=(--profile asr)
  services+=(asr)
fi

backend_id="$("${COMPOSE[@]}" ps -q backend 2>/dev/null || true)"
current_tag=""
if [[ -n "$backend_id" ]]; then
  current_image="$(docker inspect --format '{{.Config.Image}}' "$backend_id")"
  current_tag="${current_image##*:}"
fi

deployment_log="cache/deployments.log"
target="${1:-}"
if [[ -z "$target" ]]; then
  if [[ ! -f "$deployment_log" ]]; then
    echo "ERROR: 没有成功部署历史；请显式传入完整 commit SHA。" >&2
    exit 1
  fi
  mapfile -t successful < <(awk '$2 ~ /^[0-9a-f]{40}$/ {print $2}' "$deployment_log")
  for ((index=${#successful[@]}-1; index>=0; index--)); do
    if [[ "${successful[$index]}" != "$current_tag" ]]; then
      target="${successful[$index]}"
      break
    fi
  done
fi
if [[ ! "$target" =~ ^[0-9a-f]{40}$ ]]; then
  echo "ERROR: 回滚目标必须是完整的 40 位小写 commit SHA。" >&2
  exit 1
fi
if [[ "$target" == "$current_tag" ]]; then
  echo "目标 ${target} 已经在运行，无需回滚。"
  exit 0
fi

export OTOMO_IMAGE_TAG="$target"
source deploy/release_image.sh
echo "==> 当前版本: ${current_tag:-未知}"
echo "==> 回滚目标: ${OTOMO_IMAGE_TAG}"
echo "==> 先验证并拉取目标不可变镜像（当前容器继续服务）"
otomo_wait_for_release_images "${services[@]}"
"${COMPOSE[@]}" config --quiet

restore_current() {
  if [[ "$current_tag" =~ ^[0-9a-f]{40}$ ]]; then
    echo "ERROR: 回滚目标未通过健康检查，恢复原版本 ${current_tag}" >&2
    export OTOMO_IMAGE_TAG="$current_tag"
    "${COMPOSE[@]}" up -d --remove-orphans || true
  fi
}

echo "==> 切换容器"
if ! "${COMPOSE[@]}" up -d --remove-orphans; then
  restore_current
  exit 1
fi
backend_id="$("${COMPOSE[@]}" ps -q backend)"
healthy=false
for _ in $(seq 1 60); do
  status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$backend_id")"
  if [[ "$status" == "healthy" ]]; then healthy=true; break; fi
  if [[ "$status" == "unhealthy" || "$status" == "exited" || "$status" == "dead" ]]; then break; fi
  sleep 2
done
if [[ "$healthy" != "true" ]]; then
  "${COMPOSE[@]}" logs --tail=120 backend || true
  restore_current
  exit 1
fi

verify_image() {
  local service="$1" expected="$2" id actual
  id="$("${COMPOSE[@]}" ps -q "$service")"
  [[ -n "$id" ]] || { echo "ERROR: ${service} 未运行" >&2; return 1; }
  actual="$(docker inspect --format '{{.Config.Image}}' "$id")"
  [[ "$actual" == "$expected" ]] || {
    echo "ERROR: ${service} 运行镜像不一致: ${actual}" >&2
    return 1
  }
}

expected_backend="ghcr.io/nagileo/otomo-backend:${target}"
if ! verify_image backend "$expected_backend" \
  || ! verify_image scheduler "$expected_backend" \
  || ! verify_image frontend "ghcr.io/nagileo/otomo-frontend:${target}"; then
  restore_current
  exit 1
fi
if [[ -n "$(env_value DISCORD_BOT_TOKEN)" ]]; then
  verify_image discord "$expected_backend" || { restore_current; exit 1; }
fi
if [[ "$(env_value ASR_PROVIDER)" == "worker" ]]; then
  verify_image asr "ghcr.io/nagileo/otomo-backend-asr:${target}" || { restore_current; exit 1; }
fi

mkdir -p cache
printf '%s %s rollback\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$target" >> "$deployment_log"
echo "==> 回滚成功: ${target}"
"${COMPOSE[@]}" ps
