"use client";

import { usePathname, useRouter } from "next/navigation";
import {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
  type ReactNode,
} from "react";

import { authSession, productFetch } from "./api";

export type ThemeMode = "system" | "light" | "dark";
export type DensityMode = "comfortable" | "compact";
export type Appearance = {
  theme: ThemeMode;
  density: DensityMode;
  highContrast: boolean;
  reduceMotion: boolean;
  wallpaperEnabled: boolean;
  wallpaperOpacity: number;
  wallpaperBlur: number;
  wallpaperPosition: string;
};
export type CompareItem = { id: number; name: string; image?: string; type?: string };
export type TaskRecord = {
  id: string; label: string; href: string; status: "running" | "success" | "error" | "interrupted";
  startedAt: string; updatedAt: string; error?: string;
};

const DEFAULT_APPEARANCE: Appearance = {
  theme: "system", density: "comfortable", highContrast: false, reduceMotion: false,
  wallpaperEnabled: false, wallpaperOpacity: 28, wallpaperBlur: 0, wallpaperPosition: "center",
};

type ExperienceContextValue = {
  appearance: Appearance;
  setAppearance: (patch: Partial<Appearance>) => void;
  wallpaperUrl: string;
  saveWallpaper: (file: File) => Promise<void>;
  clearWallpaper: () => Promise<void>;
  commandOpen: boolean; setCommandOpen: (open: boolean) => void;
  notificationOpen: boolean; setNotificationOpen: (open: boolean) => void;
  watchOpen: boolean; setWatchOpen: (open: boolean) => void;
  settingsOpen: boolean; setSettingsOpen: (open: boolean) => void;
  compareOpen: boolean; setCompareOpen: (open: boolean) => void;
  compareItems: CompareItem[];
  addCompareItem: (item: CompareItem) => void;
  removeCompareItem: (id: number) => void;
  clearCompareItems: () => void;
  tasks: TaskRecord[];
  startTask: (label: string, href?: string) => string;
  finishTask: (id: string, error?: string) => void;
  dismissTask: (id: string) => void;
  unread: number;
  refreshUnread: () => Promise<void>;
  authenticated: boolean;
  csrf: string;
  online: boolean;
};

const ExperienceContext = createContext<ExperienceContextValue | null>(null);
const APPEARANCE_KEY = "otomo:appearance:v1";
const COMPARE_KEY = "otomo:compare:v1";
const TASK_KEY = "otomo:tasks:v1";

function parseLocal<T>(key: string, fallback: T): T {
  if (typeof window === "undefined") return fallback;
  try { return { ...fallback as any, ...JSON.parse(localStorage.getItem(key) || "{}") }; }
  catch { return fallback; }
}

function openWallpaperDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open("otomo-experience", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("assets");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function wallpaperAsset(action: "get" | "set" | "delete", value?: Blob): Promise<Blob | null> {
  const db = await openWallpaperDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction("assets", action === "get" ? "readonly" : "readwrite");
    const store = tx.objectStore("assets");
    const req = action === "get" ? store.get("wallpaper") : action === "set" ? store.put(value, "wallpaper") : store.delete("wallpaper");
    req.onsuccess = () => resolve(action === "get" ? (req.result as Blob | undefined) || null : null);
    req.onerror = () => reject(req.error);
    tx.oncomplete = () => db.close();
  });
}

function applyAppearance(value: Appearance) {
  const root = document.documentElement;
  root.dataset.theme = value.theme;
  root.dataset.density = value.density;
  root.dataset.contrast = value.highContrast ? "high" : "normal";
  root.dataset.motion = value.reduceMotion ? "reduced" : "normal";
  root.style.setProperty("--wallpaper-opacity", String(value.wallpaperOpacity / 100));
  root.style.setProperty("--wallpaper-blur", `${value.wallpaperBlur}px`);
  root.style.setProperty("--wallpaper-position", value.wallpaperPosition);
}

