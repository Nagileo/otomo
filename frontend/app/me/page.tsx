"use client";

import Link from "next/link";
import {
  BellRing, BookOpen, ListChecks, Palette, Share2, Users,
} from "lucide-react";

import { PageHeader } from "../../components/page-header";
import { useExperience } from "../../lib/experience";

const entries = [
  { href: "/library", label: "我的收藏", description: "查看收藏、评分和观看报告", icon: BookOpen },
  { href: "/workspace", label: "我的清单", description: "待看计划、保存的推荐和自定义视图", icon: ListChecks },
  { href: "/friends", label: "好友圈", description: "选择好友并查看口味与动态", icon: Users },
  { href: "/settings/subscriptions", label: "订阅提醒", description: "每日追番、周报、好友动态与口碑变化", icon: BellRing },
  { href: "/share/mine", label: "我的分享", description: "管理已经生成的公开快照", icon: Share2 },
];

export default function MePage() {
  const experience = useExperience();
  return (
    <main className="page-frame me-page">
      <PageHeader eyebrow="个人中心" title="我的 Otomo" description="收藏、清单、好友、订阅、分享和外观都从这里进入。" />
      <div className="me-entry-grid">
        {entries.map(({ href, label, description, icon: Icon }) => (
          <Link className="me-entry-card" href={href} key={href}>
            <Icon size={21} />
            <span><strong>{label}</strong><small>{description}</small></span>
          </Link>
        ))}
        <button className="me-entry-card" type="button" onClick={() => experience.setSettingsOpen(true)}>
          <Palette size={21} />
          <span><strong>外观设置</strong><small>主题、密度、壁纸和动效</small></span>
        </button>
      </div>
    </main>
  );
}
