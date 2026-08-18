"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Bell, Check, ChevronRight, Command, Download, ExternalLink, Image as ImageIcon,
  ListChecks, LoaderCircle, Monitor, Moon, Palette, RefreshCw, Search, Sun,
  Trash2, WifiOff, X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { productFetch } from "../lib/api";
import { useExperience } from "../lib/experience";
import { ComparePanel } from "../app/panels/media";

type AnyRow = Record<string, any>;

function ModalFrame({ title, subtitle, onClose, children, wide = false }: {
  title: string; subtitle?: string; onClose: () => void; children: React.ReactNode; wide?: boolean;
}) {
  return (
    <div className="global-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className={`global-modal${wide ? " wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <header className="global-modal-head"><div><strong>{title}</strong>{subtitle ? <span>{subtitle}</span> : null}</div><button className="icon-plain" onClick={onClose} title="关闭"><X size={18} /></button></header>
        {children}
      </section>
    </div>
  );
}

export function CommandPalette() {
  const exp = useExperience();
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<AnyRow[]>([]);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (exp.commandOpen) { setQuery(""); setResults([]); setTimeout(() => inputRef.current?.focus(), 30); } }, [exp.commandOpen]);
  useEffect(() => {
    if (!exp.commandOpen || query.trim().length < 2) { setResults([]); return; }
    const timer = window.setTimeout(async () => {
      setBusy(true);
      try { const payload = await productFetch(`/product/search?q=${encodeURIComponent(query.trim())}&limit=7`); setResults(payload.data?.subjects || []); }
      catch { setResults([]); }
      finally { setBusy(false); }
    }, 260);
    return () => window.clearTimeout(timer);
  }, [query, exp.commandOpen]);
  if (!exp.commandOpen) return null;
  const commands = [
    ["/", "今天看什么", "今日追番与落后进度"], ["/chat", "打开对话", "向 Otomo 提问"],
    ["/discover", "发现作品", "季番导视与跨媒介推荐"], ["/library", "查看收藏", "仪表盘与月报"],
    ["/workspace", "我的工作区", "保存视图与自定义清单"], ["/friends", "打开好友圈", "追番动态与口味同步率"],
    ["/settings/subscriptions", "订阅中心", "推送规则与投递记录"],
  ];
  function go(href: string) { exp.setCommandOpen(false); router.push(href); }
  return (
    <ModalFrame title="搜索与命令" subtitle="页面、作品和快捷动作" onClose={() => exp.setCommandOpen(false)}>
      <div className="command-search"><Search size={18} /><input ref={inputRef} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索作品，或选择一个操作" />{busy ? <LoaderCircle className="spin" size={17} /> : <kbd>Esc</kbd>}</div>
      <div className="command-results">
        {query.trim().length >= 2 ? (
          <>{results.map((item) => <button className="command-row" key={item.id} onClick={() => go(`/subject/${item.id}`)}>{item.image ? <img src={item.image} alt="" /> : <span className="command-placeholder" />}<span><strong>{item.name_cn || item.name}</strong><small>{item.type_name || "Bangumi 条目"}{item.score ? ` · ${item.score}` : ""}</small></span><ChevronRight size={16} /></button>)}{!busy && !results.length ? <div className="overlay-empty">没有找到明确候选，可以去对话里描述更多别名或上下文。</div> : null}</>
        ) : commands.map(([href, title, desc]) => <button className="command-row local" key={href} onClick={() => go(href)}><Command size={17} /><span><strong>{title}</strong><small>{desc}</small></span><ChevronRight size={16} /></button>)}
      </div>
    </ModalFrame>
  );
}