export function ExperienceProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [appearance, setAppearanceState] = useState(DEFAULT_APPEARANCE);
  const [wallpaperUrl, setWallpaperUrl] = useState("");
  const [commandOpen, setCommandOpen] = useState(false);
  const [notificationOpen, setNotificationOpen] = useState(false);
  const [watchOpen, setWatchOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [compareOpen, setCompareOpen] = useState(false);
  const [compareItems, setCompareItems] = useState<CompareItem[]>([]);
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [unread, setUnread] = useState(0);
  const [authenticated, setAuthenticated] = useState(false);
  const [csrf, setCsrf] = useState("");
  const [online, setOnline] = useState(true);

  useEffect(() => {
    const value = parseLocal(APPEARANCE_KEY, DEFAULT_APPEARANCE);
    setAppearanceState(value); applyAppearance(value);
    try { setCompareItems(JSON.parse(localStorage.getItem(COMPARE_KEY) || "[]").slice(0, 3)); } catch { /* ignore */ }
    try {
      const previous: TaskRecord[] = JSON.parse(localStorage.getItem(TASK_KEY) || "[]");
      setTasks(previous.map<TaskRecord>((task) => task.status === "running" ? { ...task, status: "interrupted" } : task).slice(0, 12));
    } catch { /* ignore */ }
    wallpaperAsset("get").then((blob) => {
      if (blob) setWallpaperUrl(URL.createObjectURL(blob));
    }).catch(() => undefined);
    const syncOnline = () => setOnline(navigator.onLine);
    syncOnline(); window.addEventListener("online", syncOnline); window.addEventListener("offline", syncOnline);
    authSession().then((auth) => { setAuthenticated(Boolean(auth.authenticated)); setCsrf(auth.csrf_token || ""); }).catch(() => undefined);
    if ("serviceWorker" in navigator) {
      if (process.env.NODE_ENV === "production") {
        navigator.serviceWorker.register("/sw.js").catch(() => undefined);
      } else {
        // A previously installed production worker can otherwise serve a stale
        // app shell while running `next dev`, which makes UI debugging misleading.
        navigator.serviceWorker.getRegistrations().then((rows) => Promise.all(rows.map((row) => row.unregister()))).catch(() => undefined);
        if ("caches" in window) {
          caches.keys().then((keys) => Promise.all(keys.filter((key) => key.startsWith("otomo-shell-")).map((key) => caches.delete(key)))).catch(() => undefined);
        }
      }
    }
    return () => { window.removeEventListener("online", syncOnline); window.removeEventListener("offline", syncOnline); };
  }, []);

  useEffect(() => {
    const labels: Record<string, string> = {
      "/today": "更新今日追番", "/product/season-guide": "生成季番导视",
      "/product/recommendations": "生成个性化推荐", "/product/library": "汇总收藏",
      "/product/monthly-report": "生成观看报告", "/product/compare": "对比作品",
    };
    const started = (event: Event) => {
      const d = (event as CustomEvent<{ id: string; path: string }>).detail;
      const key = Object.keys(labels).find((x) => d.path.startsWith(x));
      if (!key) return;
      const now = new Date().toISOString();
      const record: TaskRecord = { id: d.id, label: labels[key], href: pathname, status: "running", startedAt: now, updatedAt: now };
      setTasks((rows) => [record, ...rows].slice(0, 12));
    };
    const finished = (event: Event) => {
      const d = (event as CustomEvent<{ id: string; error?: string }>).detail;
      setTasks((rows) => rows.map((x) => x.id === d.id ? { ...x, status: d.error ? "error" : "success", error: d.error, updatedAt: new Date().toISOString() } : x));
    };
    window.addEventListener("otomo:task-start", started); window.addEventListener("otomo:task-finish", finished);
    return () => { window.removeEventListener("otomo:task-start", started); window.removeEventListener("otomo:task-finish", finished); };
  }, [pathname]);

  useEffect(() => { localStorage.setItem(COMPARE_KEY, JSON.stringify(compareItems)); }, [compareItems]);
  useEffect(() => { localStorage.setItem(TASK_KEY, JSON.stringify(tasks.slice(0, 12))); }, [tasks]);
  useEffect(() => {
    const listener = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); setCommandOpen(true); }
      if (event.key === "Escape") { setCommandOpen(false); setNotificationOpen(false); setWatchOpen(false); setSettingsOpen(false); setCompareOpen(false); }
    };
    window.addEventListener("keydown", listener); return () => window.removeEventListener("keydown", listener);
  }, []);

  const refreshUnread = useCallback(async () => {
    if (!authenticated) { setUnread(0); return; }
    try { const payload = await productFetch("/product/inbox?limit=1"); setUnread(Number(payload.data?.unread || 0)); }
    catch { /* preserve the previous count on transient failure */ }
  }, [authenticated]);
  useEffect(() => { void refreshUnread(); }, [refreshUnread, pathname]);
  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;
    const pushed = (event: MessageEvent) => {
      if (event.data?.type === "otomo-push-received") void refreshUnread();
    };
    navigator.serviceWorker.addEventListener("message", pushed);
    return () => navigator.serviceWorker.removeEventListener("message", pushed);
  }, [refreshUnread]);

  const setAppearance = useCallback((patch: Partial<Appearance>) => {
    setAppearanceState((current) => {
      const next = { ...current, ...patch }; localStorage.setItem(APPEARANCE_KEY, JSON.stringify(next)); applyAppearance(next); return next;
    });
  }, []);
  const saveWallpaper = useCallback(async (file: File) => {
    if (!/^image\/(jpeg|png|webp)$/.test(file.type)) throw new Error("仅支持 JPEG、PNG 或 WebP");
    if (file.size > 12 * 1024 * 1024) throw new Error("壁纸不能超过 12 MB");
    await wallpaperAsset("set", file);
    setWallpaperUrl((old) => { if (old) URL.revokeObjectURL(old); return URL.createObjectURL(file); });
    setAppearance({ wallpaperEnabled: true });
  }, [setAppearance]);
  const clearWallpaper = useCallback(async () => {
    await wallpaperAsset("delete");
    setWallpaperUrl((old) => { if (old) URL.revokeObjectURL(old); return ""; });
    setAppearance({ wallpaperEnabled: false });
  }, [setAppearance]);
  const addCompareItem = useCallback((item: CompareItem) => {
    setCompareItems((items) => items.some((x) => x.id === item.id) ? items : [...items, item].slice(-3));
    setCompareOpen(true);
  }, []);
  const startTask = useCallback((label: string, href = pathname) => {
    const id = crypto.randomUUID(); const now = new Date().toISOString();
    const record: TaskRecord = { id, label, href, status: "running", startedAt: now, updatedAt: now };
    setTasks((rows) => [record, ...rows].slice(0, 12)); return id;
  }, [pathname]);
  const finishTask = useCallback((id: string, error?: string) => setTasks((rows) => rows.map((x) => x.id === id ? { ...x, status: error ? "error" : "success", error, updatedAt: new Date().toISOString() } : x)), []);

  const value = useMemo<ExperienceContextValue>(() => ({
    appearance, setAppearance, wallpaperUrl, saveWallpaper, clearWallpaper,
    commandOpen, setCommandOpen, notificationOpen, setNotificationOpen, watchOpen, setWatchOpen,
    settingsOpen, setSettingsOpen, compareOpen, setCompareOpen,
    compareItems, addCompareItem, removeCompareItem: (id) => setCompareItems((rows) => rows.filter((x) => x.id !== id)),
    clearCompareItems: () => setCompareItems([]), tasks, startTask, finishTask,
    dismissTask: (id) => setTasks((rows) => rows.filter((x) => x.id !== id)),
    unread, refreshUnread, authenticated, csrf, online,
  }), [appearance, wallpaperUrl, commandOpen, notificationOpen, watchOpen, settingsOpen, compareOpen, compareItems, tasks, unread, authenticated, csrf, online, setAppearance, saveWallpaper, clearWallpaper, addCompareItem, startTask, finishTask, refreshUnread]);

  useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<{ href?: string }>).detail;
      if (detail?.href) router.push(detail.href);
    };
    window.addEventListener("otomo:navigate", handler); return () => window.removeEventListener("otomo:navigate", handler);
  }, [router]);

  return (
    <ExperienceContext.Provider value={value}>
      {appearance.wallpaperEnabled && wallpaperUrl ? <div className="app-wallpaper" style={{ backgroundImage: `url(${JSON.stringify(wallpaperUrl).slice(1, -1)})` }} /> : null}
      {children}
    </ExperienceContext.Provider>
  );
}

export function useExperience() {
  const value = useContext(ExperienceContext);
  if (!value) throw new Error("useExperience must be used inside ExperienceProvider");
  return value;
}
