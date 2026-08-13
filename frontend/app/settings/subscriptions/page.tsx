"use client";

import { useEffect, useMemo, useState } from "react";

import { AuthGate } from "../../../components/auth-gate";

const BACKEND = process.env.NEXT_PUBLIC_BACKEND ?? "http://localhost:8000";

type AnyRecord = Record<string, any>;
type Notice = { tone: "good" | "warn" | "bad"; text: string };

const KINDS = [
  ["weekly_digest", "每周周报"],
  ["daily_airing", "每日追番"],
  ["monthly_report", "月报"],
  ["rss_release", "RSS 新资源"],
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

function list(value: any): AnyRecord[] {
  return Array.isArray(value) ? value : [];
}

export default function SubscriptionSettingsPage() {
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
    const res = await fetch(`${BACKEND}/auth/session`, { credentials: "include" });
    const payload = await res.json().catch(() => ({}));
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
      setNotice({ tone: "good", text: "订阅规则已同步。" });
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
      setNotice({ tone: payload.delivery?.status === "sent" ? "good" : "warn", text: `测试完成：${payload.delivery?.status || "unknown"}` });
      await loadRules();
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="share-page">
      <header className="share-hero">
        <div>
          <div className="share-kicker">Otomo Settings</div>
          <h1>主动订阅</h1>
          <p>订阅对象、触发条件、过滤器、渠道、模板和推送记录统一管理。服务器部署后可作为常驻 scheduler 的配置页。</p>
          <div className="share-badges">
            <span>{auth?.authenticated ? `Bangumi @${auth.username}` : "未登录"}</span>
            <span>{rules.length} rules</span>
            <span>{deliveries.length} deliveries</span>
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
                <p>授权后，即使没有打开 Otomo 页面，服务器上的订阅调度器也能向这台设备推送。生产环境需要 HTTPS。</p>
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
            <div className="settings-grid">
              <label className="setting-field">
                <span>类型</span>
                <select value={draft.kind} onChange={(e) => setDraft((p) => ({ ...p, kind: e.target.value }))}>
                  {KINDS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
              </label>
              <label className="setting-field wide">
                <span>标题</span>
                <input value={draft.title} onChange={(e) => setDraft((p) => ({ ...p, title: e.target.value }))} placeholder="留空使用默认标题" />
              </label>
              <label className="setting-field">
                <span>小时</span>
                <input type="number" min={0} max={23} value={draft.hour} onChange={(e) => setDraft((p) => ({ ...p, hour: Number(e.target.value) }))} />
              </label>
              <label className="setting-field">
                <span>分钟</span>
                <input type="number" min={0} max={59} value={draft.minute} onChange={(e) => setDraft((p) => ({ ...p, minute: Number(e.target.value) }))} />
              </label>
              <label className="setting-field">
                <span>周几</span>
                <input type="number" min={0} max={6} value={draft.weekday} onChange={(e) => setDraft((p) => ({ ...p, weekday: Number(e.target.value) }))} />
              </label>
              <label className="setting-field">
                <span>月几</span>
                <input type="number" min={1} max={31} value={draft.day_of_month} onChange={(e) => setDraft((p) => ({ ...p, day_of_month: Number(e.target.value) }))} />
              </label>
              <label className="setting-field">
                <span>时区</span>
                <input value={draft.timezone} onChange={(e) => setDraft((p) => ({ ...p, timezone: e.target.value }))} />
              </label>
              <label className="setting-field">
                <span>间隔分钟</span>
                <input type="number" min={0} max={10080} value={draft.interval_minutes} onChange={(e) => setDraft((p) => ({ ...p, interval_minutes: Number(e.target.value) }))} />
              </label>
              <label className="setting-field">
                <span>模板</span>
                <select value={draft.template} onChange={(e) => setDraft((p) => ({ ...p, template: e.target.value }))}>
                  <option value="brief">brief</option>
                  <option value="normal">normal</option>
                  <option value="detailed">detailed</option>
                </select>
              </label>
              <label className="setting-field">
                <span>Webhook 格式</span>
                <select value={draft.webhook_format} onChange={(e) => setDraft((p) => ({ ...p, webhook_format: e.target.value }))}>
                  <option value="generic">generic</option>
                  <option value="serverchan">serverchan</option>
                  <option value="telegram">telegram</option>
                  <option value="discord">discord</option>
                  <option value="feishu">feishu</option>
                </select>
              </label>
              <label className="setting-field wide">
                <span>Webhook URL</span>
                <input value={draft.webhook_url} onChange={(e) => setDraft((p) => ({ ...p, webhook_url: e.target.value }))} placeholder="https://..." />
              </label>
              <label className="setting-field wide">
                <span>Email</span>
                <input value={draft.email} onChange={(e) => setDraft((p) => ({ ...p, email: e.target.value }))} placeholder="you@example.com" />
              </label>
              <label className="setting-field">
                <span>静默开始</span>
                <input type="time" value={draft.quiet_start} onChange={(e) => setDraft((p) => ({ ...p, quiet_start: e.target.value }))} />
              </label>
              <label className="setting-field">
                <span>静默结束</span>
                <input type="time" value={draft.quiet_end} onChange={(e) => setDraft((p) => ({ ...p, quiet_end: e.target.value }))} />
              </label>
              <label className="setting-field wide">
                <span>filters JSON</span>
                <input value={draft.filters_json} onChange={(e) => setDraft((p) => ({ ...p, filters_json: e.target.value }))} />
              </label>
            </div>
            <div className="settings-options">
              {CHANNELS.map(([value, label]) => (
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
            <button className="inline-action primary" onClick={createRule} disabled={busy}>创建订阅</button>
          </section>
          <section className="share-section">
            <h2>订阅规则</h2>
            <div className="share-list">
              {rules.map((rule) => (
                <div className="rating-card" key={rule.id}>
                  <div className="rating-source">{rule.kind} · {rule.enabled ? "enabled" : "paused"}</div>
                  <div className="card-title">{rule.title}</div>
                  <p className="card-note">
                    {rule.schedule?.timezone} · {rule.schedule?.interval_minutes ? `每 ${rule.schedule.interval_minutes} 分钟` : `time ${rule.schedule?.hour}:${String(rule.schedule?.minute ?? 0).padStart(2, "0")}`}
                    {rule.schedule?.weekday != null ? ` · weekday ${rule.schedule.weekday}` : ""}
                    {rule.schedule?.day_of_month != null ? ` · day ${rule.schedule.day_of_month}` : ""}
                    {rule.last_hit_key ? ` · last ${rule.last_hit_key}` : ""}
                  </p>
                  <div className="evidence-row tight">
                    {list(rule.channels).map((ch: any) => <span className="badge dim" key={ch}>{ch}</span>)}
                    <span className="badge dim">{rule.template}</span>
                    {rule.webhook_format ? <span className="badge dim">{rule.webhook_format}</span> : null}
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
                  <div className="rating-source">{d.kind} · {d.status} · {d.created_at}</div>
                  <div className="card-title">{d.title || d.hit_key}</div>
                  {d.error ? <p className="card-note">{d.error}</p> : null}
                  <div className="compact-list inline">
                    {list(d.deliveries).map((row: any, i) => <span key={i}>{row.channel} · {row.ok ? "ok" : row.error || "failed"}</span>)}
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