export function NotificationCenter() {
  const exp = useExperience();
  const [items, setItems] = useState<AnyRow[]>([]);
  const [busy, setBusy] = useState(false);
  async function load() {
    if (!exp.authenticated) return;
    setBusy(true); try { const payload = await productFetch("/product/inbox"); setItems(payload.data?.items || []); } finally { setBusy(false); }
  }
  useEffect(() => { if (exp.notificationOpen) void load(); }, [exp.notificationOpen]);
  async function mark(item: AnyRow, unread: boolean) {
    await productFetch(`/product/inbox/${item.id}`, { method: "PATCH", headers: { "Content-Type": "application/json", "x-otomo-csrf": exp.csrf }, body: JSON.stringify({ unread }) });
    setItems((rows) => rows.map((x) => x.id === item.id ? { ...x, unread } : x)); void exp.refreshUnread();
  }
  async function readAll() {
    await productFetch("/product/inbox/read-all", { method: "POST", headers: { "x-otomo-csrf": exp.csrf } });
    setItems((rows) => rows.map((x) => ({ ...x, unread: false }))); void exp.refreshUnread();
  }
  if (!exp.notificationOpen) return null;
  return (
    <ModalFrame title="通知中心" subtitle={`${exp.unread} 条未读`} onClose={() => exp.setNotificationOpen(false)}>
      <div className="overlay-toolbar"><button className="button-secondary" onClick={() => void load()} disabled={busy}><RefreshCw size={15} />刷新</button>{items.some((x) => x.unread) ? <button className="button-secondary" onClick={() => void readAll()}><Check size={15} />全部已读</button> : null}</div>
      {!exp.authenticated ? <div className="overlay-empty">连接 Bangumi 后，订阅周报、每日追番和系统提醒会集中显示在这里。</div> : null}
      <div className="notification-list">{items.map((item) => { const sections = item.payload?.sections || []; return <article className={item.unread ? "unread" : ""} key={item.id}><button className="notification-main" onClick={() => void mark(item, !item.unread)}><span className="notification-dot" /><span><strong>{item.title}</strong><small>{String(item.created_at || "").replace("T", " ").slice(0, 16)}</small></span></button>{sections.slice(0, 2).map((section: AnyRow, i: number) => <div className="notification-section" key={i}><b>{section.title}</b><span>{(section.items || []).slice(0, 3).map((x: AnyRow) => x.name || x.title).filter(Boolean).join(" · ")}</span></div>)}</article>; })}</div>
      {exp.authenticated && !busy && !items.length ? <div className="overlay-empty">暂时没有通知。你可以在订阅中心开启每日追番、周报或异动提醒。</div> : null}
      <footer className="overlay-footer"><Link href="/settings/subscriptions" onClick={() => exp.setNotificationOpen(false)}>管理订阅 <ChevronRight size={15} /></Link></footer>
    </ModalFrame>
  );
}

export function WatchQuickDrawer() {
  const exp = useExperience();
  const [data, setData] = useState<AnyRow | null>(null);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<AnyRow | null>(null);
  const [error, setError] = useState("");
  async function load() { setBusy(true); setError(""); try { const payload = await productFetch("/today"); setData(payload.data); } catch (e) { setError(String(e)); } finally { setBusy(false); } }
  useEffect(() => { if (exp.watchOpen && exp.authenticated) void load(); }, [exp.watchOpen]);
  async function prepare(item: AnyRow) {
    const upTo = Number(item.my_ep || 0) + 1;
    const payload = await productFetch("/actions/prepare-write", { method: "POST", headers: { "Content-Type": "application/json", "x-otomo-csrf": exp.csrf }, body: JSON.stringify({ operation: "mark_episodes_watched", subject_id: item.id, subject_name: item.name_cn || item.name, collection_type: 3, up_to_episode: upTo, reason: `快捷追番标记看到第 ${upTo} 集` }) });
    setPending(payload.data?.action || null);
  }
  async function confirm(ok: boolean) {
    if (!pending?.id) return;
    await productFetch(`/actions/${ok ? "confirm" : "cancel"}`, { method: "POST", headers: { "Content-Type": "application/json", "x-otomo-csrf": exp.csrf }, body: JSON.stringify({ action_id: pending.id, reason: ok ? "" : "用户取消" }) });
    setPending(null); if (ok) await load();
  }
  if (!exp.watchOpen) return null;
  const rows = [...(data?.today || []), ...(data?.backlog || [])].filter((x, i, all) => all.findIndex((y) => y.id === x.id) === i).slice(0, 10);
  return (
    <ModalFrame title="追番快捷抽屉" subtitle="今日更新与落后进度" onClose={() => exp.setWatchOpen(false)}>
      {!exp.online ? <div className="offline-notice"><WifiOff size={16} />当前离线，写回操作不可用。</div> : null}
      {!exp.authenticated ? <div className="overlay-empty">连接 Bangumi 后可以读取进度，并在二次确认后点格子。</div> : null}
      {error ? <div className="surface-error">{error}</div> : null}
      {busy && !data ? <div className="overlay-empty">正在核对放送日历和分集进度…</div> : null}
      <div className="quick-watch-list">{rows.map((item) => <article key={item.id}>{item.image ? <img src={item.image} alt="" /> : <span className="quick-cover" />}<span><Link href={`/subject/${item.id}`} onClick={() => exp.setWatchOpen(false)}>{item.name_cn || item.name}</Link><small>看到 {item.my_ep || 0}{item.aired_ep ? ` / 已播 ${item.aired_ep}` : ""}{item.behind ? ` · 落后 ${item.behind} 集` : " · 已跟上"}</small></span><button className="icon-plain" disabled={!exp.online || (item.aired_ep && item.my_ep >= item.aired_ep)} title="看完下一集" onClick={() => void prepare(item)}><Check size={17} /></button></article>)}</div>
      {pending ? <div className="inline-confirm"><strong>确认写回 Bangumi？</strong><span>{pending.summary}</span><div><button className="button-secondary" onClick={() => void confirm(false)}>取消</button><button className="button-primary" onClick={() => void confirm(true)}>确认</button></div></div> : null}
      <footer className="overlay-footer"><Link href="/" onClick={() => exp.setWatchOpen(false)}>打开完整今日页 <ChevronRight size={15} /></Link></footer>
    </ModalFrame>
  );
}

