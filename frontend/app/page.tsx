import { BarChart3, BookOpenCheck, FileCheck2, MessagesSquare, Mic, ShieldCheck } from "lucide-react";
import Link from "next/link";

import { Logo } from "@/components/app-shell";
import { Button } from "@/components/ui/button";

const FEATURES = [
  { icon: FileCheck2, title: "Resume intelligence", text: "Parses your resume into a verified profile - projects, roles, challenges, results. Nothing is trusted until you confirm it." },
  { icon: ShieldCheck, title: "Grounded answers", text: "Every claim about you is checked against your profile before it's shown. Unsupported statements are removed, never invented." },
  { icon: MessagesSquare, title: "Follows the conversation", text: "Understands follow-ups like “why did you choose that?” and “what was the biggest challenge?” in the context of what was just discussed." },
  { icon: Mic, title: "Real-time & mock interviews", text: "Streaming speech recognition, live transcript and answer streaming - or an adaptive AI interviewer that probes your weak spots." },
  { icon: BookOpenCheck, title: "Job-aware preparation", text: "Match your resume to a job description, then practise a personalised question bank built from both." },
  { icon: BarChart3, title: "Reports & analytics", text: "Post-interview reports, topic-level scores and an adaptive preparation plan across sessions." },
];

function Turn({ who, text, right }: { who: string; text: string; right?: boolean }) {
  return (
    <div className={right ? "flex flex-col items-end" : "flex flex-col items-start"}>
      <span className="mb-1 text-[10px] font-semibold tracking-wider text-muted-foreground">{who}</span>
      <p className={`max-w-[85%] rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed ${right ? "rounded-tr-sm border border-candidate-border bg-candidate" : "rounded-tl-sm bg-interviewer"}`}>{text}</p>
    </div>
  );
}

export default function Landing() {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="mx-auto flex w-full max-w-6xl items-center justify-between px-6 py-5">
        <Logo />
        <nav className="flex gap-2">
          <Button variant="ghost" render={<Link href="/login" />}>Sign in</Button>
          <Button render={<Link href="/register" />}>Get started</Button>
        </nav>
      </header>
      <main id="main" className="flex-1">
        <section className="mx-auto grid max-w-6xl items-center gap-12 px-6 py-16 lg:grid-cols-2 lg:py-24">
          <div>
            <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">
              Your Resume. Your Experience.
              <br />
              <span className="text-brand">Your AI Interview Coach.</span>
            </h1>
            <p className="mt-5 max-w-xl text-lg text-muted-foreground">
              InterviewOS knows your projects, your role in them and the job you&apos;re targeting - and answers from verified facts
              first. When your profile doesn&apos;t say something, it tells you instead of making it up.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Button size="lg" render={<Link href="/register" />}>Create your profile</Button>
              <Button size="lg" variant="outline" render={<Link href="/login" />}>Sign in</Button>
            </div>
            <p className="mt-6 text-xs text-muted-foreground">
              Built for preparation, mock interviews, coaching, accessibility and interviews where AI assistance is explicitly permitted.
            </p>
          </div>
          <div className="rounded-2xl border bg-card p-5 shadow-sm" aria-label="Example conversation">
            <div className="mb-4 flex items-center justify-between border-b pb-3 text-sm">
              <span className="font-medium">Live session</span>
              <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-live"><span className="size-2 rounded-full bg-live" /> LIVE 12:04</span>
            </div>
            <div className="space-y-4">
              <Turn who="INTERVIEWER" text="Tell me about your RAG project." />
              <Turn who="CANDIDATE" right text="My Multimodal RAG Platform is a question-answering platform that retrieves answers across PDFs, images and tables…" />
              <Turn who="INTERVIEWER" text="Why did you choose Qdrant?" />
              <Turn who="CANDIDATE" right text="I chose Qdrant as the vector database for my Multimodal RAG Platform. In the project, I stored embeddings with metadata in Qdrant…" />
              <Turn who="INTERVIEWER" text="What was the biggest challenge?" />
              <Turn who="CANDIDATE" right text="Your project information doesn't specify a particular challenge. A safe way to answer would be to explain one you personally encountered…" />
            </div>
          </div>
        </section>
        <section className="border-t bg-muted/30">
          <div className="mx-auto grid max-w-6xl gap-6 px-6 py-16 sm:grid-cols-2 lg:grid-cols-3">
            {FEATURES.map((f) => (
              <div key={f.title} className="rounded-xl border bg-background p-5">
                <f.icon className="size-5 text-brand" aria-hidden />
                <h2 className="mt-3 font-medium">{f.title}</h2>
                <p className="mt-1.5 text-sm text-muted-foreground">{f.text}</p>
              </div>
            ))}
          </div>
        </section>
      </main>
      <footer className="border-t py-6 text-center text-xs text-muted-foreground">InterviewOS AI · Real-Time Interview Intelligence Engine</footer>
    </div>
  );
}
