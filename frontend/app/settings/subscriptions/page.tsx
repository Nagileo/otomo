"use client";

import { useEffect, useMemo, useState } from "react";

import { AuthGate } from "../../../components/auth-gate";
import { useExperience } from "../../../lib/experience";

const BACKEND = process.env.NEXT_PUBLIC_BACKEND ?? "http://localhost:8000";

type AnyRecord = Record<string, any>;
type Notice = { tone: "good" | "warn" | "bad"; text: string };

const KINDS = [
  ["weekly_digest", "每周周报"],
  ["daily_airing", "每日追番"],
  ["monthly_report", "月报"],
  ["rss_release", "RSS 新资源"],
  ["anime_follow", "动画作品长期关注"],
  ["birthday", "生日提醒"],
  ["rating_alert", "口碑哨兵（在看/想看的番评分异动）"],
  ["friends_activity", "好友动态（他们在看什么、打了几分）"],
  ["episode_buzz", "分集爆点（你追的番哪集突然火了）"],
  ["bili_up_video", "B站导视/漫评"],
] as const;
const CHANNELS = [
  ["inbox", "站内"],
  ["email", "Email"],
  ["webhook", "Webhook"],
  ["discord_dm", "Discord 私信（需 /绑定）"],
  ["webpush", "浏览器推送"],
] as const;
const WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];
const PRESETS = [
  { kind: "daily_airing", label: "每日追番", description: "每天 8:00 提醒今天更新的作品", hour: 8, weekday: 0 },
  { kind: "weekly_digest", label: "每周周报", description: "每周一 9:00 汇总进度与动态", hour: 9, weekday: 0 },
  { kind: "friends_activity", label: "好友动态", description: "每天 20:00 看好友最近在追什么", hour: 20, weekday: 0 },
  { kind: "rating_alert", label: "口碑变化", description: "每天 19:00 留意在看/想看作品的评分变化", hour: 19, weekday: 0 },
] as const;
const KIND_LABEL = Object.fromEntries(KINDS);
const CHANNEL_LABEL = Object.fromEntries(CHANNELS);
const TEMPLATE_LABEL: Record<string, string> = { brief: "精简", normal: "标准", detailed: "详细" };
const DELIVERY_STATUS: Record<string, string> = { sent: "已发送", failed: "发送失败", skipped: "已跳过", pending: "等待发送" };

function list(value: any): AnyRecord[] {
  return Array.isArray(value) ? value : [];
}

