"use client";

import { Check, CheckCheck, FileUp, Loader2, Pencil, Plus, Trash2, Undo2, Upload, X } from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/app-shell";
import { EditDialog } from "@/components/profile/edit-dialog";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { useProfile } from "@/hooks/use-profile";
import { api } from "@/lib/api";
import { pct } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Profile, ProfileKind, ResumeResponse } from "@/types/api";

const CATEGORY_LABELS: Record<string, string> = {
  programming_language: "Programming Languages", framework: "Frameworks", library: "Libraries", database: "Databases",
  cloud: "Cloud", ai_ml: "AI/ML", devops: "DevOps", tool: "Tools", concept: "Concepts", other: "Other",
};

function UploadZone({ onUploaded }: { onUploaded: (r: ResumeResponse & { warnings?: string[] }) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [drag, setDrag] = useState(false);
  const upload = async (file: File) => {
    if (file.size > 8 * 1024 * 1024) {
      toast.error("File exceeds the 8 MB limit");
      return;
    }
    setBusy(true);
    const form = new FormData();
    form.append("file", file);
    try {
      const r = await api<ResumeResponse & { warnings: string[]; knowledge_base: { chunks: number } }>("/resume/upload", { form });
      toast.success(`Resume parsed - ${r.knowledge_base.chunks} knowledge chunks indexed. Review the details below.`);
      (r.warnings ?? []).forEach((w) => toast.warning(w));
      onUploaded(r);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setDrag(true);
      }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDrag(false);
        const f = e.dataTransfer.files[0];
        if (f) void upload(f);
      }}
      className={cn("flex flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed p-8 text-center", drag && "border-brand bg-brand/5")}
    >
      <FileUp className="size-8 text-muted-foreground" aria-hidden />
      <div>
        <p className="font-medium">Upload your resume</p>
        <p className="text-sm text-muted-foreground">PDF, DOCX or TXT, up to 8 MB. Files are validated and scanned for active content.</p>
      </div>
      <input
        ref={input}
        type="file"
        accept=".pdf,.docx,.txt,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain"
        className="sr-only"
        aria-label="Choose resume file"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) void upload(f);
          e.target.value = "";
        }}
      />
      <Button onClick={() => input.current?.click()} disabled={busy}>
        {busy ? <Loader2 className="animate-spin" /> : <Upload />} {busy ? "Parsing…" : "Choose file"}
      </Button>
    </div>
  );
}

function ItemActions({
  verified,
  onConfirm,
  onEdit,
  onRemove,
  label,
}: {
  verified: boolean;
  onConfirm: () => void;
  onEdit: () => void;
  onRemove: () => void;
  label: string;
}) {
  return (
    <div className="flex shrink-0 items-center gap-1">
      <Button size="xs" variant={verified ? "secondary" : "outline"} onClick={onConfirm} aria-label={verified ? `Mark ${label} unverified` : `Confirm ${label}`}>
        {verified ? <Undo2 /> : <Check />} {verified ? "Verified" : "Confirm"}
      </Button>
      <Button size="icon-xs" variant="ghost" onClick={onEdit} aria-label={`Edit ${label}`}><Pencil /></Button>
      <Button size="icon-xs" variant="ghost" onClick={onRemove} aria-label={`Remove ${label}`}><X /></Button>
    </div>
  );
}

function SourceNote({ source }: { source: { document?: string; section?: string; page?: number } }) {
  if (!source?.document) return null;
  return (
    <span className="text-xs text-muted-foreground">
      {source.document}
      {source.section ? ` · ${source.section}` : ""}
      {source.page ? ` · p.${source.page}` : ""}
    </span>
  );
}

