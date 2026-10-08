"use client";

import * as React from "react";
import type { ColDef, ICellRendererParams } from "ag-grid-community";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { DataGrid } from "@/components/ui/data-grid";
import { ErrorState } from "@/components/shared/error-state";
import { toast } from "sonner";
import { DeliveryPanel } from "./delivery-panel";
import { ScanControl } from "./scan-control";

type Program = { id: number; name: string; platform: string; sources: string; matching_asset_count?: number; domain_count?: number; wildcard_count?: number; program_url?: string; description?: string; status?: string; is_active?: boolean; source_records?: { source: string; source_id: string; is_active: boolean }[]; policy_links?: { url: string; source: string }[] };
type Asset = { id: number; raw_value: string; asset_type: string; scope: string; is_wildcard: boolean };
type Page<T> = { items: T[]; total: number; has_more: boolean };
type Schedule = { enabled: boolean; time: string; timezone: string; include_vdp: boolean };
type Job = { id: string; status: string; created_at: string; result?: { error?: string; sources?: { source: string; error?: string }[] } };
type Overview = { sources: { name: string; last_success: string | null; age_seconds?: number | null; latest?: { status: string; error?: string } }[]; facets: { platforms?: { value: string }[] } };
type Manifest = { export_id: string; created_at: string; programs: { id: number; name: string; counts: Record<string, number>; omitted: { value: string; reason: string }[]; conversions: { from: string; to: string }[] }[] };

async function request(path: string, init?: RequestInit): Promise<Response> {
  const token = localStorage.getItem("osmedeus_token");
  const response = await fetch(`/catalog-api/v1${path}`, { ...init, headers: { "Content-Type": "application/json", Authorization: `Bearer ${token || ""}`, ...init?.headers } });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : `Catalog unavailable (${response.status}). Your Osmedeus pages remain available.`);
  }
  return response;
}
async function json<T>(path: string, init?: RequestInit): Promise<T> { return (await request(path, init)).json(); }
function message(error: unknown) { return error instanceof Error ? error.message : "Catalog request failed"; }
const selectClass = "rounded-md border border-input bg-background px-3 py-2 text-sm";

