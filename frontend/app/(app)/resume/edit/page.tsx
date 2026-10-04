"use client";

import { ArrowLeft, FileText, Loader2, Pencil, Plus, RefreshCw, Trash2, Upload } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/app-shell";
import { EditDialog, FIELDS } from "@/components/profile/edit-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { useProfile } from "@/hooks/use-profile";
import { api } from "@/lib/api";
import { shortDate } from "@/lib/format";
import type { Project } from "@/types/api";

interface Doc {
  id: string;
  kind: string;
  filename: string;
  pages: number;
  created_at: string;
  project_id: string | null;
  project: string | null;
}

function PersonalForm({ initial, onSave }: { initial: Record<string, string>; onSave: (d: Record<string, unknown>) => Promise<void> }) {
  const [v, setV] = useState(initial);
  const [saving, setSaving] = useState(false);
  const field = (key: string, label: string, textarea = false) => (
    <div className="space-y-1">
      <Label htmlFor={`p-${key}`}>{label}</Label>
      {textarea ? (
        <Textarea id={`p-${key}`} rows={3} value={v[key] ?? ""} onChange={(e) => setV({ ...v, [key]: e.target.value })} />
      ) : (
        <Input id={`p-${key}`} value={v[key] ?? ""} onChange={(e) => setV({ ...v, [key]: e.target.value })} />
      )}
    </div>
  );
  return (
    <form
      className="grid gap-3 sm:grid-cols-2"
      onSubmit={async (e) => {
        e.preventDefault();
        setSaving(true);
        try {
          await onSave({ ...v, links: (v.links ?? "").split(/[\n,]/).map((x) => x.trim()).filter(Boolean) });
        } finally {
          setSaving(false);
        }
      }}
    >
      {field("name", "Full name")}
      {field("headline", "Headline")}
      {field("email", "Email")}
      {field("phone", "Phone")}
      {field("location", "Location")}
      {field("links", "Links (comma separated)")}
      <div className="sm:col-span-2">{field("summary", "Summary", true)}</div>
      <div className="sm:col-span-2">
        <Button type="submit" disabled={saving}>{saving ? "Saving…" : "Save & verify personal details"}</Button>
      </div>
    </form>
  );
}

