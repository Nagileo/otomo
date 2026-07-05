"""手动取 Pixiv refresh_token（ZipFile OAuth 流；gppt 被 reCAPTCHA/网络拦住时的备用）。

用法：
  1. python scripts/pixiv_token.py --proxy http://127.0.0.1:7890
     （打印一个登录 URL 并尝试自动打开浏览器）
  2. 浏览器里【先按 F12 打开 DevTools → Network 面板】再完成 pixiv 登录（人工过验证码没问题）。
  3. 登录成功后 Network 里找最后一个 callback 请求（过滤 "callback"），
     其 URL 形如 pixiv://account/login?code=XXXX&via=login —— 复制 code 的值。
  4. 30 秒内回到终端粘贴 code（code 有效期极短，过期就重跑一遍）。

client_id/secret 是 Pixiv Android 客户端的公开常量（pixivpy 生态通用），不是任何人的机密。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import secrets
import sys
import webbrowser

import httpx

# Pixiv Android app 公开常量（与 pixivpy / gppt 相同）
CLIENT_ID = "MOBrBDS8blbauoSck0ZfDbtuzpyT"
CLIENT_SECRET = "lsACyCD94FhDUtGTXi3QzcFE2uU1hqtDaKeqrdwj"
LOGIN_URL = "https://app-api.pixiv.net/web/v1/login"
AUTH_TOKEN_URL = "https://oauth.secure.pixiv.net/auth/token"
REDIRECT_URI = "https://app-api.pixiv.net/web/v1/users/auth/pixiv/callback"
USER_AGENT = "PixivAndroidApp/5.0.234 (Android 11; Pixel 5)"


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def main() -> int:
    ap = argparse.ArgumentParser(description="Exchange a pixiv login code for a refresh_token")
    ap.add_argument("--proxy", default="", help="如 http://127.0.0.1:7890；国内网络必填")
    args = ap.parse_args()

    verifier, challenge = _pkce_pair()
    url = f"{LOGIN_URL}?code_challenge={challenge}&code_challenge_method=S256&client=pixiv-android"
    print("\n[1] 打开并登录（先按 F12 开 Network 面板再登录）：\n")
    print(f"    {url}\n")
    webbrowser.open(url)
    print('[2] 登录后在 Network 里过滤 "callback"，从 pixiv://account/login?code=... 复制 code')
    code = input("[3] 粘贴 code（30 秒内）: ").strip()
    if not code:
        print("code 为空，退出。")
        return 1

    proxy = args.proxy or None
    with httpx.Client(proxy=proxy, timeout=20) as client:
        resp = client.post(
            AUTH_TOKEN_URL,
            data={
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": REDIRECT_URI,
                "include_policy": "true",
            },
            headers={"User-Agent": USER_AGENT},
        )
    if resp.status_code != 200:
        print(f"\n交换失败 HTTP {resp.status_code}: {resp.text[:300]}")
        print("常见原因：code 已过期（重跑一遍更快粘贴）/ 代理没配 / code 复制不完整。")
        return 1
    payload = resp.json()
    refresh = payload.get("refresh_token", "")
    user = (payload.get("user") or {}).get("name", "?")
    print(f"\n✅ 登录用户：{user}")
    print(f"\nPIXIV_REFRESH_TOKEN={refresh}\n")
    print("把上面这行复制进 backend/.env 即可（refresh_token 长期有效，妥善保管）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
