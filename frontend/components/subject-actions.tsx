"use client";

import { ListPlus, Scale } from "lucide-react";
import { useEffect, useState } from "react";

import { productFetch } from "../lib/api";
import { useExperience } from "../lib/experience";

export function SubjectActions({ subject }: { subject: Record<string, any> }) {
  const exp = useExperience();
  const [lists, setLists] = useState<any[]>([]);
  const [open, setOpen] = useState(false);
  const [notice, setNotice] = useState("");
  useEffect(() => { if (open && exp.authenticated) productFetch("/workspace/lists").then((x) => setLists(x.data || [])).catch(() => setLists([])); }, [open, exp.authenticated]);
  async function add(listId: string) {
    await productFetch(`/workspace/lists/${listId}/items`, { method: "PUT", headers: { "Content-Type": "application/json", "x-otomo-csrf": exp.csrf }, body: JSON.stringify({ subject_id: subject.id, name: subject.name_cn || subject.name, subject_type: subject.type_name || "anime", image: subject.image || "" }) });
    setNotice("已加入清单"); setOpen(false);
  }
  if (!subject.id) return null;
  return <div className="subject-actions"><button className="button-secondary" onClick={() => exp.addCompareItem({ id: subject.id, name: subject.name_cn || subject.name, image: subject.image })}><Scale size={16} />加入对比</button>{exp.authenticated ? <div className="list-picker"><button className="button-secondary" onClick={() => setOpen(!open)}><ListPlus size={16} />加入清单</button>{open ? <div className="list-picker-menu">{lists.map((list) => <button key={list.id} onClick={() => void add(list.id)}>{list.title}<small>{list.items?.length || 0} 项</small></button>)}{!lists.length ? <span>请先在工作区新建清单</span> : null}</div> : null}</div> : null}{notice ? <span className="action-notice">{notice}</span> : null}</div>;
}