export default function BugBountyPage() {
  const [programs, setPrograms] = React.useState<Page<Program>>({ items: [], total: 0, has_more: false });
  const [assets, setAssets] = React.useState<Page<Asset>>({ items: [], total: 0, has_more: false });
  const [program, setProgram] = React.useState<Program | null>(null);
  const [overview, setOverview] = React.useState<Overview | null>(null);
  const [schedule, setSchedule] = React.useState<Schedule | null>(null);
  const [jobs, setJobs] = React.useState<Job[]>([]);
  const [search, setSearch] = React.useState("");
  const [source, setSource] = React.useState("");
  const [platform, setPlatform] = React.useState("");
  const [target, setTarget] = React.useState("");
  const [scope, setScope] = React.useState("in_scope");
  const [kind, setKind] = React.useState("");
  const [programOffset, setProgramOffset] = React.useState(0);
  const [assetOffset, setAssetOffset] = React.useState(0);
  const [selected, setSelected] = React.useState<number[]>([]);
  const [selectedPrograms, setSelectedPrograms] = React.useState<number[]>([]);
  const [allPrograms, setAllPrograms] = React.useState(false);
  const [selectionRevision, setSelectionRevision] = React.useState(0);
  const [wildcards, setWildcards] = React.useState(false);
  const [preview, setPreview] = React.useState<Manifest | null>(null);
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [revision, setRevision] = React.useState(0);
  const previousJobs = React.useRef("");
  const filters = React.useMemo(() => ({ program_id: program?.id, source: source || undefined, scope: scope || undefined, kind: kind || undefined, target_text: target || undefined, active: "true" }), [program?.id, source, scope, kind, target]);
  const programFilters = React.useMemo(() => ({ q: search, source, platform, scope, kind, scope_text: target, scannable_only: "true", active: "true" }), [search, source, platform, scope, kind, target]);

  React.useEffect(() => {
    let live = true;
    const load = () => Promise.all([json<Overview>("/overview"), json<{ items: Job[] }>("/jobs")])
      .then(([info, running]) => { if (live) {
        setOverview(info); setJobs(running.items); setError("");
        const stamp = running.items.map(job => `${job.id}:${job.status}`).join(",");
        if (previousJobs.current !== stamp) { previousJobs.current = stamp; setRevision(v => v + 1); }
      } })
      .catch(e => { if (live) setError(message(e)); });
    void load();
    const timer = setInterval(load, 10000);
    return () => { live = false; clearInterval(timer); };
  }, [revision]);
  React.useEffect(() => { json<Schedule>("/schedule").then(setSchedule).catch(e => setError(message(e))); }, []);
  React.useEffect(() => {
    const abort = new AbortController();
    const query = new URLSearchParams({ ...programFilters, limit: "50", offset: String(programOffset) });
    json<Page<Program>>(`/programs?${query}`, { signal: abort.signal }).then(setPrograms).catch(e => { if (!abort.signal.aborted) setError(message(e)); });
    return () => abort.abort();
  }, [programFilters, programOffset, revision]);
  React.useEffect(() => {
    if (!program) return;
    const abort = new AbortController();
    const query = new URLSearchParams({ limit: "50", offset: String(assetOffset) });
    Object.entries(filters).forEach(([key, value]) => { if (value !== undefined) query.set(key, String(value)); });
    json<Page<Asset>>(`/assets?${query}`, { signal: abort.signal }).then(setAssets).catch(e => { if (!abort.signal.aborted) setError(message(e)); });
    return () => abort.abort();
  }, [program, filters, assetOffset, revision]);

  function resetAssets() { setSelected([]); setPreview(null); setAssetOffset(0); setSelectionRevision(v => v + 1); }
  function resetPrograms() { setSelectedPrograms([]); setAllPrograms(false); setProgramOffset(0); setProgram(null); resetAssets(); }
  async function openProgram(row: Program) {
    try { const detail = await json<Program>(`/programs/${row.id}`); setProgram(detail); resetAssets(); }
    catch (e) { setError(message(e)); }
  }
  const programColumns: ColDef<Program>[] = [
    { headerName: "Select", width: 75, flex: 0, sortable: false, cellRenderer: (p: ICellRendererParams<Program>) => p.data && <input type="checkbox" aria-label={`Select program ${p.data.name}`} checked={allPrograms || selectedPrograms.includes(p.data.id)} disabled={allPrograms} onChange={e => { const id = p.data!.id; setSelectedPrograms(v => e.target.checked ? [...v, id] : v.filter(n => n !== id)); resetAssets(); }} /> },
    { field: "name", headerName: "Program", cellRenderer: (p: ICellRendererParams<Program>) => <button className="text-primary underline" onClick={() => p.data && void openProgram(p.data)}>{p.value}</button> },
    { field: "platform", headerName: "Platform" }, { field: "sources", headerName: "Sources" },
    { field: "matching_asset_count", headerName: "Matching records" }, { field: "domain_count", headerName: "Exact domains" }, { field: "wildcard_count", headerName: "Wildcards" },
    { headerName: "Policy", cellRenderer: (p: ICellRendererParams<Program>) => p.data?.program_url && <a className="text-primary underline" href={p.data.program_url} target="_blank" rel="noopener noreferrer">Policy</a> },
  ];
  const assetColumns = React.useMemo<ColDef<Asset>[]>(() => [
    { headerName: "Select", width: 75, flex: 0, sortable: false, cellRenderer: (p: ICellRendererParams<Asset>) => p.data && <input type="checkbox" aria-label={`Select ${p.data.raw_value} (${p.data.scope})`} checked={selected.includes(p.data.id)} onChange={e => { const id = p.data!.id; setSelected(previous => e.target.checked ? [...previous, id] : previous.filter(v => v !== id)); setAllPrograms(false); setSelectedPrograms([]); setSelectionRevision(v => v + 1); setPreview(null); }} /> },
    { field: "raw_value", headerName: "Asset" }, { field: "asset_type", headerName: "Type" }, { field: "scope", headerName: "Scope" },
  ], [selected]);
  async function perform(action: () => Promise<void>) {
    setBusy(true); setError("");
    try { await action(); } catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  async function exportInputs(selection: boolean) {
    await perform(async () => {
      const value = await json<Manifest>("/exports", { method: "POST", body: JSON.stringify({ filters, asset_ids: selection ? selected : [], convert_wildcards: wildcards }) });
      setPreview(value);
    });
  }
  async function download() {
    if (!preview) return;
    await perform(async () => {
      const response = await request(`/exports/${preview.export_id}/download`);
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a"); link.href = url; link.download = `catalog-${preview.export_id}.zip`; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  }

  return <div className="space-y-6 p-6">
    <header><h1 className="text-2xl font-semibold">Bug Bounty</h1><p className="text-sm text-muted-foreground">Program scope and scan inputs. Shared catalog, independent of the selected Osmedeus organization.</p></header>
    {error && <div role="alert"><ErrorState title="Catalog unavailable" message={error} onRetry={() => setRevision(v => v + 1)} /></div>}
    <section className="space-y-3 rounded-lg border bg-card p-4" aria-label="Sources">
      <div className="flex flex-wrap items-center gap-3"><h2 className="font-semibold">Sources</h2><Button disabled={busy || jobs.some(j => j.status === "running")} onClick={() => void perform(async () => { await json("/refresh", { method: "POST", body: JSON.stringify({ source: source || "all", include_vdp: schedule?.include_vdp || false }) }); setRevision(v => v + 1); toast.success("Refresh requested"); })}>Refresh sources</Button></div>
      <div className="grid gap-3 md:grid-cols-3">{overview?.sources.map(s => <div key={s.name} className="rounded-md border p-3 text-sm"><strong>{s.name}</strong><p>{s.latest?.status || "Not fetched"}</p><p className="text-muted-foreground">Last success: {s.last_success ? new Date(s.last_success).toLocaleString() : "Never"}{s.age_seconds != null ? ` (${Math.floor(s.age_seconds / 3600)}h old)` : ""}</p>{s.latest?.error && <p className="text-destructive">{s.latest.error}</p>}</div>)}</div>
      {jobs.slice(0, 3).map(job => <p key={job.id} className="text-sm">Refresh {new Date(job.created_at).toLocaleString()}: {job.status}{job.result?.error ? ` — ${job.result.error}` : ""}</p>)}
      {schedule && <details><summary className="cursor-pointer text-sm">Refresh schedule</summary><div className="mt-3 flex flex-wrap items-center gap-3">
        <label className="text-sm"><input type="checkbox" checked={schedule.enabled} onChange={e => setSchedule({ ...schedule, enabled: e.target.checked })} /> Daily refresh</label>
        <Input aria-label="Refresh time" type="time" className="w-32" value={schedule.time} onChange={e => setSchedule({ ...schedule, time: e.target.value })} />
        <Input aria-label="Timezone" className="w-56" value={schedule.timezone} onChange={e => setSchedule({ ...schedule, timezone: e.target.value })} />
        <label className="text-sm"><input type="checkbox" checked={schedule.include_vdp} onChange={e => setSchedule({ ...schedule, include_vdp: e.target.checked })} /> Include VDPs where supported</label>
        <Button disabled={busy} variant="outline" onClick={() => void perform(async () => { setSchedule(await json<Schedule>("/schedule", { method: "PUT", body: JSON.stringify(schedule) })); toast.success("Schedule saved"); })}>Save schedule</Button>
      </div></details>}
    </section>
    <section className="space-y-3 rounded-lg border bg-card p-4" aria-label="Programs">
      <h2 className="font-semibold">Programs with scan inputs ({programs.total})</h2>
      <p className="text-sm text-muted-foreground">Only active programs with at least one usable in-scope domain, wildcard, IP or CIDR are shown. Other programs remain stored. Wildcards are discovery scope, not independent apex authorization.</p>
      <div className="flex flex-wrap gap-3"><Input aria-label="Search programs" placeholder="Search programs" className="max-w-sm" value={search} onChange={e => { setSearch(e.target.value); resetPrograms(); }} />
        <select aria-label="Source" className={selectClass} value={source} onChange={e => { setSource(e.target.value); resetPrograms(); }}><option value="">All sources</option>{["rix-scope", "chaos", "arkadiyt"].map(s => <option key={s}>{s}</option>)}</select>
        <select aria-label="Platform" className={selectClass} value={platform} onChange={e => { setPlatform(e.target.value); resetPrograms(); }}><option value="">All platforms</option>{overview?.facets.platforms?.map(p => <option key={p.value}>{p.value}</option>)}</select>
        <Input aria-label="Filter targets" placeholder="Domain / scope text" className="max-w-sm" value={target} onChange={e => { setTarget(e.target.value); resetPrograms(); }} />
        <select aria-label="Scope" className={selectClass} value={scope} onChange={e => { setScope(e.target.value); resetPrograms(); }}><option value="in_scope">In scope</option><option value="out_of_scope">Out of scope</option><option value="candidate">Candidate</option><option value="">All scope</option></select>
        <select aria-label="Asset type" className={selectClass} value={kind} onChange={e => { setKind(e.target.value); resetPrograms(); }}><option value="">All types</option><option value="domains">Exact domains</option><option value="wildcards">Wildcard domains</option><option value="ip">IP addresses</option><option value="cidr">CIDRs</option><option value="url">URLs</option></select>
      </div>
      <p className="text-sm text-muted-foreground">Counts describe matching catalog records, before exclusions and export conversion. Source snapshot ages are shown above. Only the reviewed export determines eligible target counts.</p>
      <div className="flex flex-wrap gap-3"><Button variant="outline" disabled={!programs.total} onClick={() => { setAllPrograms(true); setSelectedPrograms([]); resetAssets(); }}>Select all matching programs</Button><Button variant="ghost" onClick={() => { setAllPrograms(false); setSelectedPrograms([]); resetAssets(); }}>Clear program selection</Button><span className="self-center text-sm">{allPrograms ? `${programs.total} matching programs (all pages)` : `${selectedPrograms.length} programs selected across pages`}</span></div>
      <DataGrid rows={programs.items} columns={programColumns} getRowId={row => String(row.id)} height={300} />
      <div className="flex gap-3"><Button variant="outline" disabled={!programOffset} onClick={() => setProgramOffset(v => v - 50)}>Previous programs</Button><Button variant="outline" disabled={!programs.has_more} onClick={() => setProgramOffset(v => v + 50)}>Next programs</Button><span className="self-center text-sm">{programOffset + 1}–{programOffset + programs.items.length}</span></div>
    </section>
    {program && <section className="space-y-3 rounded-lg border bg-card p-4" aria-label="Program assets">
      <h2 className="font-semibold">{program.name} — assets ({assets.total})</h2>
      <p className="text-sm text-muted-foreground">{program.platform} · {program.status || "Unknown status"}{program.is_active === false ? " · Inactive" : ""}</p>
      {program.description && <p className="whitespace-pre-wrap text-sm">{program.description}</p>}
      {program.source_records && <details><summary className="cursor-pointer text-sm">Source provenance</summary><ul className="mt-2 space-y-1 text-sm">{program.source_records.map(record => <li key={`${record.source}:${record.source_id}`}>{record.source}: {record.source_id} ({record.is_active ? "active" : "inactive"})</li>)}</ul></details>}
      <div className="flex flex-wrap gap-3">{program.policy_links?.map(link => <a key={link.url} className="text-sm text-primary underline" href={link.url} target="_blank" rel="noopener noreferrer">Policy ({link.source})</a>)}</div>
      <DataGrid rows={assets.items} columns={assetColumns} getRowId={row => String(row.id)} height={300} />
      <div className="flex flex-wrap gap-3"><Button variant="outline" disabled={!assetOffset} onClick={() => setAssetOffset(v => v - 50)}>Previous assets</Button><Button variant="outline" disabled={!assets.has_more} onClick={() => setAssetOffset(v => v + 50)}>Next assets</Button><span className="self-center text-sm">{selected.length} selected across pages</span><Button variant="ghost" onClick={() => { setSelected([]); setPreview(null); }}>Clear selection</Button></div>
      <details className="text-sm"><summary className="cursor-pointer">Export tools</summary><div className="mt-3 space-y-3">
      <label className="block text-sm"><input type="checkbox" checked={wildcards} onChange={e => { setWildcards(e.target.checked); setPreview(null); }} /> Convert wildcards to discovery roots (does not authorize the apex)</label>
      <p className="text-sm text-muted-foreground">Exports preserve exclusions and scope metadata. Your chosen workflow must enforce those restrictions.</p>
      <div className="flex gap-3"><Button disabled={busy || !assets.total} onClick={() => void exportInputs(false)}>Preview all matching inputs</Button><Button variant="outline" disabled={busy || !selected.length} onClick={() => void exportInputs(true)}>Preview selected inputs</Button></div>
      </div></details>
    </section>}
    <ScanControl selectionRevision={selectionRevision} selection={{ mode: allPrograms ? "filtered" : "ids", program_ids: allPrograms ? [] : selected.length && program ? [program.id] : selectedPrograms, asset_ids: selected, filters: programFilters }} selectionCount={allPrograms ? programs.total : selected.length && program ? 1 : selectedPrograms.length} />
    <details className="rounded-lg border bg-card p-4"><summary className="cursor-pointer text-sm">Exports and delivery</summary><div className="mt-3 space-y-3">
    {preview && <section className="space-y-3 rounded-lg border bg-card p-4" aria-label="Export preview"><h2 className="font-semibold">Export preview</h2><p className="text-sm">Snapshot: {new Date(preview.created_at).toLocaleString()}</p>{preview.programs.map(p => <div key={p.id}><h3>{p.name}</h3><p className="text-sm">{Object.entries(p.counts).map(([name, count]) => `${name}: ${count}`).join(" · ")}</p><details><summary className="cursor-pointer text-sm">{p.omitted.length} omitted · {p.conversions.length} wildcard conversions</summary><pre className="max-h-64 overflow-auto text-xs">{JSON.stringify({ omitted: p.omitted, conversions: p.conversions }, null, 2)}</pre></details></div>)}<Button disabled={busy} onClick={() => void download()}>Download ZIP</Button></section>}
    <DeliveryPanel exportId={preview?.export_id} />
    </div></details>
  </div>;
}