export function AppearanceDrawer() {
  const exp = useExperience();
  const fileRef = useRef<HTMLInputElement>(null);
  const [message, setMessage] = useState("");
  const [installEvent, setInstallEvent] = useState<any>(null);
  useEffect(() => { const handler = (event: Event) => { event.preventDefault(); setInstallEvent(event); }; window.addEventListener("beforeinstallprompt", handler); return () => window.removeEventListener("beforeinstallprompt", handler); }, []);
  if (!exp.settingsOpen) return null;
  const a = exp.appearance;
  async function upload(file?: File) { if (!file) return; try { await exp.saveWallpaper(file); setMessage("壁纸已保存在这台设备"); } catch (e) { setMessage(String(e)); } }
  return (
    <ModalFrame title="外观与显示" subtitle="设置只影响当前浏览器" onClose={() => exp.setSettingsOpen(false)}>
      <div className="appearance-form">
        <fieldset><legend>主题</legend><div className="choice-grid">{([["system", Monitor, "跟随系统"], ["light", Sun, "浅色"], ["dark", Moon, "深色"]] as const).map(([mode, Icon, label]) => <button className={a.theme === mode ? "active" : ""} key={mode} onClick={() => exp.setAppearance({ theme: mode })}><Icon size={17} />{label}</button>)}</div></fieldset>
        <fieldset><legend>背景壁纸</legend><input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={(e) => void upload(e.target.files?.[0])} /><div className="wallpaper-actions"><button className="button-secondary" onClick={() => fileRef.current?.click()}><ImageIcon size={16} />选择本地图片</button>{exp.wallpaperUrl ? <button className="button-secondary danger" onClick={() => void exp.clearWallpaper()}><Trash2 size={16} />移除</button> : null}</div>{exp.wallpaperUrl ? <><label className="toggle-setting"><span>显示壁纸</span><input type="checkbox" checked={a.wallpaperEnabled} onChange={(e) => exp.setAppearance({ wallpaperEnabled: e.target.checked })} /></label><label><span>背景可见度 {a.wallpaperOpacity}%</span><input type="range" min="8" max="55" value={a.wallpaperOpacity} onChange={(e) => exp.setAppearance({ wallpaperOpacity: Number(e.target.value) })} /></label><label><span>模糊 {a.wallpaperBlur}px</span><input type="range" min="0" max="18" value={a.wallpaperBlur} onChange={(e) => exp.setAppearance({ wallpaperBlur: Number(e.target.value) })} /></label><label><span>位置</span><select value={a.wallpaperPosition} onChange={(e) => exp.setAppearance({ wallpaperPosition: e.target.value })}><option value="center">居中</option><option value="top">顶部</option><option value="bottom">底部</option></select></label></> : null}<small>原图仅保存在浏览器 IndexedDB，不会上传到 Otomo 服务器。</small></fieldset>
        <fieldset><legend>阅读体验</legend><label><span>内容密度</span><select value={a.density} onChange={(e) => exp.setAppearance({ density: e.target.value as any })}><option value="comfortable">舒适</option><option value="compact">紧凑</option></select></label><label className="toggle-setting"><span>高对比度</span><input type="checkbox" checked={a.highContrast} onChange={(e) => exp.setAppearance({ highContrast: e.target.checked })} /></label><label className="toggle-setting"><span>减少动态效果</span><input type="checkbox" checked={a.reduceMotion} onChange={(e) => exp.setAppearance({ reduceMotion: e.target.checked })} /></label></fieldset>
        <fieldset><legend>安装应用</legend><p>安装后可从桌面启动；账户数据仍需联网，已访问的应用壳可离线打开。</p><button className="button-secondary" disabled={!installEvent} onClick={() => { installEvent?.prompt(); setInstallEvent(null); }}><Download size={16} />{installEvent ? "安装 Otomo" : "当前浏览器暂无安装提示"}</button></fieldset>
        {message ? <div className="inline-notice">{message}</div> : null}
      </div>
    </ModalFrame>
  );
}

