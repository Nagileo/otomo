"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Bell, BookOpen, CalendarDays, Command, Compass, ListChecks, LogIn, LogOut,
  MessageCircle, MessagesSquare, MonitorCog, Palette, Sparkles, Users,
} from "lucide-react";
import { useEffect, useState } from "react";

import { BACKEND } from "../lib/api";
import { useExperience } from "../lib/experience";
import { ExperienceOverlays } from "./experience-overlays";
import { UserAvatar } from "./identity-avatar";

type AuthState = {
  authenticated?: boolean;
  username?: string;
  avatar_url?: string;
  oauth_configured?: boolean;
  csrf_token?: string;
};

const primary = [
  { href: "/", label: "今日", icon: CalendarDays },
  { href: "/chat", label: "对话", icon: MessageCircle },
  { href: "/discover", label: "发现", icon: Compass },
  { href: "/library", label: "收藏", icon: BookOpen },
  { href: "/workspace", label: "清单", icon: ListChecks },
  { href: "/community", label: "社区", icon: MessagesSquare },
];

function active(pathname: string, href: string) {
  if (href === "/") return pathname === "/" || pathname === "/today";
  return pathname === href || pathname.startsWith(`${href}/`);
}

function aggregateVisitPath(pathname: string) {
  if (pathname.startsWith("/subject/")) return "/subject";
  if (pathname.startsWith("/share/") && pathname !== "/share/mine") return "/share";
  return pathname;
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const exp = useExperience();
  const [auth, setAuth] = useState<AuthState | null>(null);
  const authReady = auth !== null;

  useEffect(() => {
    fetch(`${BACKEND}/auth/session`, { credentials: "include" })
      .then((response) => response.json())
      .then(setAuth)
      .catch(() => setAuth({ authenticated: false }));
  }, [pathname]);

  useEffect(() => {
    if (!authReady) return;
    fetch(`${BACKEND}/community/visit`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: aggregateVisitPath(pathname) }),
    }).catch(() => undefined);
  }, [pathname, authReady]);

  if (pathname.startsWith("/share/") && pathname !== "/share/mine") return <>{children}</>;

  async function logout() {
    await fetch(`${BACKEND}/auth/logout`, {
      method: "POST",
      credentials: "include",
      headers: auth?.csrf_token ? { "x-otomo-csrf": auth.csrf_token } : {},
      body: "{}",
    });
    window.location.href = "/";
  }

  return (
    <div className="app-shell">
      <aside className="app-sidebar">
        <Link className="brand-lockup" href="/" aria-label="Otomo 首页">
          <span className="brand-mark"><Sparkles size={19} /></span>
          <span><strong>Otomo</strong><small>番组搭子</small></span>
        </Link>
        <nav className="primary-nav" aria-label="主导航">
          {primary.map(({ href, label, icon: Icon }) => (
            <Link key={href} href={href} className={active(pathname, href) ? "active" : ""}>
              <Icon size={18} /><span>{label}</span>
            </Link>
          ))}
        </nav>
        <div className="sidebar-spacer" />
        <button className="secondary-nav" onClick={() => exp.setCommandOpen(true)}><Command size={17} /><span>搜索</span><kbd>Ctrl K</kbd></button>
        <button className="secondary-nav" onClick={() => exp.setWatchOpen(true)}><CalendarDays size={17} /><span>快捷追番</span></button>
        <button className="secondary-nav" onClick={() => exp.setNotificationOpen(true)}><Bell size={17} /><span>通知</span>{exp.unread ? <b className="nav-count">{exp.unread > 99 ? "99+" : exp.unread}</b> : null}</button>
        <button className="secondary-nav" onClick={() => exp.setCompareOpen(true)}><ListChecks size={17} /><span>作品对比</span>{exp.compareItems.length ? <b className="nav-count">{exp.compareItems.length}</b> : null}</button>
        <Link className={`secondary-nav${active(pathname, "/friends") ? " active" : ""}`} href="/friends"><Users size={17} /><span>好友圈</span></Link>
        <button className="secondary-nav" onClick={() => exp.setSettingsOpen(true)}><Palette size={17} /><span>外观</span></button>
        <Link className="secondary-nav" href="/settings/subscriptions"><MonitorCog size={17} /><span>订阅设置</span></Link>
        <Link className="secondary-nav" href="/share/mine"><Sparkles size={17} /><span>我的分享</span></Link>
        <div className="account-block">
          {auth?.authenticated ? (
            <>
              <UserAvatar className="account-avatar" username={auth.username} avatarUrl={auth.avatar_url} />
              <span className="account-copy"><strong>@{auth.username}</strong><small>Bangumi 已连接</small></span>
              <button className="icon-plain" onClick={() => void logout()} title="退出 Bangumi"><LogOut size={17} /></button>
            </>
          ) : (
            <a className="account-login" href={`${BACKEND}/auth/bangumi/start`}>
              <LogIn size={17} /><span>{auth?.oauth_configured === false ? "配置 OAuth" : "连接 Bangumi"}</span>
            </a>
          )}
        </div>
      </aside>
      <div className="app-main">
        <header className="mobile-header">
          <Link className="mobile-brand" href="/"><Sparkles size={18} /> Otomo</Link>
          <div><button className="icon-plain" onClick={() => exp.setCommandOpen(true)} title="搜索"><Command size={17} /></button><button className="icon-plain" onClick={() => exp.setNotificationOpen(true)} title="通知"><Bell size={17} />{exp.unread ? <i /> : null}</button></div>
        </header>
        {children}
      </div>
      <nav className="mobile-nav" aria-label="移动端主导航">
        {primary.map(({ href, label, icon: Icon }) => (
          <Link key={href} href={href} className={active(pathname, href) ? "active" : ""}>
            <Icon size={20} /><span>{label}</span>
          </Link>
        ))}
      </nav>
      {!exp.online ? <div className="offline-bar">当前离线：可以浏览已缓存页面，本轮账户操作将在联网后恢复。</div> : null}
      <ExperienceOverlays />
    </div>
  );
}
