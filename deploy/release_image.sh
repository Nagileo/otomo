#!/usr/bin/env bash
# Shared release-image helpers for production scripts. Callers must define the
# COMPOSE array before invoking otomo_wait_for_release_images.

otomo_export_release_tag() {
  local release_sha
  release_sha="$(git rev-parse --verify HEAD 2>/dev/null || true)"
  if [[ ! "$release_sha" =~ ^[0-9a-f]{40}$ ]]; then
    echo "ERROR: 无法读取当前 Git commit；生产部署必须从完整的 Git 工作区执行。" >&2
    return 1
  fi
  export OTOMO_IMAGE_TAG="$release_sha"
  echo "==> 目标发布版本: ${OTOMO_IMAGE_TAG}"
}

otomo_wait_for_release_images() {
  local timeout_seconds="${OTOMO_IMAGE_WAIT_SECONDS:-900}"
  local retry_seconds="${OTOMO_IMAGE_RETRY_SECONDS:-15}"
  local started_at now elapsed attempt=1
  local -a services=("$@")

  if [[ ${#services[@]} -eq 0 ]]; then
    echo "ERROR: otomo_wait_for_release_images 至少需要一个服务名。" >&2
    return 1
  fi
  if [[ ! "$timeout_seconds" =~ ^[0-9]+$ || ! "$retry_seconds" =~ ^[1-9][0-9]*$ ]]; then
    echo "ERROR: OTOMO_IMAGE_WAIT_SECONDS / OTOMO_IMAGE_RETRY_SECONDS 必须是非负整数。" >&2
    return 1
  fi

  started_at="$(date +%s)"
  while true; do
    echo "==> 拉取 commit 镜像（第 ${attempt} 次）: ${services[*]}"
    if "${COMPOSE[@]}" pull "${services[@]}"; then
      return 0
    fi

    now="$(date +%s)"
    elapsed=$((now - started_at))
    if (( elapsed >= timeout_seconds )); then
      echo "ERROR: 等待 ${OTOMO_IMAGE_TAG} 镜像超时（${timeout_seconds}s）。" >&2
      echo "GitHub Actions 的 ci/build-images 可能仍在运行或已经失败；旧容器保持不变。" >&2
      return 1
    fi
    echo "镜像尚未发布；${retry_seconds}s 后重试（旧容器仍在服务）。" >&2
    sleep "$retry_seconds"
    attempt=$((attempt + 1))
  done
}
