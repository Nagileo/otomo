"use client";

// 面板公共原语：类型/格式化/徽章/容器/分享按钮。所有 panels/ 域文件从这里取。

import { type ReactNode } from "react";

export type AnyRecord = Record<string, any>;

export type SpoilerState = {
  mode?: string;
  memory_default?: string;
  soft_warning?: boolean;
  progress_episode?: number;
  pending_followup?: boolean;
  followup_question?: string;
};
export type MemoryState = {
  username?: string;
  likes?: AnyRecord[];
  dislikes?: AnyRecord[];
  spoiler_default?: string;
  progress?: Record<string, AnyRecord>;
  recent_feedback?: AnyRecord[];
  profile_snapshot?: Record<string, AnyRecord>;
  aspect_profiles?: Record<string, AnyRecord>;
  pending_write_actions?: AnyRecord[];
  recent_decisions?: AnyRecord[];
  watch_plan?: AnyRecord[];
  recommendation_lists?: AnyRecord[];
  weekly_digest_subscription?: AnyRecord;
  inbox?: AnyRecord[];
  recent_visual_feedback?: AnyRecord[];
  updated_at?: string;
};


export function list<T = AnyRecord>(value: any): T[] {
  return Array.isArray(value) ? value : [];
}

export function text(value: any, fallback = "未知") {
  const s = String(value ?? "").trim();
  return s || fallback;
}

export function fmtScore(score: any, scale?: any) {
  if (score === null || score === undefined || score === "") return "暂无";
  return scale ? `${score}/${scale}` : String(score);
}

export function clsBySignal(signal: any) {
  const s = String(signal ?? "").toLowerCase();
  if (["strong", "positive", "high", "used"].includes(s)) return "good";
  if (["mixed", "maybe", "medium", "low_data"].includes(s)) return "warn";
  if (["weak", "wait", "hidden", "unavailable"].includes(s)) return "bad";
  return "dim";
}

export function pct(value: any) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "0.00";
  return n.toFixed(2);
}

export function confidenceLabel(value: any) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "unknown";
  if (n >= 0.85) return "high";
  if (n >= 0.6) return "medium";
  if (n > 0) return "low";
  return "unknown";
}

export function sourceTone(source: any) {
  const s = String(source ?? "");
  if (s === "explicit_user") return "good";
  if (s === "derived_from_feedback") return "warn";
  if (s === "bangumi_profile") return "dim";
  return "dim";
}

export function hasActionableMemory(data: AnyRecord) {
  return (
    list(data.pending_write_actions).length > 0
    || list(data.inbox).some((item) => item?.unread)
    || Boolean(data.weekly_digest_subscription?.pending)
  );
}

export function Badge({ children, tone = "dim" }: { children: ReactNode; tone?: string }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}

export function Panel({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
}) {
  return (
    <section className="evidence-panel">
      <div className="evidence-head">
        <div>
          <div className="evidence-title">{title}</div>
          {subtitle && <div className="evidence-sub">{subtitle}</div>}
        </div>
      </div>
      {children}
    </section>
  );
}

export function EmptyHint({ text }: { text: string }) {
  return <div className="empty-hint">{text}</div>;
}

export type ShareSnapshotType = "subject_dossier" | "watch_order" | "monthly_report" | "season_guide" | "watch_cockpit";
export type ShareSnapshotHandler = (req: {
  type: ShareSnapshotType;
  title: string;
  summary?: string;
  payload: AnyRecord;
  spoiler_level?: "none" | "mild" | "full";
  personalization_mode?: "public_generic" | "public_personalized" | "private_preview";
}) => void;

export function ShareSnapshotButton({
  type,
  title,
  payload,
  onShareSnapshot,
}: {
  type: ShareSnapshotType;
  title: string;
  payload: AnyRecord;
  onShareSnapshot?: ShareSnapshotHandler;
}) {
  if (!onShareSnapshot) return null;
  return (
    <button
      type="button"
      className="inline-action"
      onClick={() => onShareSnapshot({
        type,
        title,
        payload,
        summary: title,
        spoiler_level: "none",
        personalization_mode: "public_generic",
      })}
    >
      生成分享页
    </button>
  );
}


export type PrepareWriteHandler = (subjectId: number, subjectName: string, collectionType?: number) => void;
export type PrepareDownloaderHandler = (payload: AnyRecord) => void;


