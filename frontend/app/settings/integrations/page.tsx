"use client";

import { LoaderCircle, ShieldCheck, Trash2 } from "lucide-react";
import QRCode from "qrcode";
import { useEffect, useState } from "react";

import { AuthGate } from "../../../components/auth-gate";
import { PageHeader } from "../../../components/page-header";
import { productFetch } from "../../../lib/api";
import { useExperience } from "../../../lib/experience";

type AnyRecord = Record<string, any>;

export default function IntegrationSettingsPage() {
  const exp = useExperience();
  const [status, setStatus] = useState<AnyRecord>({ configured: false, authenticated: false });
  const [qr, setQr] = useState<AnyRecord | null>(null);
  const [qrImage, setQrImage] = useState("");
  const [cookies, setCookies] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    if (!exp.authReady || !exp.authenticated) return;
    productFetch("/integrations/bilibili")
      .then((payload) => setStatus(payload.integration || {}))
      .catch((error) => setNotice(error instanceof Error ? error.message : String(error)));
  }, [exp.authReady, exp.authenticated]);

  useEffect(() => {
    if (!qr?.login_id || !["waiting", "scanned"].includes(qr.status)) return;
    let stopped = false;
    const timer = window.setTimeout(async () => {
      try {
        const payload = await productFetch("/integrations/bilibili/qr/poll", {
          method: "POST",
          headers: { "Content-Type": "application/json", "x-otomo-csrf": exp.csrf },
          body: JSON.stringify({ login_id: qr.login_id }),
        });
        if (stopped) return;
        setQr((current) => current ? { ...current, ...payload.login } : payload.login);
        if (payload.integration) {
          setStatus(payload.integration);
          setQrImage("");
          setNotice("B站账号已连接；这份登录态只属于当前 Bangumi 用户。");
        }
      } catch (error) {
        if (!stopped) setNotice(error instanceof Error ? error.message : String(error));
      }
    }, 1800);
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [qr?.login_id, qr?.status, exp.csrf]);

  async function startQr() {
    setBusy(true); setNotice("");
    try {
      const payload = await productFetch("/integrations/bilibili/qr/start", {
        method: "POST", headers: { "x-otomo-csrf": exp.csrf },
      });
      setQr(payload.login);
      setQrImage(await QRCode.toDataURL(payload.login.qr_url, { width: 240, margin: 1 }));
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally { setBusy(false); }
  }

  async function importCookies() {
    if (!cookies.trim()) return;
    setBusy(true); setNotice("");
    try {
      const payload = await productFetch("/integrations/bilibili/cookies", {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-otomo-csrf": exp.csrf },
        body: JSON.stringify({ cookies_text: cookies }),
      });
      setStatus(payload.integration || {});
      setCookies("");
      setNotice("cookies.txt 已加密保存到你的独立账号。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally { setBusy(false); }
  }

  async function disconnect() {
    if (!window.confirm("断开你自己的 B站账号？公开视频搜索仍可使用。")) return;
    setBusy(true); setNotice("");
    try {
      const payload = await productFetch("/integrations/bilibili", {
        method: "DELETE", headers: { "x-otomo-csrf": exp.csrf },
      });
      setStatus(payload.integration || {});
      setQr(null); setQrImage("");
      setNotice("当前账号的 B站登录态已删除。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally { setBusy(false); }
  }

  return (
    <main className="page-frame integration-settings-page">
      <PageHeader eyebrow="Accounts" title="账号与集成" description="每位 Otomo 用户维护自己的外部账号；凭据不会共享给其他用户，也不会进入模型上下文。" />
      {!exp.authReady ? <div className="surface-loading"><LoaderCircle className="spin" size={16} /> 正在读取账号…</div> : !exp.authenticated ? <AuthGate
        eyebrow="需要登录"
        title="连接账号前，请先登录 Bangumi"
        description="Otomo 使用 Bangumi 身份作为外部账号的隔离边界。"
        features={["每位用户独立凭据", "服务器端加密", "不会进入模型上下文"]}
      /> : <>
        {notice ? <div className="surface-notice">{notice}</div> : null}
        <section className="admin-section admin-integration">
          <header><div><span className="section-kicker">个人账号</span><h2>Bilibili</h2></div><span>{status.authenticated ? `已连接 @${status.username}` : status.configured ? "登录态已失效" : "公开模式"}</span></header>
          <div className="integration-status-row">
            <span className={`admin-integration-badge ${status.authenticated ? "good" : "dim"}`}>{status.authenticated ? "可用" : "未连接"}</span>
            <p>用于你发起的搜索、视频详情、字幕与少量深度核验。服务器按当前 Bangumi 用户隔离并加密保存 Cookie。</p>
          </div>
          <div className="panel-actions">
            <button className="button-primary" disabled={busy} onClick={() => void startQr()}>{busy ? <LoaderCircle className="spin" size={15} /> : <ShieldCheck size={15} />}使用 B站 App 扫码</button>
            {status.configured ? <button className="button-secondary" disabled={busy} onClick={() => void disconnect()}><Trash2 size={15} />断开我的账号</button> : null}
          </div>
          {qrImage ? <div className="admin-bili-qr"><img src={qrImage} alt="B站登录二维码" /><div><strong>{qr?.message || "等待扫码"}</strong><span>二维码约 3 分钟后过期；扫码记录同样支持多进程部署。</span></div></div> : null}
          <details className="admin-cookie-fallback"><summary>扫码不可用？导入 cookies.txt</summary><label className="admin-cookie-import"><span>粘贴 Netscape cookies.txt</span><textarea rows={5} value={cookies} onChange={(event) => setCookies(event.target.value)} placeholder="# Netscape HTTP Cookie File…" spellCheck={false} /><small>建议使用专门账号；Cookie 全文不会返回浏览器、日志或聊天。</small></label><button className="button-secondary" disabled={busy || !cookies.trim()} onClick={() => void importCookies()}>导入到我的账号</button></details>
        </section>
      </>}
    </main>
  );
}