function ProjectDetail({ p, onEdit, docs, onUpload }: { p: Project; onEdit: () => void; docs: Doc[]; onUpload: (f: File) => Promise<void> }) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const lists = FIELDS.project.filter((f) => f.type === "list" && f.key !== "technologies");
  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-2">
        <div>
          <CardTitle className="flex items-center gap-2">{p.name} {p.verified ? <Badge variant="secondary">Verified</Badge> : <Badge variant="outline">Unverified</Badge>}</CardTitle>
          {p.description ? <CardDescription>{p.description}</CardDescription> : null}
        </div>
        <Button size="sm" variant="outline" onClick={onEdit}><Pencil /> Edit</Button>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div className="flex flex-wrap gap-1">{p.technologies.map((t) => <Badge key={t} variant="outline">{t}</Badge>)}</div>
        <dl className="grid gap-3 sm:grid-cols-2">
          {[["Problem", p.problem], ["Solution", p.solution], ["Your role", p.candidate_role]].map(([k, val]) => (
            <div key={k as string}>
              <dt className="text-xs font-semibold uppercase text-muted-foreground">{k}</dt>
              <dd className={val ? "" : "text-muted-foreground italic"}>{val || "Not recorded"}</dd>
            </div>
          ))}
          {lists.map((f) => {
            const vals = (p as unknown as Record<string, string[]>)[f.key] ?? [];
            return (
              <div key={f.key}>
                <dt className="text-xs font-semibold uppercase text-muted-foreground">{f.label}</dt>
                {vals.length ? <dd><ul className="list-disc pl-4">{vals.map((x) => <li key={x}>{x}</li>)}</ul></dd> : <dd className="italic text-muted-foreground">Not recorded</dd>}
              </div>
            );
          })}
        </dl>
        <div className="border-t pt-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-semibold uppercase text-muted-foreground">Project documentation</span>
            <input
              ref={input}
              type="file"
              accept=".pdf,.docx,.txt"
              className="sr-only"
              aria-label={`Upload documentation for ${p.name}`}
              onChange={async (e) => {
                const f = e.target.files?.[0];
                e.target.value = "";
                if (!f) return;
                setBusy(true);
                try {
                  await onUpload(f);
                } finally {
                  setBusy(false);
                }
              }}
            />
            <Button size="xs" variant="outline" onClick={() => input.current?.click()} disabled={busy}>
              {busy ? <Loader2 className="animate-spin" /> : <Upload />} Add document
            </Button>
          </div>
          {docs.length ? (
            <ul className="space-y-1">{docs.map((d) => <li key={d.id} className="flex items-center gap-2 text-xs"><FileText className="size-3.5" /> {d.filename}</li>)}</ul>
          ) : (
            <p className="text-xs text-muted-foreground">Design notes, READMEs or reports give answers more specific, grounded detail.</p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

export default function ResumeEditPage() {
  const { profile, loading, apply } = useProfile();
  const [docs, setDocs] = useState<Doc[]>([]);
  const [edit, setEdit] = useState<{ row: Project | null } | null>(null);
  const [reindexing, setReindexing] = useState(false);

  const loadDocs = () => api<Doc[]>("/documents").then(setDocs).catch(() => undefined);
  useEffect(() => {
    void loadDocs();
  }, []);

  if (loading || !profile) return <Skeleton className="h-96 w-full" />;
  const personal = profile.personal;
  const initial: Record<string, string> = {
    name: personal.name ?? "", headline: personal.headline ?? "", email: personal.email ?? "", phone: personal.phone ?? "",
    location: personal.location ?? "", links: personal.links.join(", "), summary: personal.summary ?? "",
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <PageHeader
        title="Profile editor"
        description="Your canonical candidate profile. Everything here is what answers are grounded in."
        actions={
          <>
            <Button variant="outline" render={<Link href="/resume" />}><ArrowLeft /> Verification</Button>
            <Button
              variant="outline"
              disabled={reindexing}
              onClick={async () => {
                setReindexing(true);
                try {
                  const r = await api<{ knowledge_base: { chunks: number }; question_bank: number }>("/resume/reindex", { method: "POST" });
                  toast.success(`Knowledge base rebuilt (${r.knowledge_base.chunks} chunks) · question bank ${r.question_bank} items`);
                } catch (e) {
                  toast.error((e as Error).message);
                } finally {
                  setReindexing(false);
                }
              }}
            >
              {reindexing ? <Loader2 className="animate-spin" /> : <RefreshCw />} Rebuild knowledge base
            </Button>
          </>
        }
      />
      <Card id="personal">
        <CardHeader>
          <CardTitle>Personal information</CardTitle>
          <CardDescription>{personal.verified ? "Verified" : "Not yet verified"}</CardDescription>
        </CardHeader>
        <CardContent>
          <PersonalForm key={JSON.stringify(initial)} initial={initial} onSave={async (d) => void (await apply([{ action: "personal", data: d }], "Personal details saved"))} />
        </CardContent>
      </Card>

      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Projects</h2>
        <Button size="sm" variant="outline" onClick={() => setEdit({ row: null })}><Plus /> Add project</Button>
      </div>
      {profile.projects.map((p) => (
        <ProjectDetail
          key={p.id}
          p={p}
          docs={docs.filter((d) => d.project_id === p.id)}
          onEdit={() => setEdit({ row: p })}
          onUpload={async (file) => {
            const form = new FormData();
            form.append("file", file);
            form.append("project_id", p.id);
            try {
              const r = await api<{ id: string; chunks: number }>("/documents", { form });
              toast.success(`Indexed ${r.chunks} passage${r.chunks === 1 ? "" : "s"} for ${p.name}`);
              await loadDocs();
            } catch (e) {
              toast.error((e as Error).message);
            }
          }}
        />
      ))}

      <Card>
        <CardHeader>
          <CardTitle>Uploaded documents</CardTitle>
          <CardDescription>Supplementary documents in your knowledge base.</CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="divide-y text-sm">
            {docs.map((d) => (
              <li key={d.id} className="flex items-center justify-between gap-3 py-2">
                <span className="flex items-center gap-2"><FileText className="size-4" /> {d.filename} <span className="text-xs text-muted-foreground">{d.kind.replace("_", " ")} · {shortDate(d.created_at)}</span></span>
                {d.kind !== "resume" && d.kind !== "job_description" ? (
                  <Button
                    size="icon-sm"
                    variant="ghost"
                    aria-label={`Delete ${d.filename}`}
                    onClick={async () => {
                      try {
                        await api(`/documents/${d.id}`, { method: "DELETE" });
                        toast.success("Document deleted");
                        await loadDocs();
                      } catch (e) {
                        toast.error((e as Error).message);
                      }
                    }}
                  >
                    <Trash2 />
                  </Button>
                ) : null}
              </li>
            ))}
            {!docs.length ? <li className="py-2 text-muted-foreground">No documents yet.</li> : null}
          </ul>
        </CardContent>
      </Card>

      {edit ? (
        <EditDialog
          open
          onOpenChange={(o) => !o && setEdit(null)}
          kind="project"
          row={edit.row as unknown as Record<string, unknown> | null}
          title={edit.row ? `Edit ${edit.row.name}` : "Add project"}
          onSave={async (payload) => {
            if (edit.row) await apply([{ action: "edit", kind: "project", id: edit.row.id, data: payload }], "Project saved");
            else await apply([{ action: "add", kind: "project", data: payload }], "Project added");
          }}
        />
      ) : null}
    </div>
  );
}
