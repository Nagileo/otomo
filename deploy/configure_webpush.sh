#!/usr/bin/env bash
# Generate one long-lived VAPID key pair inside the backend image and write it
# to backend/.env. Existing keys are never rotated unless --rotate is explicit.
set -euo pipefail
cd "$(dirname "$0")/.."

ENV_FILE="backend/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: $ENV_FILE 不存在；先复制 deploy/production.env.example 并完成基础配置。" >&2
  exit 1
fi

rotate=false
subject="${1:-}"
if [[ "$subject" == "--rotate" ]]; then
  rotate=true
  subject="${2:-}"
fi
if [[ -z "$subject" ]]; then
  echo "用法: bash deploy/configure_webpush.sh admin@example.com" >&2
  echo "轮换: bash deploy/configure_webpush.sh --rotate admin@example.com" >&2
  exit 1
fi
if [[ "$subject" != mailto:* && "$subject" != https://* ]]; then
  subject="mailto:$subject"
fi

current_private="$(sed -n 's/^WEBPUSH_VAPID_PRIVATE_KEY=//p' "$ENV_FILE" | tail -n 1 | tr -d '\r')"
if [[ -n "$current_private" && "$rotate" != true ]]; then
  echo "ERROR: 已存在 VAPID 密钥。为避免所有浏览器重新授权，本脚本不会自动轮换。" >&2
  echo "确需轮换时显式传 --rotate。" >&2
  exit 1
fi

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)
source deploy/release_image.sh
otomo_export_release_tag
otomo_wait_for_release_images backend
echo "==> 使用 backend 镜像生成 VAPID 密钥"
mapfile -t keys < <("${COMPOSE[@]}" run --rm --no-deps -T backend python -c '
import base64
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid

vapid = Vapid()
vapid.generate_keys()
private = vapid.private_key.private_numbers().private_value.to_bytes(32, "big")
public = vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
encode = lambda value: base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
print(encode(public))
print(encode(private))
')

public_key="${keys[0]:-}"
private_key="${keys[1]:-}"
if [[ ${#public_key} -lt 80 || ${#private_key} -lt 40 ]]; then
  echo "ERROR: VAPID 密钥生成失败。" >&2
  exit 1
fi

set_env() {
  local key="$1"
  local value="$2"
  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    printf '\n%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

set_env WEBPUSH_ENABLED true
set_env WEBPUSH_VAPID_PUBLIC_KEY "$public_key"
set_env WEBPUSH_VAPID_PRIVATE_KEY "$private_key"
set_env WEBPUSH_VAPID_SUBJECT "$subject"

echo "==> Web Push 配置已写入 $ENV_FILE"
echo "下一步: bash deploy.sh"
echo "部署后进入 Otomo -> 订阅设置 -> 允许当前浏览器，再给具体规则勾选“浏览器推送”。"
