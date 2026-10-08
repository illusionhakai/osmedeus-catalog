"use client";

import * as React from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useOrg } from "@/providers/org-provider";

export type Selection = { mode: "ids" | "filtered"; program_ids: number[]; asset_ids: number[]; filters: Record<string, string> };
type Mapping = { name: string; kind: "module" | "flow"; params: Record<string, string> };
type Item = { id: string; program_name: string; kind: string; workflow: Mapping; target_count: number; accepted_count?: number; status: string; job_id?: string; error?: string; tracking_error?: string; runs: { run_uuid: string; target: string; status: string; completed_steps: number; total_steps: number }[]; cancellation?: Record<string, unknown>; attempts: unknown[] };
type Batch = { id: string; status: string; export_id: string; created_at: string; error?: string; cancel_requested: boolean; catalog_changed?: boolean; credentials_available?: boolean; organization: { name: string }; items: Item[]; delivery?: { status: string; error?: string }; request: unknown; history: unknown[]; manifest: { created_at: string; source_snapshots: Record<string, unknown>; programs: { id: number; name: string; counts: Record<string, number>; policy_links: { url: string; source: string }[]; omitted: unknown[]; conversions: unknown[]; exclusions?: unknown[] }[] } };
type BatchSummary = Omit<Batch, "manifest" | "request" | "history">;
const categoryLabels: Record<string, string> = { domains: "Domains / subdomains", ips: "IP addresses", cidrs: "CIDRs", "discovery-roots": "Wildcard discovery" };
const defaultOrg = "00000000-0000-0000-0000-000000000001";
const selectClass = "rounded-md border border-input bg-background px-3 py-2 text-sm";

async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/catalog-api/v1${path}`, { method: body === undefined ? "GET" : "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${localStorage.getItem("osmedeus_token") || ""}` }, body: body === undefined ? undefined : JSON.stringify(body) });
  const value = await response.json().catch(() => null);
  if (response.status === 504) throw new Error(path === "/scan-batches" && body !== undefined
    ? "Preparing the scan review timed out. Send to scan again; scans start only after Launch scans."
    : "Scan control request timed out. Check batch progress before retrying.");
  if (!response.ok) throw new Error(typeof value?.detail === "string" ? value.detail : `Scan control unavailable (${response.status})`);
  return value;
}

