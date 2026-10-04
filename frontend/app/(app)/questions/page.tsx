"use client";

import { RefreshCw, Target } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/app-shell";
import { ScoreValue } from "@/components/live/score";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "@/lib/api";
import { pct, shortDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { BankItem } from "@/types/api";

interface Bank {
  categories: string[];
  counts: Record<string, number>;
  items: BankItem[];
}

export default function QuestionsPage() {
  const [bank, setBank] = useState<Bank | null>(null);
  const [category, setCategory] = useState("");
  const [difficulty, setDifficulty] = useState("");
  const [sort, setSort] = useState("relevance");

  const load = useCallback(() => {
    const qs = new URLSearchParams({ sort });
    if (category) qs.set("category", category);
    if (difficulty) qs.set("difficulty", difficulty);
    api<Bank>(`/question-bank?${qs}`).then(setBank).catch((e: Error) => toast.error(e.message));
  }, [category, difficulty, sort]);
  useEffect(load, [load]);

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        title="Question bank"
        description="Generated from your verified profile, your target jobs and a curated set - personalised and tracked."
        actions={
          <Button
            variant="outline"
            onClick={async () => {
              const r = await api<{ count: number }>("/question-bank/rebuild", { method: "POST" });
              toast.success(`Question bank rebuilt (${r.count} questions)`);
              load();
            }}
          >
            <RefreshCw /> Rebuild
          </Button>
        }
      />
      <div className="mb-4 flex flex-wrap items-center gap-2" role="group" aria-label="Filters">
        <div className="flex flex-wrap gap-1">
          <Button size="sm" variant={category === "" ? "default" : "outline"} onClick={() => setCategory("")}>All</Button>
          {bank?.categories.map((c) => (
            <Button key={c} size="sm" variant={category === c ? "default" : "outline"} onClick={() => setCategory(c)}>
              {c} <span className="text-xs opacity-70">{bank.counts[c]}</span>
            </Button>
          ))}
        </div>
        <select aria-label="Difficulty" className="h-8 rounded-lg border bg-transparent px-2 text-sm" value={difficulty} onChange={(e) => setDifficulty(e.target.value)}>
          <option value="">Any difficulty</option>
          <option value="easy">Easy</option>
          <option value="medium">Medium</option>
          <option value="hard">Hard</option>
        </select>
        <select aria-label="Sort" className="h-8 rounded-lg border bg-transparent px-2 text-sm" value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="relevance">Most relevant</option>
          <option value="weakest">Weakest first</option>
          <option value="recent">Recently practised</option>
          <option value="difficulty">Difficulty</option>
        </select>
      </div>
      {!bank ? (
        <Skeleton className="h-96 w-full" />
      ) : (
        <div className="rounded-xl border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Question</TableHead>
                <TableHead>Difficulty</TableHead>
                <TableHead className="hidden md:table-cell">Expected concepts</TableHead>
                <TableHead className="text-right">Resume</TableHead>
                <TableHead className="text-right">Job</TableHead>
                <TableHead className="hidden lg:table-cell">Last practised</TableHead>
                <TableHead className="text-right">Performance</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {bank.items.map((i) => (
                <TableRow key={i.id}>
                  <TableCell className="max-w-md whitespace-normal">
                    <div className="font-medium">{i.question}</div>
                    <div className="text-xs text-muted-foreground">{i.category} · {i.source}</div>
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline" className={cn("capitalize", i.difficulty === "hard" && "border-destructive/40", i.difficulty === "easy" && "border-success/40")}>{i.difficulty}</Badge>
                  </TableCell>
                  <TableCell className="hidden max-w-xs whitespace-normal text-xs text-muted-foreground md:table-cell">{i.expected_concepts.join(", ")}</TableCell>
                  <TableCell className="text-right tabular-nums">{pct(i.resume_relevance)}</TableCell>
                  <TableCell className="text-right tabular-nums">{pct(i.job_relevance)}</TableCell>
                  <TableCell className="hidden text-xs text-muted-foreground lg:table-cell">{i.last_practiced ? shortDate(i.last_practiced) : "Never"}</TableCell>
                  <TableCell className="text-right">{i.performance !== null ? <><ScoreValue value={i.performance} /> <span className="text-xs text-muted-foreground">×{i.attempts}</span></> : "-"}</TableCell>
                  <TableCell>
                    <Button size="xs" variant="outline" render={<Link href={`/practice?q=${i.id}`} />}><Target /> Practise</Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
