"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Bell, BookOpen, CalendarDays, Compass, LogIn, LogOut, MessageCircle,
  Sparkles,
} from "lucide-react";
import { useEffect, useState } from "react";

import { BACKEND } from "../lib/api";

type AuthState = {
  authenticated?: boolean;
  username?: string;
  oauth_configured?: boolean;
  csrf_token?: string;
};

const primary = [
  { href: "/", label: "今日", icon: CalendarDays },
  { href: "/chat", label: "对话", icon: MessageCircle },
  { href: "/discover", label: "发现", icon: Compass },
  { href: "/library", label: "收藏", icon: BookOpen },
  { href: "/settings/subscriptions", label: "订阅", icon: Bell },
];

function active(pathname: string, href: string) {
  if (href === "/") return pathname === "/" || pathname === "/today";
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [auth, setAuth] = useState<AuthState | null>(null);

  useEffect(() => {
    fetch(`${BACKEND}/auth/session`, { credentials: "include" })
      .then((response) => response.json())
      .then(setAuth)
      .catch(() => setAuth({ authenticated: false }));
  }, [pathname]);

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
        <Link className="secondary-nav" href="/share/mine"><Sparkles size={17} /><span>我的分享</span></Link>
        <div className="account-block">
          {auth?.authenticated ? (
            <>
              <span className="account-avatar">{String(auth.username || "U").slice(0, 1).toUpperCase()}</span>
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
          <span>{primary.find((item) => active(pathname, item.href))?.label || "作品"}</span>
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
    </div>
  );
}