export function CompareDrawer() {
  const exp = useExperience();
  const [data, setData] = useState<AnyRow | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<AnyRow[]>([]);
  const ids = useMemo(() => exp.compareItems.map((x) => x.id), [exp.compareItems]);
  async function compare() { setBusy(true); setError(""); try { const payload = await productFetch("/product/compare", { method: "POST", headers: { "Content-Type": "application/json", "x-otomo-csrf": exp.csrf }, body: JSON.stringify({ subject_ids: ids }) }); setData(payload.data); } catch (e) { setError(String(e)); } finally { setBusy(false); } }
  useEffect(() => { setData(null); }, [ids.join(",")]);
  useEffect(() => {
    if (!exp.compareOpen || query.trim().length < 2) { setResults([]); return; }
    const timer = window.setTimeout(() => productFetch(`/product/search?q=${encodeURIComponent(query.trim())}&limit=6`).then((payload) => setResults(payload.data?.subjects || [])).catch(() => setResults([])), 250);
    return () => window.clearTimeout(timer);
  }, [query, exp.compareOpen]);
  if (!exp.compareOpen) return null;
  return (
    <ModalFrame wide title="作品对比" subtitle="选择 2~3 部作品查看硬指标与标签差异" onClose={() => exp.setCompareOpen(false)}>
      <div className="compare-tray">{exp.compareItems.map((item) => <span key={item.id}>{item.image ? <img src={item.image} alt="" /> : null}<b>{item.name}</b><button className="icon-plain" onClick={() => exp.removeCompareItem(item.id)}><X size={14} /></button></span>)}{exp.compareItems.length < 3 ? <label className="compare-search"><Search size={15} /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索并加入作品" /></label> : null}</div>
      {results.length && exp.compareItems.length < 3 ? <div className="compare-search-results">{results.filter((row) => !ids.includes(Number(row.id))).map((row) => <button key={row.id} onClick={() => { exp.addCompareItem({ id: Number(row.id), name: row.name_cn || row.name, image: row.image }); setQuery(""); setResults([]); }}>{row.image ? <img src={row.image} alt="" /> : null}<span>{row.name_cn || row.name}</span><PlusIcon /></button>)}</div> : null}
      <div className="overlay-toolbar"><button className="button-primary" disabled={ids.length < 2 || busy} onClick={() => void compare()}>{busy ? <LoaderCircle className="spin" size={16} /> : <ListChecks size={16} />}开始对比</button>{exp.compareItems.length ? <button className="button-secondary" onClick={exp.clearCompareItems}>清空</button> : null}</div>
      {error ? <div className="surface-error">{error}</div> : null}{data ? <ComparePanel data={data} /> : null}
    </ModalFrame>
  );
}

function PlusIcon() { return <span aria-hidden="true">+</span>; }

export function TaskCenter() {
  const exp = useExperience();
  const active = exp.tasks.filter((x) => x.status !== "success").slice(0, 4);
  if (!active.length) return null;
  return <aside className="task-center" aria-label="任务提示">{active.map((task) => <div className={task.status} key={task.id}>{task.status === "running" ? <LoaderCircle className="spin" size={15} /> : task.status === "interrupted" ? <RefreshCw size={15} /> : <X size={15} />}<button onClick={() => window.dispatchEvent(new CustomEvent("otomo:navigate", { detail: { href: task.href } }))}><strong>{task.label}</strong><small>{task.status === "running" ? "正在处理…" : task.status === "interrupted" ? "页面刷新使任务中断，点击返回重试" : task.error || "执行失败，请稍后重试"}</small></button><button className="icon-plain task-dismiss" onClick={() => exp.dismissTask(task.id)} title="关闭这条提示" aria-label={`关闭“${task.label}”提示`}><X size={14} /></button></div>)}</aside>;
}

export function ExperienceOverlays() {
  return <><CommandPalette /><NotificationCenter /><WatchQuickDrawer /><AppearanceDrawer /><CompareDrawer /><TaskCenter /></>;
}
