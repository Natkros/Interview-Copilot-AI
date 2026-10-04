"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { ProfileKind } from "@/types/api";

export interface FieldDef {
  key: string;
  label: string;
  type?: "text" | "textarea" | "list" | "select";
  options?: { value: string; label: string }[];
  hint?: string;
  required?: boolean;
}

export const FIELDS: Record<ProfileKind, FieldDef[]> = {
  skill: [
    { key: "name", label: "Skill", required: true },
    {
      key: "category", label: "Category", type: "select", options: [
        { value: "programming_language", label: "Programming language" }, { value: "framework", label: "Framework" },
        { value: "library", label: "Library" }, { value: "database", label: "Database" }, { value: "cloud", label: "Cloud" },
        { value: "ai_ml", label: "AI/ML" }, { value: "devops", label: "DevOps" }, { value: "tool", label: "Tool" },
        { value: "concept", label: "Concept" }, { value: "other", label: "Other" },
      ],
    },
  ],
  project: [
    { key: "name", label: "Project name", required: true },
    { key: "description", label: "What it is", type: "textarea" },
    { key: "problem", label: "Problem it solves", type: "textarea" },
    { key: "solution", label: "Solution / approach", type: "textarea" },
    { key: "candidate_role", label: "Your role / contribution", type: "textarea", hint: "e.g. Sole developer; led the backend in a team of 3" },
    { key: "technologies", label: "Technologies", type: "list", hint: "One per line" },
    { key: "architecture", label: "Architecture", type: "list", hint: "One component or design point per line" },
    { key: "responsibilities", label: "What you built", type: "list" },
    { key: "challenges", label: "Challenges", type: "list", hint: "Only real challenges - answers will quote these" },
    { key: "solutions", label: "How you solved them", type: "list" },
    { key: "results", label: "Results", type: "list" },
    { key: "metrics", label: "Metrics", type: "list", hint: "Only numbers you actually measured" },
    { key: "testing", label: "Testing / evaluation", type: "list" },
    { key: "limitations", label: "Limitations", type: "list" },
    { key: "future_work", label: "Future work", type: "list" },
    { key: "url", label: "Link" },
  ],
  experience: [
    { key: "kind", label: "Type", type: "select", options: [{ value: "job", label: "Job" }, { value: "internship", label: "Internship" }] },
    { key: "title", label: "Title", required: true },
    { key: "organization", label: "Organization" },
    { key: "location", label: "Location" },
    { key: "start_date", label: "Start" },
    { key: "end_date", label: "End" },
    { key: "highlights", label: "Highlights", type: "list" },
    { key: "technologies", label: "Technologies", type: "list" },
  ],
  certification: [
    { key: "name", label: "Certification", required: true },
    { key: "issuer", label: "Issuer" },
    { key: "date", label: "Date" },
  ],
  item: [
    {
      key: "kind", label: "Type", type: "select", options: [
        { value: "education", label: "Education" }, { value: "achievements", label: "Achievement" },
        { value: "research", label: "Research" }, { value: "publications", label: "Publication" },
        { value: "leadership", label: "Leadership" }, { value: "activities", label: "Activity" },
      ],
    },
    { key: "title", label: "Title", required: true },
    { key: "subtitle", label: "Subtitle" },
    { key: "date", label: "Date" },
    { key: "details", label: "Details", type: "list" },
  ],
};

type Values = Record<string, string>;

function toValues(kind: ProfileKind, row: Record<string, unknown> | null): Values {
  const out: Values = {};
  for (const f of FIELDS[kind]) {
    const v = row?.[f.key];
    out[f.key] = Array.isArray(v) ? v.join("\n") : v === null || v === undefined ? (f.options?.[0]?.value ?? "") : String(v);
  }
  return out;
}

function toPayload(kind: ProfileKind, values: Values): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const f of FIELDS[kind]) {
    const v = values[f.key] ?? "";
    out[f.key] = f.type === "list" ? v.split("\n").map((x) => x.trim()).filter(Boolean) : v.trim() || null;
  }
  return out;
}

export function EditDialog({
  open,
  onOpenChange,
  kind,
  row,
  onSave,
  title,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  kind: ProfileKind;
  row: Record<string, unknown> | null;
  onSave: (data: Record<string, unknown>) => Promise<void>;
  title?: string;
}) {
  const [values, setValues] = useState<Values>(() => toValues(kind, row));
  const [saving, setSaving] = useState(false);

  const missing = FIELDS[kind].filter((f) => f.required && !values[f.key]?.trim());

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{title ?? (row ? "Edit" : "Add")}</DialogTitle>
          <DialogDescription>Saving marks this item as verified. Only enter facts that are true - answers will quote them.</DialogDescription>
        </DialogHeader>
        <form
          className="space-y-3"
          onSubmit={async (e) => {
            e.preventDefault();
            if (missing.length) return;
            setSaving(true);
            try {
              await onSave(toPayload(kind, values));
              onOpenChange(false);
            } finally {
              setSaving(false);
            }
          }}
        >
          {FIELDS[kind].map((f) => {
            const id = `f-${f.key}`;
            return (
              <div key={f.key} className="space-y-1">
                <Label htmlFor={id}>
                  {f.label}
                  {f.required ? <span className="text-destructive"> *</span> : null}
                </Label>
                {f.type === "select" ? (
                  <select
                    id={id}
                    className="h-8 w-full rounded-lg border bg-transparent px-2 text-sm"
                    value={values[f.key]}
                    onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}
                  >
                    {f.options!.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                ) : f.type === "textarea" || f.type === "list" ? (
                  <Textarea
                    id={id}
                    rows={f.type === "list" ? 3 : 2}
                    value={values[f.key]}
                    onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}
                  />
                ) : (
                  <Input id={id} value={values[f.key]} onChange={(e) => setValues({ ...values, [f.key]: e.target.value })} required={f.required} />
                )}
                {f.hint ? <p className="text-xs text-muted-foreground">{f.hint}</p> : null}
              </div>
            );
          })}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={saving || missing.length > 0}>{saving ? "Saving…" : "Save & verify"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
