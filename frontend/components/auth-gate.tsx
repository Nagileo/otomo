"use client";

import { ArrowRight, LockKeyhole, Sparkles } from "lucide-react";

import { BACKEND } from "../lib/api";

export function AuthGate({
  eyebrow,
  title,
  description,
  features,
}: {
  eyebrow: string;
  title: string;
  description: string;
  features: string[];
}) {
  return (
    <section className="auth-gate">
      <div className="auth-gate-visual" aria-hidden="true">
        <span><LockKeyhole size={24} /></span>
        <i><Sparkles size={16} /></i>
      </div>
      <div className="auth-gate-copy">
        <span className="section-kicker">{eyebrow}</span>
        <h2>{title}</h2>
        <p>{description}</p>
        <div className="auth-gate-features">
          {features.map((feature) => <span key={feature}>{feature}</span>)}
        </div>
        <a className="button-primary" href={`${BACKEND}/auth/bangumi/start`}>
          连接 Bangumi <ArrowRight size={16} />
        </a>
        <small>OAuth 授权由 Bangumi 完成；Otomo 不会读取你的密码。</small>
      </div>
    </section>
  );
}
