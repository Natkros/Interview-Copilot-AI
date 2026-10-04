"use client";

import { BadgeCheck, BookOpen, Briefcase, FileText, History } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { cn } from "@/lib/utils";
import type { SourceRef } from "@/types/api";

const GROUPS: { key: string; label: string; icon: typeof FileText; match: (s: SourceRef) => boolean }[] = [
  { key: "candidate", label: "Your profile & documents", icon: FileText, match: (s) => ["projects", "experience", "candidate_documents"].includes(s.collection) },
  { key: "history", label: "Earlier in this interview", icon: History, match: (s) => s.collection === "interview_history" },
  { key: "job", label: "Job description", icon: Briefcase, match: (s) => s.collection === "job_descriptions" },
  { key: "technical", label: "Technical reference", icon: BookOpen, match: (s) => s.collection === "technical_knowledge" },
];

export function SourcesSheet({
  open,
  onOpenChange,
  sources,
  title,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  sources: SourceRef[];
  title?: string;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>Sources</SheetTitle>
          <SheetDescription>{title ?? "Evidence retrieved for this answer. Select a source to read the passage."}</SheetDescription>
        </SheetHeader>
        <div className="space-y-5 px-4 pb-6">
          {sources.length === 0 ? <p className="text-sm text-muted-foreground">No sources were needed for this answer.</p> : null}
          {GROUPS.map((g) => {
            const items = sources.filter(g.match);
            if (!items.length) return null;
            const Icon = g.icon;
            return (
              <section key={g.key} aria-label={g.label}>
                <h3 className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  <Icon className="size-3.5" aria-hidden /> {g.label}
                </h3>
                <ul className="space-y-2">
                  {items.map((s) => {
                    const isOpen = selected === s.id;
                    const parts = s.label.split(" -> ");
                    return (
                      <li key={s.id}>
                        <button
                          type="button"
                          onClick={() => setSelected(isOpen ? null : s.id)}
                          aria-expanded={isOpen}
                          className={cn(
                            "w-full rounded-lg border p-3 text-left text-sm transition-colors hover:bg-muted/60 focus-visible:outline-2 focus-visible:outline-ring",
                            isOpen && "bg-muted/60",
                          )}
                        >
                          <div className="flex items-start justify-between gap-2">
                            <div>
                              <div className="font-medium">{parts[0]}</div>
                              {parts.length > 1 ? (
                                <div className="text-xs text-muted-foreground">{parts.slice(1).join(" → ")}</div>
                              ) : null}
                            </div>
                            {g.key === "candidate" ? (
                              s.verified ? (
                                <Badge variant="secondary" className="shrink-0">
                                  <BadgeCheck /> Verified
                                </Badge>
                              ) : (
                                <Badge variant="outline" className="shrink-0">Unverified</Badge>
                              )
                            ) : null}
                          </div>
                          {isOpen ? <p className="mt-2 whitespace-pre-wrap border-t pt-2 text-sm leading-relaxed">{s.excerpt}</p> : null}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </section>
            );
          })}
        </div>
      </SheetContent>
    </Sheet>
  );
}
