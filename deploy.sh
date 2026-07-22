#!/usr/bin/env bash
# 一键更新:拉代码/配置 + 拉 CI 构建好的新镜像 + 重启。服务器不再本地 build。
# 用法:bash deploy.sh
set -euo pipefail
cd "$(dirname "$0")"

COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile discord"

echo "==> git pull"
git pull

echo "==> 拉取最新镜像"
$COMPOSE pull

echo "==> 重启服务"
$COMPOSE up -d

echo "==> 状态"
$COMPOSE ps
