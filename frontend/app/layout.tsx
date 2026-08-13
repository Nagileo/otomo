import type { Metadata, Viewport } from "next";
import { AppShell } from "../components/app-shell";
import { ExperienceProvider } from "../lib/experience";
import "./globals.css";

export const metadata: Metadata = {
  title: "Otomo · 番组搭子",
  description: "ACGN 知识图谱 Agent — 追番、口碑、推荐、圣地巡礼",
  manifest: "/manifest.webmanifest",
  appleWebApp: {
    capable: true,
    statusBarStyle: "black-translucent",
    title: "Otomo",
  },
  icons: {
    icon: [
      { url: "/icon.svg", type: "image/svg+xml" },
      { url: "/icon-192.png", sizes: "192x192", type: "image/png" },
    ],
    apple: "/apple-touch-icon.png",
  },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f4f6f3" },
    { media: "(prefers-color-scheme: dark)", color: "#0d100f" },
  ],
  width: "device-width",
  initialScale: 1,
  // 移动端不自动放大输入框（配合全局 16px 字号），但不禁用用户缩放
  maximumScale: 5,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const appearanceScript = `(function(){try{var a=JSON.parse(localStorage.getItem('otomo:appearance:v1')||'{}');var d=document.documentElement;d.dataset.theme=a.theme||'system';d.dataset.density=a.density||'comfortable';d.dataset.contrast=a.highContrast?'high':'normal';d.dataset.motion=a.reduceMotion?'reduced':'normal'}catch(e){}})()`;
  return (
    <html lang="zh" suppressHydrationWarning>
      <head><script dangerouslySetInnerHTML={{ __html: appearanceScript }} /></head>
      <body><ExperienceProvider><AppShell>{children}</AppShell></ExperienceProvider></body>
    </html>
  );
}
