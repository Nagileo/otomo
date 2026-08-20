"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Bell, CalendarDays, CircleUserRound, Command, Compass, ListChecks, LogIn, LogOut,
  MessageCircle, MessagesSquare, MonitorCog, Palette, Plug, Sparkles, Users,
} from "lucide-react";
import { useEffect } from "react";

import { BACKEND } from "../lib/api";
import { useExperience } from "../lib/experience";
import { ExperienceOverlays } from "./experience-overlays";
import { UserAvatar } from "./identity-avatar";

const primary = [
  { href: "/", label: "今日", icon: CalendarDays },
  { href: "/chat", label: "对话", icon: MessageCircle },
  { href: "/discover", label: "发现", icon: Compass },
  { href: "/community", label: "同好", icon: MessagesSquare },
  { href: "/me", label: "我的", icon: CircleUserRound },
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

  useEffect(() => {
    if (!exp.authReady) return;
    fetch(`${BACKEND}/community/visit`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: aggregateVisitPath(pathname) }),
    }).catch(() => undefined);
  }, [pathname, exp.authReady]);

  if (pathname.startsWith("/share/") && pathname !== "/share/mine") return <>{children}</>;

  async function logout() {
    await fetch(`${BACKEND}/auth/logout`, {
      method: "POST",
      credentials: "include",
      headers: exp.csrf ? { "x-otomo-csrf": exp.csrf } : {},
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
        <Link className="secondary-nav" href="/settings/integrations"><Plug size={17} /><span>账号与集成</span></Link>
        <Link className="secondary-nav" href="/settings/subscriptions"><MonitorCog size={17} /><span>订阅设置</span></Link>
        <Link className="secondary-nav" href="/share/mine"><Sparkles size={17} /><span>我的分享</span></Link>
        <div className="account-block">
          {exp.authenticated ? (
            <>
              <UserAvatar className="account-avatar" username={exp.username} avatarUrl={exp.avatarUrl} />
              <span className="account-copy"><strong>@{exp.username}</strong><small>Bangumi 已连接</small></span>
              <button className="icon-plain" onClick={() => void logout()} title="退出 Bangumi"><LogOut size={17} /></button>
            </>
          ) : (
            <a className="account-login" href={`${BACKEND}/auth/bangumi/start`}>
              <LogIn size={17} /><span>{exp.authReady && !exp.oauthConfigured ? "配置 OAuth" : "连接 Bangumi"}</span>
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
