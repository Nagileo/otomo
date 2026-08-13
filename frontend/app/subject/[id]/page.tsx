"use client";

import { ArrowLeft, ExternalLink, MessageCircle } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { PageHeader } from "../../../components/page-header";
import { authSession, createShareSnapshot, productFetch } from "../../../lib/api";
import { SubjectDossierPanel } from "../../panels/product";

export default function SubjectPage({ params }: { params: { id: string } }) {
  const [data, setData] = useState<any>(null);
  const [sources, setSources] = useState<any[]>([]);
  const [csrf, setCsrf] = useState("");
  const [authenticated, setAuthenticated] = useState(false);
  const [error, setError] = useState("");
  const [share, setShare] = useState("");

  useEffect(() => {
    authSession().then((auth) => {
      setCsrf(auth.csrf_token || "");
      setAuthenticated(Boolean(auth.authenticated));
    }).catch(() => undefined);
    productFetch(`/product/subjects/${encodeURIComponent(params.id)}?spoiler_level=none`)
      .then((payload) => { setData(payload.data); setSources(payload.sources || []); })
      .catch((e) => setError(String(e)));
  }, [params.id]);

  async function shareSnapshot(request: Record<string, any>) {
    try {
      const payload = await createShareSnapshot(request, csrf, sources);
      setShare(payload.url || payload.snapshot?.url || "");
    } catch (e) { setError(String(e)); }
  }

  const subject = data?.subject || {};
  return (
    <main className="page-frame subject-page">
      <PageHeader
        eyebrow="Subject dossier"
        title={subject.name || "作品档案"}
        description={subject.name && subject.name !== subject.name_cn ? subject.name_cn : "多源信息正在汇总"}
        actions={<><Link className="button-secondary icon-label" href="/discover"><ArrowLeft size={16} />返回发现</Link><Link className="button-secondary icon-label" href={`/chat?q=${encodeURIComponent(`详细评价《${subject.name || params.id}》`)}`}><MessageCircle size={16} />继续问</Link>{subject.id ? <a className="button-secondary icon-label" href={`https://bgm.tv/subject/${subject.id}`} target="_blank" rel="noreferrer">Bangumi <ExternalLink size={15} /></a> : null}</>}
      />
      {error ? <div className="surface-error">{error}</div> : null}
      {!data && !error ? <div className="surface-loading">正在汇总无剧透评价、系列关系、音乐与观看入口…</div> : null}
      {share ? <div className="inline-notice">分享页已生成：<a href={share} target="_blank" rel="noreferrer">打开公开快照</a></div> : null}
      {data ? <SubjectDossierPanel data={data} productView onShareSnapshot={authenticated ? (request) => void shareSnapshot(request) : undefined} /> : null}
    </main>
  );
}