export function ScanControl({ selection, selectionCount, selectionRevision }: { selection: Selection; selectionCount: number; selectionRevision: number }) {
  const { orgs, activeOrg, error: orgError } = useOrg();
  const destination = activeOrg ?? orgs.find(o => o.uuid === defaultOrg);
  const org = destination?.uuid ?? "";
  const [mode, setMode] = React.useState("automatic");
  const [convert, setConvert] = React.useState(true);
  const [parallelism, setParallelism] = React.useState(1);
  const [concurrency, setConcurrency] = React.useState(1);
  const [preview, setPreview] = React.useState<Batch | null>(null);
  const [previewSignature, setPreviewSignature] = React.useState("");
  const [batches, setBatches] = React.useState<BatchSummary[]>([]);
  const [batch, setBatch] = React.useState<Batch | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [preparing, setPreparing] = React.useState(false);
  const [error, setError] = React.useState("");
  const [revision, setRevision] = React.useState(0);
  const [clock, setClock] = React.useState(0);
  const signature = JSON.stringify({ selection, selectionRevision, org, mode, convert, parallelism, concurrency });
  const signatureRef = React.useRef(signature);
  React.useEffect(() => { signatureRef.current = signature; }, [signature]);
  const validPreview = preview && previewSignature === signature && !preview.catalog_changed && preview.status === "prepared";
  const batchId = batch?.id;
  const previewId = preview?.id;

  React.useEffect(() => {
    let live = true;
    const load = async () => {
      if (live) setClock(Date.now());
      const result = await api<{ items: BatchSummary[] }>("/scan-batches");
      if (live) setBatches(result.items);
      if (live && !batchId) {
        const active = result.items.find(b => !["prepared", "completed", "cancelled", "partial"].includes(b.status));
        if (active) {
          const detail = await api<Batch>(`/scan-batches/${active.id}`);
          if (live) setBatch(detail);
        }
      }
      if (batchId) {
        const detail = await api<Batch>(`/scan-batches/${batchId}`);
        if (live) setBatch(detail);
      }
      if (previewId) {
        const detail = await api<Batch>(`/scan-batches/${previewId}`);
        if (live) setPreview(detail);
      }
    };
    void load().catch(e => { if (live) setError(e.message); });
    const timer = setInterval(() => void load().catch(e => { if (live) setError(e.message); }), 3000);
    return () => { live = false; clearInterval(timer); };
  }, [batchId, previewId, revision]);

  async function perform(action: () => Promise<void>) {
    setBusy(true); setError("");
    try { await action(); } catch (e) { setError(e instanceof Error ? e.message : "Scan control failed"); }
    finally { setBusy(false); }
  }
  async function prepare() {
    const frozenSignature = signature;
    setPreparing(true);
    try {
      await perform(async () => {
        const result = await api<Batch>("/scan-batches", { selection, org_uuid: org, scan_mode: mode, convert_wildcards: convert, parallelism, target_concurrency: concurrency });
        // A selection edited during this request cannot approve the previous inputs.
        setPreview(result); setPreviewSignature(frozenSignature);
        setRevision(v => v + 1);
      });
    } finally { setPreparing(false); }
  }
  async function act(id: string, action: string, extra = {}) {
    await perform(async () => {
      setBatch(await api<Batch>(`/scan-batches/${id}/${action}`, { confirmed: true, ...extra }));
      if (action === "start") setPreview(null);
      setRevision(v => v + 1);
    });
  }
  async function download(id: string) {
    await perform(async () => {
      const response = await fetch(`/catalog-api/v1/exports/${id}/download`, { headers: { Authorization: `Bearer ${localStorage.getItem("osmedeus_token") || ""}` } });
      if (!response.ok) throw new Error("Export download unavailable");
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a"); link.href = url; link.download = `catalog-${id}.zip`; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  }

  return <section aria-label="Scan control center" className="space-y-4 rounded-lg border bg-card p-4">
    <h2 className="font-semibold">Send to scan</h2>
    <p className="text-sm text-muted-foreground">{selectionCount} programs selected · Destination: {destination?.name || "Loading organization…"}</p>
    {(error || orgError) && <p role="alert" className="text-sm text-destructive">{error || orgError} <Button variant="ghost" onClick={() => setRevision(v => v + 1)}>Reload scan control</Button></p>}
    <div className="space-y-3" onChangeCapture={() => setPreviewSignature("")}>
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-sm">Scan mode <select aria-label="Scan mode" className={`${selectClass} ml-2`} value={mode} disabled={busy} onChange={e => setMode(e.target.value)}><option value="automatic">Automatic</option><option value="full">Full recon</option></select></label>
        <Button disabled={busy || !selectionCount || !org} onClick={() => void prepare()}>{preparing ? "Preparing review…" : "Send to scan"}</Button>
      </div>
      <details className="text-sm"><summary className="cursor-pointer">Advanced settings</summary><div className="mt-3 flex flex-wrap items-center gap-4">
        <label>Parallel jobs <Input aria-label="Parallel jobs" className="w-24" type="number" min={1} max={3} value={parallelism} onChange={e => setParallelism(Number(e.target.value))} /></label>
        <label>Targets per job concurrently <Input aria-label="Target concurrency" className="w-24" type="number" min={1} max={10} value={concurrency} onChange={e => setConcurrency(Number(e.target.value))} /></label>
        <label><input type="checkbox" checked={convert} onChange={e => setConvert(e.target.checked)} /> Include wildcard discovery</label>
      </div></details>
    </div>
    {preparing && <p role="status" className="text-sm text-muted-foreground">Preparing selected targets and checking workflows. Scans start after Launch scans.</p>}
    {preview && <div aria-label="Scan review" className="space-y-3 rounded-md border p-4">
      <h3 className="font-semibold">Review scans</h3>
      <p className="text-sm">{preview.manifest.programs.length} programs · {preview.items.reduce((n, i) => n + i.target_count, 0)} targets · {preview.organization.name}</p>
      <table aria-label="Workflow routing" className="w-full text-left text-sm"><thead><tr><th>Targets</th><th>Workflow</th><th>Count</th></tr></thead><tbody>{Object.entries(preview.items.reduce<Record<string, { workflow: Mapping; count: number }>>((rows, item) => ({ ...rows, [item.kind]: { workflow: item.workflow, count: (rows[item.kind]?.count || 0) + item.target_count } }), {})).map(([kind, row]) => <tr key={kind}><td>{categoryLabels[kind] || kind}</td><td>{row.workflow.name}</td><td>{row.count}</td></tr>)}</tbody></table>
      <p className="text-sm text-muted-foreground">{preview.manifest.programs.reduce((n, p) => n + p.omitted.length, 0)} omitted targets · Snapshot: {new Date(preview.manifest.created_at).toLocaleString()} ({Math.max(0, Math.floor((clock - Date.parse(preview.manifest.created_at)) / 60000))} minutes old)</p>
      <details><summary className="cursor-pointer text-sm">Scope details and exclusions</summary>{preview.manifest.programs.map(p => <div key={p.id} className="mt-3 text-sm"><strong>{p.name}</strong>{p.policy_links?.map(l => <a key={l.url} className="ml-3 text-primary underline" href={l.url} target="_blank" rel="noopener noreferrer">Policy ({l.source})</a>)}<pre className="max-h-48 overflow-auto text-xs">{JSON.stringify({ omitted: p.omitted, conversions: p.conversions, exclusions: p.exclusions }, null, 2)}</pre></div>)}</details>
      {!validPreview && <p role="status" className="text-sm text-destructive">Selection, settings or catalog changed, or this review was already launched. Send to scan again before launching.</p>}
      <p className="text-sm">Launching confirms authorization, destination and workflow suitability, including scope exclusions. Wildcard roots authorize discovery only, not an apex scan. Workflows do not automatically enforce exclusions.</p>
      <Button disabled={busy || !validPreview} onClick={() => { if (signatureRef.current === previewSignature) void act(preview.id, "start"); }}>Launch scans</Button>
      <details className="text-sm"><summary className="cursor-pointer">Review tools</summary><div className="mt-3 space-y-3"><Button variant="outline" onClick={() => void download(preview.export_id)}>Download reviewed ZIP</Button><pre className="max-h-48 overflow-auto text-xs">{JSON.stringify({ request: preview.request, sources: preview.manifest.source_snapshots }, null, 2)}</pre></div></details>
    </div>}
    <details className="text-sm"><summary className="cursor-pointer">Scan history ({batches.length})</summary><div className="mt-3 flex flex-col items-start gap-2">{batches.map(b => <Button key={b.id} variant="ghost" disabled={busy} onClick={() => void perform(async () => setBatch(await api<Batch>(`/scan-batches/${b.id}`)))}>{new Date(b.created_at).toLocaleString()} · {b.organization.name} · {b.status} · {b.id.slice(0, 8)}</Button>)}</div></details>
    {batch && <div className="space-y-3" aria-label="Batch progress">
      <p className="font-medium">Batch status: {batch.status}</p><p className="text-sm">Delivery: {batch.delivery?.status || "not started"} · Snapshot: {new Date(batch.manifest.created_at).toLocaleString()} · {batch.catalog_changed ? "Catalog has changed; approved inputs remain unchanged" : "Catalog snapshot unchanged"}</p>
      {batch.error && <p role="alert" className="text-sm text-destructive">{batch.error}</p>}
      {batch.cancel_requested && <p className="text-sm text-destructive">Cancellation requested. Only engine-reported run status is known, not guaranteed process termination. Late runs are checked while credentials remain valid; missing or uncertain jobs mean cancellation is incomplete.</p>}
      <div className="flex flex-wrap gap-3">{["paused", "cancel_requested"].includes(batch.status) && <Button disabled={busy} onClick={() => void act(batch.id, "resume")}>Resume with current login</Button>}{batch.status === "paused" && ["failed", "interrupted"].includes(batch.delivery?.status || "") && <Button disabled={busy} variant="outline" onClick={() => { if (window.confirm("Retry unconfirmed uploads? An upload with a lost acknowledgement may be duplicated.")) void act(batch.id, "resume", { retry_delivery: true }); }}>Retry delivery and resume</Button>}{!["completed", "cancelled", "partial"].includes(batch.status) && <Button disabled={busy} variant="destructive" onClick={() => { if (window.confirm("Stop unsent work and request cancellation of known active runs? Whole-job cancellation is not atomic.")) void act(batch.id, "cancel"); }}>Cancel batch</Button>}<Link className="self-center text-sm text-primary underline" href="/scans">Open Scans</Link></div>
      {batch.items.map(item => <div key={item.id} className="space-y-1 rounded-md border p-3 text-sm"><strong>{item.program_name} / {item.kind}</strong><p>{item.status} · {item.target_count} reviewed targets · {item.runs.length}/{item.accepted_count ?? "?"} run records observed</p>{item.job_id && <p>Job: {item.job_id}</p>}{(item.error || item.tracking_error) && <p className="text-destructive">{item.error || item.tracking_error}</p>}{item.runs.map(run => <p key={run.run_uuid}>{run.target || run.run_uuid}: {run.status} ({run.completed_steps ?? 0}/{run.total_steps ?? "?"} steps)</p>)}
        {batch.status === "paused" && !batch.cancel_requested && ["failed", "uncertain"].includes(item.status) && <div className="flex gap-3">{["retry", "skip"].map(action => <Button key={action} variant="outline" disabled={busy} onClick={() => { if (window.confirm(item.status === "uncertain" ? `Inspect Osmedeus jobs first: an untracked scan may exist. ${action} this item explicitly? Retrying may duplicate a scan.` : `${action} this failed item? Retrying repeats its targets, including any that already completed.`)) void act(batch.id, `items/${item.id}/${action}`, { acknowledge_uncertain: item.status === "uncertain" }); }}>{action === "retry" ? "Retry item" : "Skip item"}</Button>)}</div>}
        <details><summary>Cancellation / previous attempts</summary><pre className="max-h-48 overflow-auto text-xs">{JSON.stringify({ cancellation: item.cancellation, attempts: item.attempts }, null, 2)}</pre></details>
      </div>)}<details><summary className="text-sm">Action history</summary><pre className="max-h-48 overflow-auto text-xs">{JSON.stringify(batch.history, null, 2)}</pre></details>
    </div>}
  </section>;
}