export default function SubscriptionSettingsPage() {
  const experience = useExperience();
  const [csrf, setCsrf] = useState("");
  const [auth, setAuth] = useState<AnyRecord | null>(null);
  const [rules, setRules] = useState<AnyRecord[]>([]);
  const [deliveries, setDeliveries] = useState<AnyRecord[]>([]);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState(false);
  const [pushBusy, setPushBusy] = useState(false);
  const [pushConfig, setPushConfig] = useState<AnyRecord>({ enabled: false, devices: [] });
  const [draft, setDraft] = useState({
    kind: "weekly_digest",
    title: "",
    hour: 9,
    minute: 0,
    weekday: 0,
    day_of_month: 1,
    interval_minutes: 0,
    timezone: "Asia/Shanghai",
    channels: ["inbox"],
    template: "normal",
    webhook_format: "generic",
    webhook_url: "",
    email: "",
    quiet_start: "23:00",
    quiet_end: "08:00",
    filters_json: "{}",
  });

  useEffect(() => {
    void bootstrap();
  }, []);

  async function bootstrap() {
    const payload = await experience.refreshAuthSession();
    setAuth(payload);
    setCsrf(payload.csrf_token || "");
    if (payload.authenticated) {
      await Promise.all([loadRules(payload.csrf_token || ""), loadWebpush()]);
    }
  }

  async function loadWebpush() {
    const res = await fetch(`${BACKEND}/subscriptions/webpush/config`, { credentials: "include" });
    const payload = await res.json().catch(() => ({}));
    if (res.ok && payload.ok) setPushConfig(payload);
  }

  function vapidKey(value: string) {
    const padding = "=".repeat((4 - (value.length % 4)) % 4);
    const base64 = (value + padding).replace(/-/g, "+").replace(/_/g, "/");
    const raw = window.atob(base64);
    return Uint8Array.from(Array.from(raw).map((char) => char.charCodeAt(0)));
  }

  async function pushDeviceId(endpoint: string) {
    const bytes = new TextEncoder().encode(endpoint.trim());
    const hash = await crypto.subtle.digest("SHA-256", bytes);
    const hex = Array.from(new Uint8Array(hash)).map((x) => x.toString(16).padStart(2, "0")).join("");
    return `push_${hex.slice(0, 32)}`;
  }

  async function enableBrowserPush() {
    setPushBusy(true);
    try {
      if (!pushConfig.enabled || !pushConfig.public_key) throw new Error("服务器尚未配置 VAPID 密钥。");
      if (!("serviceWorker" in navigator) || !("PushManager" in window)) throw new Error("当前浏览器不支持 Web Push。");
      if (!window.isSecureContext) throw new Error("Web Push 只允许 HTTPS 或 localhost。");
      const permission = await Notification.requestPermission();
      if (permission !== "granted") throw new Error("浏览器通知权限未授予。");
      await navigator.serviceWorker.register("/sw.js");
      const registration = await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.getSubscription() || await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: vapidKey(pushConfig.public_key),
      });
      const json = subscription.toJSON();
      if (!json.endpoint || !json.keys?.p256dh || !json.keys?.auth) throw new Error("浏览器没有返回完整 PushSubscription。");
      const res = await fetch(`${BACKEND}/subscriptions/webpush`, {
        method: "POST",
        credentials: "include",
        headers: headers({ "Content-Type": "application/json" }),
        body: JSON.stringify({ endpoint: json.endpoint, expiration_time: subscription.expirationTime, keys: json.keys }),
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok || !payload.ok) throw new Error(payload.detail || `绑定失败：HTTP ${res.status}`);
      setNotice({ tone: "good", text: "当前浏览器已允许接收 Otomo 推送。现在可在规则中勾选“浏览器推送”。" });
      await loadWebpush();
    } catch (error) {
      setNotice({ tone: "bad", text: error instanceof Error ? error.message : "浏览器推送授权失败。" });
    } finally {
      setPushBusy(false);
    }
  }

  async function disableBrowserPush() {
    setPushBusy(true);
    try {
      const registration = await navigator.serviceWorker.getRegistration();
      const subscription = await registration?.pushManager.getSubscription();
      if (!subscription) {
        setNotice({ tone: "warn", text: "当前浏览器没有活动的推送授权。" });
        return;
      }
      const deviceId = await pushDeviceId(subscription.endpoint);
      const res = await fetch(`${BACKEND}/subscriptions/webpush/${deviceId}`, {
        method: "DELETE",
        credentials: "include",
        headers: headers(),
      });
      if (!res.ok && res.status !== 404) {
        const payload = await res.json().catch(() => ({}));
        throw new Error(payload.detail || `解绑失败：HTTP ${res.status}`);
      }
      await subscription.unsubscribe();
      setNotice({ tone: "good", text: "当前浏览器已停止接收 Otomo 推送。" });
      await loadWebpush();
    } catch (error) {
      setNotice({ tone: "bad", text: error instanceof Error ? error.message : "浏览器推送解绑失败。" });
    } finally {
      setPushBusy(false);
    }
  }

  function headers(extra?: Record<string, string>) {
    return { ...(extra || {}), ...(csrf ? { "x-otomo-csrf": csrf } : {}) };
  }

  async function loadRules(token = csrf) {
    setBusy(true);
    try {
      const res = await fetch(`${BACKEND}/subscriptions/rules`, { credentials: "include", headers: token ? { "x-otomo-csrf": token } : {} });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok || !payload.ok) {
        setNotice({ tone: "bad", text: payload.detail || payload.error || `读取失败：HTTP ${res.status}` });
        return;
      }
      setRules(list(payload.rules));
      setDeliveries(list(payload.deliveries));
    } finally {
      setBusy(false);
    }
  }

  function toggleDraftChannel(channel: string) {
    setDraft((prev) => {
      const set = new Set(prev.channels);
      if (set.has(channel)) set.delete(channel);
      else set.add(channel);
      if (!set.size) set.add("inbox");
      return { ...prev, channels: Array.from(set) };
    });
  }

  function applyPreset(preset: typeof PRESETS[number]) {
    setDraft((previous) => ({
      ...previous,
      kind: preset.kind,
      title: preset.label,
      hour: preset.hour,
      minute: 0,
      weekday: preset.weekday,
    }));
    setNotice({ tone: "good", text: `已选“${preset.label}”，确认时间和接收方式后即可创建。` });
  }

  const draftFilters = useMemo(() => {
    try {
      const value = JSON.parse(draft.filters_json || "{}");
      return value && typeof value === "object" && !Array.isArray(value) ? value : {};
    } catch {
      return null;
    }
  }, [draft.filters_json]);

  async function createRule() {
    if (draftFilters === null) {
      setNotice({ tone: "bad", text: "filters JSON 格式错误。" });
      return;
    }
    setBusy(true);
    try {
      const schedule: AnyRecord = {
        timezone: draft.timezone,
        hour: Number(draft.hour),
        minute: Number(draft.minute),
        weekday: draft.kind === "weekly_digest" ? Number(draft.weekday) : null,
      };
      if (draft.kind === "monthly_report") schedule.day_of_month = Number(draft.day_of_month);
      if (Number(draft.interval_minutes) >= 5) schedule.interval_minutes = Number(draft.interval_minutes);
      const res = await fetch(`${BACKEND}/subscriptions/rules`, {
        method: "POST",
        credentials: "include",
        headers: headers({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          kind: draft.kind,
          title: draft.title,
          filters: draftFilters,
          schedule,
          channels: draft.channels,
          template: draft.template,
          webhook_format: draft.webhook_format,
          webhook_url: draft.webhook_url,
          email: draft.email,
          quiet_hours: { start: draft.quiet_start, end: draft.quiet_end },
        }),
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok || !payload.ok) {
        setNotice({ tone: "bad", text: payload.detail || payload.error || `创建失败：HTTP ${res.status}` });
        return;
      }
      setNotice({ tone: "good", text: "订阅已创建。" });
      await loadRules();
    } finally {
      setBusy(false);
    }
  }

  async function patchRule(rule: AnyRecord, updates: AnyRecord) {
    setBusy(true);
    try {
      const res = await fetch(`${BACKEND}/subscriptions/rules/${rule.id}`, {
        method: "PATCH",
        credentials: "include",
        headers: headers({ "Content-Type": "application/json" }),
        body: JSON.stringify(updates),
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok || !payload.ok) {
        setNotice({ tone: "bad", text: payload.detail || payload.error || `更新失败：HTTP ${res.status}` });
        return;
      }
      await loadRules();
    } finally {
      setBusy(false);
    }
  }

  async function deleteRule(rule: AnyRecord) {
    setBusy(true);
    try {
      const res = await fetch(`${BACKEND}/subscriptions/rules/${rule.id}`, {
        method: "DELETE",
        credentials: "include",
        headers: headers(),
      });
      if (!res.ok) {
        const payload = await res.json().catch(() => ({}));
        setNotice({ tone: "bad", text: payload.detail || `删除失败：HTTP ${res.status}` });
        return;
      }
      await loadRules();
    } finally {
      setBusy(false);
    }
  }

  async function testRule(rule: AnyRecord) {
    setBusy(true);
    try {
      const res = await fetch(`${BACKEND}/subscriptions/rules/${rule.id}/test`, {
        method: "POST",
        credentials: "include",
        headers: headers({ "Content-Type": "application/json" }),
        body: JSON.stringify({}),
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok || !payload.ok) {
        setNotice({ tone: "bad", text: payload.detail || payload.error || `测试失败：HTTP ${res.status}` });
        return;
      }
      const status = String(payload.delivery?.status || "pending");
      setNotice({ tone: status === "sent" ? "good" : "warn", text: `测试完成：${DELIVERY_STATUS[status] || "等待结果"}` });
      await loadRules();
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="share-page">
      <header className="share-hero">
        <div>
          <div className="share-kicker">提醒与周报</div>
          <h1>订阅提醒</h1>
          <p>选择你关心的内容和收到提醒的时间；复杂的过滤与外部推送放在高级设置里。</p>
          <div className="share-badges">
            <span>{auth?.authenticated ? `Bangumi @${auth.username}` : "未登录"}</span>
            <span>{rules.length} 条订阅</span>
            <span>{deliveries.length} 次发送记录</span>
          </div>
        </div>
      </header>
      {notice && <div className={`auth-notice ${notice.tone}`}>{notice.text}</div>}
      {!auth?.authenticated ? (
        <AuthGate eyebrow="ACTIVE DELIVERY" title="让 Otomo 在合适的时间主动找到你" description="订阅规则和投递设备按账户隔离。连接后可以配置每日追番、周报、好友动态和口碑异动。" features={["站内与浏览器通知", "Discord / 邮件 / Webhook", "静默时段与过滤条件"]} />
      ) : (
        <>
          <section className="share-section">
            <div className="section-heading-row">
              <div>
                <h2>浏览器设备</h2>
                <p>允许后，即使没有打开 Otomo 页面，这台设备也能收到你订阅的提醒。</p>
              </div>
              <span className={`badge ${pushConfig.enabled ? "good" : "warn"}`}>
                {pushConfig.enabled ? `${list(pushConfig.devices).length} 台已绑定` : "服务器未配置 VAPID"}
              </span>
            </div>
            <div className="settings-actions">
              <button className="inline-action primary" onClick={enableBrowserPush} disabled={pushBusy || !pushConfig.enabled}>
                允许当前浏览器
              </button>
              <button className="inline-action" onClick={disableBrowserPush} disabled={pushBusy}>
                停止当前浏览器
              </button>
            </div>
            {list(pushConfig.devices).length ? (
              <div className="compact-list">
                {list(pushConfig.devices).map((device) => (
                  <span key={device.id}>{device.user_agent || "浏览器设备"} · {device.updated_at}</span>
                ))}
              </div>
            ) : null}
          </section>
          <section className="share-section">
            <h2>新建订阅</h2>
            <p className="card-note">可以从常用模板开始，也可以在下面自行选择。</p>
            <div className="subscription-presets">
              {PRESETS.map((preset) => (
                <button type="button" className={draft.kind === preset.kind ? "active" : ""} key={preset.kind} onClick={() => applyPreset(preset)}>
                  <strong>{preset.label}</strong><span>{preset.description}</span>
                </button>
              ))}
            </div>
            <div className="settings-grid">
              <label className="setting-field">
                <span>类型</span>
                <select value={draft.kind} onChange={(e) => setDraft((p) => ({ ...p, kind: e.target.value }))}>
                  {KINDS.filter(([value]) => value !== "anime_follow").map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
              </label>
              <label className="setting-field">
                <span>几点</span>
                <input type="number" min={0} max={23} value={draft.hour} onChange={(e) => setDraft((p) => ({ ...p, hour: Number(e.target.value) }))} />
              </label>
              <label className="setting-field">
                <span>几分</span>
                <input type="number" min={0} max={59} value={draft.minute} onChange={(e) => setDraft((p) => ({ ...p, minute: Number(e.target.value) }))} />
              </label>
              {draft.kind === "weekly_digest" ? (
                <label className="setting-field"><span>每周哪天</span><select value={draft.weekday} onChange={(e) => setDraft((p) => ({ ...p, weekday: Number(e.target.value) }))}>{WEEKDAYS.map((label, value) => <option value={value} key={label}>{label}</option>)}</select></label>
              ) : null}
              {draft.kind === "monthly_report" ? (
                <label className="setting-field"><span>每月几号</span><input type="number" min={1} max={31} value={draft.day_of_month} onChange={(e) => setDraft((p) => ({ ...p, day_of_month: Number(e.target.value) }))} /></label>
              ) : null}
            </div>
            <div className="settings-options">
              {CHANNELS.filter(([value]) => value !== "webhook").map(([value, label]) => (
                <label className="settings-check" key={value}>
                  <input
                    type="checkbox"
                    checked={draft.channels.includes(value)}
                    onChange={() => toggleDraftChannel(value)}
                    disabled={value === "webpush" && !pushConfig.enabled}
                  />
                  <span>{label}</span>
                </label>
              ))}
            </div>
            <details className="subscription-advanced">
              <summary>高级设置</summary>
              <div className="settings-grid">
                <label className="setting-field wide"><span>提醒标题</span><input value={draft.title} onChange={(e) => setDraft((p) => ({ ...p, title: e.target.value }))} placeholder="留空使用默认标题" /></label>
                <label className="setting-field"><span>时区</span><input value={draft.timezone} onChange={(e) => setDraft((p) => ({ ...p, timezone: e.target.value }))} /></label>
                <label className="setting-field"><span>重复间隔（分钟）</span><input type="number" min={0} max={10080} value={draft.interval_minutes} onChange={(e) => setDraft((p) => ({ ...p, interval_minutes: Number(e.target.value) }))} /></label>
                <label className="setting-field"><span>内容详略</span><select value={draft.template} onChange={(e) => setDraft((p) => ({ ...p, template: e.target.value }))}><option value="brief">精简</option><option value="normal">标准</option><option value="detailed">详细</option></select></label>
                <label className="setting-field wide"><span>Email 地址</span><input value={draft.email} onChange={(e) => setDraft((p) => ({ ...p, email: e.target.value }))} placeholder="you@example.com" /></label>
                <label className="setting-field"><span>免打扰开始</span><input type="time" value={draft.quiet_start} onChange={(e) => setDraft((p) => ({ ...p, quiet_start: e.target.value }))} /></label>
                <label className="setting-field"><span>免打扰结束</span><input type="time" value={draft.quiet_end} onChange={(e) => setDraft((p) => ({ ...p, quiet_end: e.target.value }))} /></label>
                <label className="settings-check"><input type="checkbox" checked={draft.channels.includes("webhook")} onChange={() => toggleDraftChannel("webhook")} /><span>发送到 Webhook</span></label>
                <label className="setting-field"><span>Webhook 类型</span><select value={draft.webhook_format} onChange={(e) => setDraft((p) => ({ ...p, webhook_format: e.target.value }))}><option value="generic">通用</option><option value="serverchan">Server 酱</option><option value="telegram">Telegram</option><option value="discord">Discord</option><option value="feishu">飞书</option></select></label>
                <label className="setting-field wide"><span>Webhook 地址</span><input value={draft.webhook_url} onChange={(e) => setDraft((p) => ({ ...p, webhook_url: e.target.value }))} placeholder="https://..." /></label>
                <label className="setting-field wide"><span>自定义过滤条件（JSON）</span><input value={draft.filters_json} onChange={(e) => setDraft((p) => ({ ...p, filters_json: e.target.value }))} /></label>
              </div>
            </details>
            <button className="inline-action primary" onClick={createRule} disabled={busy}>创建订阅</button>
          </section>
          <section className="share-section">
            <h2>订阅规则</h2>
            <div className="share-list">
              {rules.map((rule) => (
                <div className="rating-card" key={rule.id}>
                  <div className="rating-source">{KIND_LABEL[rule.kind] || rule.kind} · {rule.enabled ? "运行中" : "已暂停"}</div>
                  <div className="card-title">{rule.title}</div>
                  <p className="card-note">
                    {rule.schedule?.interval_minutes ? `每 ${rule.schedule.interval_minutes} 分钟` : `${String(rule.schedule?.hour ?? 0).padStart(2, "0")}:${String(rule.schedule?.minute ?? 0).padStart(2, "0")}`}
                    {rule.schedule?.weekday != null ? ` · ${WEEKDAYS[Number(rule.schedule.weekday)] || ""}` : ""}
                    {rule.schedule?.day_of_month != null ? ` · 每月 ${rule.schedule.day_of_month} 日` : ""}
                    {rule.schedule?.timezone ? ` · ${rule.schedule.timezone}` : ""}
                  </p>
                  <div className="evidence-row tight">
                    {list(rule.channels).map((ch: any) => <span className="badge dim" key={ch}>{CHANNEL_LABEL[ch] || ch}</span>)}
                    <span className="badge dim">{TEMPLATE_LABEL[rule.template] || rule.template}</span>
                  </div>
                  <div className="settings-actions">
                    <button className="inline-action" onClick={() => patchRule(rule, { enabled: !rule.enabled })} disabled={busy}>{rule.enabled ? "暂停" : "启用"}</button>
                    <button className="inline-action" onClick={() => testRule(rule)} disabled={busy}>测试</button>
                    <button className="inline-action" onClick={() => deleteRule(rule)} disabled={busy}>删除</button>
                  </div>
                </div>
              ))}
            </div>
          </section>
          <section className="share-section">
            <h2>推送记录</h2>
            <div className="share-list">
              {deliveries.slice(0, 30).map((d) => (
                <div className="rating-card" key={d.id}>
                  <div className="rating-source">{KIND_LABEL[d.kind] || d.kind} · {DELIVERY_STATUS[d.status] || d.status} · {d.created_at}</div>
                  <div className="card-title">{d.title || d.hit_key}</div>
                  {d.error ? <p className="card-note">{d.error}</p> : null}
                  <div className="compact-list inline">
                    {list(d.deliveries).map((row: any, i) => <span key={i}>{CHANNEL_LABEL[row.channel] || row.channel} · {row.ok ? "已发送" : row.error || "发送失败"}</span>)}
                  </div>
                </div>
              ))}
            </div>
          </section>
        </>
      )}
    </main>
  );
}