export default function ResumePage() {
  const { data, profile, loading, saving, apply, setData, reload } = useProfile();
  const [edit, setEdit] = useState<{ kind: ProfileKind; row: Record<string, unknown> | null } | null>(null);

  const op = (action: "confirm" | "unconfirm" | "remove", kind: ProfileKind, id: string) => apply([{ action, kind, id }]).catch(() => undefined);

  if (loading) return <Skeleton className="h-96 w-full" />;
  const p = profile as Profile;
  const empty = p.skills.length + p.projects.length + p.experiences.length + p.items.length === 0;

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader
        title="Resume & Profile"
        description="Nothing extracted from your resume is trusted until you confirm it. Verified facts get the highest grounding priority."
        actions={
          !empty ? (
            <>
              <Button variant="outline" render={<Link href="/resume/edit" />}><Pencil /> Full editor</Button>
              <Button onClick={() => void apply([{ action: "confirm_all" }], "All items verified")} disabled={saving}>
                <CheckCheck /> Confirm all
              </Button>
            </>
          ) : null
        }
      />

      <div className="mb-6 grid gap-4 md:grid-cols-[1fr_280px]">
        <UploadZone onUploaded={(r) => setData((d) => ({ ...(d ?? r), ...r, profile: r.profile }))} />
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Verification</CardTitle>
            <CardDescription>{p.verification.verified} of {p.verification.total} facts confirmed</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <Progress value={Math.round(p.verification.ratio * 100)} aria-label="Verification progress" />
            <p className="text-2xl font-semibold tabular-nums">{pct(p.verification.ratio)}</p>
            {data?.document ? (
              <p className="text-xs text-muted-foreground">
                Source: {data.document.filename} ({data.document.pages} page{data.document.pages === 1 ? "" : "s"})
              </p>
            ) : null}
            <p className="text-xs text-muted-foreground">Knowledge base v{p.kb.version}</p>
          </CardContent>
        </Card>
      </div>

      {data?.parse_report?.warnings.length ? (
        <Alert className="mb-6">
          <AlertTitle>Review needed</AlertTitle>
          <AlertDescription>
            <ul className="list-disc pl-4">{data.parse_report.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
          </AlertDescription>
        </Alert>
      ) : null}

      {empty ? (
        <p className="text-sm text-muted-foreground">Upload a resume to build your candidate profile, or add details manually in the <Link className="underline" href="/resume/edit">full editor</Link>.</p>
      ) : (
        <div className="space-y-6">
          <Card>
            <CardHeader className="flex flex-row items-start justify-between gap-3">
              <div>
                <CardTitle>Personal information</CardTitle>
                <CardDescription>{p.personal.verified ? "Verified" : "Detected - please confirm"}</CardDescription>
              </div>
              <Button size="xs" variant="outline" render={<Link href="/resume/edit#personal" />}><Pencil /> Edit</Button>
            </CardHeader>
            <CardContent className="grid gap-1 text-sm sm:grid-cols-2">
              <div><span className="text-muted-foreground">Name: </span>{p.personal.name ?? "-"}</div>
              <div><span className="text-muted-foreground">Headline: </span>{p.personal.headline ?? "-"}</div>
              <div><span className="text-muted-foreground">Email: </span>{p.personal.email ?? "-"}</div>
              <div><span className="text-muted-foreground">Links: </span>{p.personal.links.join(", ") || "-"}</div>
              {p.personal.summary ? <p className="sm:col-span-2"><span className="text-muted-foreground">Summary: </span>{p.personal.summary}</p> : null}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-start justify-between">
              <div>
                <CardTitle>Skills</CardTitle>
                <CardDescription>Detected from your skills section and from projects/experience.</CardDescription>
              </div>
              <Button size="xs" variant="outline" onClick={() => setEdit({ kind: "skill", row: null })}><Plus /> Add</Button>
            </CardHeader>
            <CardContent className="space-y-4">
              {Object.entries(
                p.skills.reduce<Record<string, typeof p.skills>>((acc, s) => {
                  (acc[s.category] ||= []).push(s);
                  return acc;
                }, {}),
              ).map(([cat, skills]) => (
                <div key={cat}>
                  <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{CATEGORY_LABELS[cat] ?? cat}</h3>
                  <ul className="flex flex-wrap gap-2">
                    {skills.map((s) => (
                      <li key={s.id} className={cn("flex items-center gap-1 rounded-full border py-0.5 pl-3 pr-1 text-sm", s.verified ? "border-success/50 bg-success/5" : "border-dashed")}>
                        <span title={s.source?.section ? `Found in ${s.source.section}` : undefined}>{s.name}</span>
                        <button className="rounded-full p-1 hover:bg-muted" aria-label={s.verified ? `${s.name} verified - undo` : `Confirm ${s.name}`} onClick={() => void op(s.verified ? "unconfirm" : "confirm", "skill", s.id)}>
                          {s.verified ? <Check className="size-3.5 text-success" /> : <Check className="size-3.5 text-muted-foreground" />}
                        </button>
                        <button className="rounded-full p-1 hover:bg-muted" aria-label={`Edit ${s.name}`} onClick={() => setEdit({ kind: "skill", row: s as unknown as Record<string, unknown> })}>
                          <Pencil className="size-3" />
                        </button>
                        <button className="rounded-full p-1 hover:bg-muted" aria-label={`Remove ${s.name}`} onClick={() => void op("remove", "skill", s.id)}>
                          <X className="size-3.5" />
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-start justify-between">
              <div>
                <CardTitle>Projects</CardTitle>
                <CardDescription>Interviewers dig into these. Add real challenges, your role and results so answers can use them.</CardDescription>
              </div>
              <Button size="xs" variant="outline" onClick={() => setEdit({ kind: "project", row: null })}><Plus /> Add</Button>
            </CardHeader>
            <CardContent>
              <ul className="divide-y">
                {p.projects.map((pr) => {
                  const missing = [
                    !pr.challenges.length && "challenges",
                    !pr.candidate_role && "your role",
                    !pr.results.length && !pr.metrics.length && "results",
                  ].filter(Boolean) as string[];
                  return (
                    <li key={pr.id} className="flex flex-col gap-2 py-3 sm:flex-row sm:items-start sm:justify-between">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-medium">{pr.name}</span>
                          {pr.verified ? <Badge variant="secondary">Verified</Badge> : <Badge variant="outline">Unverified</Badge>}
                        </div>
                        {pr.description ? <p className="mt-1 text-sm text-muted-foreground">{pr.description}</p> : null}
                        <div className="mt-1 flex flex-wrap gap-1">{pr.technologies.map((t) => <Badge key={t} variant="outline">{t}</Badge>)}</div>
                        {missing.length ? <p className="mt-1 text-xs text-warning">Not recorded: {missing.join(", ")}. Answers won&apos;t invent these.</p> : null}
                        <SourceNote source={pr.source} />
                      </div>
                      <ItemActions
                        label={pr.name}
                        verified={pr.verified}
                        onConfirm={() => void op(pr.verified ? "unconfirm" : "confirm", "project", pr.id)}
                        onEdit={() => setEdit({ kind: "project", row: pr as unknown as Record<string, unknown> })}
                        onRemove={() => void op("remove", "project", pr.id)}
                      />
                    </li>
                  );
                })}
                {!p.projects.length ? <li className="py-3 text-sm text-muted-foreground">No projects detected.</li> : null}
              </ul>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-start justify-between">
              <CardTitle>Experience & internships</CardTitle>
              <Button size="xs" variant="outline" onClick={() => setEdit({ kind: "experience", row: null })}><Plus /> Add</Button>
            </CardHeader>
            <CardContent>
              <ul className="divide-y">
                {p.experiences.map((e) => (
                  <li key={e.id} className="flex flex-col gap-2 py-3 sm:flex-row sm:items-start sm:justify-between">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium">{e.title ?? "Role"}</span>
                        {e.organization ? <span className="text-muted-foreground">· {e.organization}</span> : null}
                        <Badge variant="outline" className="capitalize">{e.kind}</Badge>
                      </div>
                      <p className="text-xs text-muted-foreground">{[e.start_date, e.end_date].filter(Boolean).join(" - ")}</p>
                      <ul className="mt-1 list-disc pl-5 text-sm text-muted-foreground">{e.highlights.map((h) => <li key={h}>{h}</li>)}</ul>
                    </div>
                    <ItemActions
                      label={e.title ?? "experience"}
                      verified={e.verified}
                      onConfirm={() => void op(e.verified ? "unconfirm" : "confirm", "experience", e.id)}
                      onEdit={() => setEdit({ kind: "experience", row: e as unknown as Record<string, unknown> })}
                      onRemove={() => void op("remove", "experience", e.id)}
                    />
                  </li>
                ))}
                {!p.experiences.length ? <li className="py-3 text-sm text-muted-foreground">None detected.</li> : null}
              </ul>
            </CardContent>
          </Card>

          <div className="grid gap-6 md:grid-cols-2">
            <Card>
              <CardHeader className="flex flex-row items-start justify-between">
                <CardTitle>Certifications</CardTitle>
                <Button size="xs" variant="outline" onClick={() => setEdit({ kind: "certification", row: null })}><Plus /> Add</Button>
              </CardHeader>
              <CardContent>
                <ul className="divide-y">
                  {p.certifications.map((c) => (
                    <li key={c.id} className="flex items-start justify-between gap-2 py-2.5 text-sm">
                      <div>
                        <div className="font-medium">{c.name}</div>
                        <div className="text-xs text-muted-foreground">{[c.issuer, c.date].filter(Boolean).join(" · ")}</div>
                      </div>
                      <ItemActions label={c.name} verified={c.verified} onConfirm={() => void op(c.verified ? "unconfirm" : "confirm", "certification", c.id)} onEdit={() => setEdit({ kind: "certification", row: c as unknown as Record<string, unknown> })} onRemove={() => void op("remove", "certification", c.id)} />
                    </li>
                  ))}
                  {!p.certifications.length ? <li className="py-2 text-sm text-muted-foreground">None.</li> : null}
                </ul>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="flex flex-row items-start justify-between">
                <CardTitle>Education, achievements & more</CardTitle>
                <Button size="xs" variant="outline" onClick={() => setEdit({ kind: "item", row: null })}><Plus /> Add</Button>
              </CardHeader>
              <CardContent>
                <ul className="divide-y">
                  {p.items.map((it) => (
                    <li key={it.id} className="flex items-start justify-between gap-2 py-2.5 text-sm">
                      <div className="min-w-0">
                        <div className="text-xs uppercase tracking-wide text-muted-foreground">{it.kind}</div>
                        <div className="font-medium">{it.title}</div>
                        <div className="text-xs text-muted-foreground">{[it.subtitle, it.date].filter(Boolean).join(" · ")}</div>
                      </div>
                      <ItemActions label={it.title} verified={it.verified} onConfirm={() => void op(it.verified ? "unconfirm" : "confirm", "item", it.id)} onEdit={() => setEdit({ kind: "item", row: it as unknown as Record<string, unknown> })} onRemove={() => void op("remove", "item", it.id)} />
                    </li>
                  ))}
                  {!p.items.length ? <li className="py-2 text-sm text-muted-foreground">None.</li> : null}
                </ul>
              </CardContent>
            </Card>
          </div>

          <div className="flex justify-end">
            <Button
              variant="ghost"
              className="text-destructive"
              onClick={async () => {
                if (!window.confirm("Delete your uploaded resume and every fact extracted from it? Manually added facts are kept.")) return;
                try {
                  await api("/resume", { method: "DELETE" });
                  toast.success("Resume deleted");
                  await reload();
                } catch (e) {
                  toast.error((e as Error).message);
                }
              }}
            >
              <Trash2 /> Delete resume
            </Button>
          </div>
        </div>
      )}

      {edit ? (
        <EditDialog
          open
          onOpenChange={(o) => !o && setEdit(null)}
          kind={edit.kind}
          row={edit.row}
          onSave={async (payload) => {
            if (edit.row) await apply([{ action: "edit", kind: edit.kind, id: String(edit.row.id), data: payload }], "Saved and verified");
            else await apply([{ action: "add", kind: edit.kind, data: payload }], "Added");
          }}
        />
      ) : null}
    </div>
  );
}
