"use client";

// 记忆/计划域面板：长期记忆、计划板、决策与通知类证据。
// 既有面板（Memory/WatchPlan/写回确认流相关）后续搬迁至此；新记忆域面板一律写在本文件。

import { Badge, Panel, list, text, type AnyRecord } from "../evidence-panels";

const KIND_LABEL: Record<string, string> = {
  weekly_digest: "周报",
  daily_airing: "每日追番",
  system: "系统",
};

export function InboxPanel({ data }: { data: AnyRecord }) {
  const items = list(data.items);
  return (
    <Panel title={`收件箱 · ${text(data.username)}`} subtitle={`${items.length} 条通知`}>
      {items.length === 0 && <div className="empty-hint">收件箱是空的；订阅周报或每日追番提醒后会出现在这里。</div>}
      <div className="inbox-list">
        {items.map((it, i) => {
          const payload = (it.payload || {}) as AnyRecord;
          const sections = list(payload.sections);
          return (
            <div key={i} className={`inbox-item${it.unread ? " unread" : ""}`}>
              <div className="inbox-head">
                <Badge tone={it.unread ? "warn" : "dim"}>{KIND_LABEL[text(it.kind, "system")] ?? text(it.kind)}</Badge>
                <span className="inbox-title">{text(it.title, "通知")}</span>
                <span className="inbox-time">{text(it.created_at, "")}</span>
              </div>
              {sections.slice(0, 2).map((sec, j) => (
                <div key={j} className="inbox-section">
                  <div className="inbox-section-title">{text(sec.title, "")}</div>
                  <ul>
                    {list(sec.items)
                      .slice(0, 4)
                      .map((row, k) => (
                        <li key={k}>
                          {text(row.name || row.title, "条目")}
                          {row.behind ? <Badge tone="warn">落后 {row.behind} 集</Badge> : null}
                          {row.subgroup ? <Badge tone="dim">{text(row.subgroup)}</Badge> : null}
                        </li>
                      ))}
                  </ul>
                </div>
              ))}
            </div>
          );
        })}
      </div>
    </Panel>
  );
}
