"use client";

import * as React from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type DeliveredFile = { member: string; program_name?: string; kind: string; is_target_file: boolean; path: string; lines: number; cli_template?: string; run_request_template?: Record<string, string> };
type Delivery = { export_id: string; status: string; created_at: string; total_files: number; files: DeliveredFile[]; error?: string; pending_member?: string };

async function api(path: string, init?: RequestInit) {
  const response = await fetch(`/catalog-api/v1${path}`, { ...init, headers: { "Content-Type": "application/json", Authorization: `Bearer ${localStorage.getItem("osmedeus_token") || ""}` } });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(typeof body?.detail === "string" ? body.detail : `Delivery service unavailable (${response.status})`);
  return body;
}

export function DeliveryPanel({ exportId }: { exportId?: string }) {
  const [items, setItems] = React.useState<Delivery[]>([]);
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [revision, setRevision] = React.useState(0);
  React.useEffect(() => {
    let live = true;
    const load = () => api("/deliveries").then(body => { if (live) setItems(body.items); }).catch(e => { if (live) setError(e.message); });
    void load();
    const timer = setInterval(load, 3000);
    return () => { live = false; clearInterval(timer); };
  }, [revision]);
  const current = items.find(item => item.export_id === exportId);
  async function deliver(id: string, retry = false) {
    setBusy(true); setError("");
    try { await api(`/exports/${id}/deliver`, { method: "POST", body: JSON.stringify({ retry }) }); setRevision(v => v + 1); }
    catch (e) { setError(e instanceof Error ? e.message : "Delivery failed"); }
    finally { setBusy(false); }
  }
  function downloadReceipt(item: Delivery) {
    const url = URL.createObjectURL(new Blob([JSON.stringify(item, null, 2)], { type: "application/json" }));
    const link = document.createElement("a"); link.href = url; link.download = `delivery-${item.export_id}.json`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <section className="space-y-3 rounded-lg border bg-card p-4" aria-label="Osmedeus delivery">
    <h2 className="font-semibold">Send inputs to Osmedeus</h2>
    <p className="text-sm text-muted-foreground">Preview a program above, then upload its target files and scope metadata directly to the engine. This does not start a scan or create discovered assets.</p>
    {exportId && <Button disabled={busy || !!current} onClick={() => void deliver(exportId)}>Send preview to Osmedeus</Button>}
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    <p className="text-sm text-muted-foreground">Delivered snapshots are immutable. After a scope refresh, create and send a new preview. Scope metadata must still be enforced by your workflow.</p>
    {items.map(item => <details key={item.export_id} open={item.export_id === exportId} className="rounded-md border p-3">
      <summary className="cursor-pointer text-sm">{item.status === "ready" ? "Ready in Osmedeus" : item.status} · {item.files.length}/{item.total_files} files · {item.export_id.slice(0, 8)}</summary>
      {item.error && <p className="mt-2 text-sm text-destructive">{item.error}</p>}
      {item.status !== "ready" && item.status !== "uploading" && <div className="mt-2 space-y-2"><p className="text-sm">A file may have reached Osmedeus without confirmation. Explicit retry skips confirmed files but can leave duplicate unconfirmed uploads.</p><Button variant="outline" disabled={busy} onClick={() => void deliver(item.export_id, true)}>Retry unconfirmed delivery</Button></div>}
      {item.status === "ready" && item.files.filter(file => file.is_target_file).map(file => <div key={file.member} className="mt-3 space-y-2">
        <h3 className="text-sm font-medium">{file.program_name} / {file.kind} ({file.lines} targets)</h3>
        {file.kind === "discovery-roots" && <p className="text-sm text-destructive">Discovery seeds only: wildcard scope does not independently authorize the apex.</p>}
        <Input aria-label={`${file.kind} engine path`} readOnly value={file.path} />
        <pre className="overflow-x-auto text-xs">{file.cli_template}</pre>
        <details><summary className="cursor-pointer text-sm">Run request template (not submitted)</summary><pre className="overflow-x-auto text-xs">{JSON.stringify(file.run_request_template, null, 2)}</pre></details>
      </div>)}
      <Button className="mt-3" variant="outline" onClick={() => downloadReceipt(item)}>Download delivery receipt</Button>
    </details>)}
  </section>;
}
