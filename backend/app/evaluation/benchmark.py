"""Evaluation framework.

    python -m app.evaluation.benchmark                    # offline composer (no cost)
    python -m app.evaluation.benchmark --provider anthropic   # uses LLM_API_KEY (billed)

Builds an isolated in-memory environment (SQLite + in-process Qdrant), loads
the sample resume with every fact verified, then runs each benchmark item
through the real pipeline (classification -> resolution -> hybrid retrieval ->
generation -> grounding -> evaluation). Reports measured metrics only:

- question classification accuracy
- retrieval Recall@K / Precision@K over labelled relevant chunks
- answer correctness (labelled must / must-not content and insufficiency)
- grounding score, unsupported-claim rates (generated vs. shipped)
- first-token and end-to-end latency (p50 / p95, in-process)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

K = 5


def _setup_env(provider: str) -> None:
    os.environ.update(DATABASE_URL="sqlite://", QDRANT_URL=":memory:", EMBEDDING_PROVIDER="hash",
                      LLM_JUDGE_ENABLED="false")
    if provider == "offline":
        os.environ["LLM_PROVIDER"] = "offline"
    else:
        os.environ["LLM_PROVIDER"] = "anthropic"
        if not os.environ.get("LLM_API_KEY"):
            sys.exit("LLM_API_KEY is required for --provider anthropic")


def _matches(chunk, spec: dict[str, str]) -> bool:
    for key, val in spec.items():
        if key == "source_type":
            if chunk.source_type != val:
                return False
        elif chunk.meta.get(key) != val:
            return False
    return True


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = min(len(s) - 1, max(0, round(q * (len(s) - 1))))
    return round(s[idx], 2)


async def _run(provider: str, limit: int | None) -> dict[str, Any]:
    from app.agents.conversation import ConversationManager
    from app.agents.supervisor import (
        InterviewPipeline,
        PipelineContext,
        build_overview,
        candidate_entities,
    )
    from app.agents.validation import GroundingValidator
    from app.database.models import Base, Document, InterviewSession, User
    from app.database.session import get_engine, init_engine, session_factory
    from app.models.domain import ContextBundle
    from app.rag.indexer import reindex_profile, seed_technical_knowledge
    from app.rag.retriever import HybridRetriever
    from app.services.documents import extract_document
    from app.services.profile import (
        apply_parsed_resume,
        apply_profile_operation,
        get_or_create_profile,
    )
    from app.services.resume_parser import parse_resume

    init_engine("sqlite://")
    Base.metadata.create_all(get_engine())
    db = session_factory()()
    user = User(email="bench@example.com", password_hash="x")
    db.add(user)
    db.flush()
    raw = resources.files("app.evaluation").joinpath("fixtures/sample_resume.txt").read_bytes()
    doc = extract_document(raw, "resume.txt")
    d = Document(user_id=user.id, kind="resume", filename="resume.txt", content_type="text/plain", sha256=doc.sha256,
                 text=doc.text)
    db.add(d)
    db.flush()
    profile = get_or_create_profile(db, user)
    apply_parsed_resume(db, profile, parse_resume(doc.text, doc.pages), d.id, "resume.txt")
    apply_profile_operation(db, profile, {"action": "confirm_all"})
    seed_technical_knowledge()
    reindex_profile(db, profile)
    db.commit()

    data = json.loads(resources.files("app.evaluation").joinpath("benchmark.json").read_text(encoding="utf-8"))
    items = data["items"][:limit] if limit else data["items"]
    validator = GroundingValidator(candidate_entities(profile))
    results = []

    class RecordingPipeline(InterviewPipeline):
        last_bundle = None

        async def gather_context(self, *a, **kw):
            bundle = await super().gather_context(*a, **kw)
            RecordingPipeline.last_bundle = bundle
            return bundle

    for item in items:
        session = InterviewSession(user_id=user.id, mode="live_coaching")
        db.add(session)
        db.flush()
        mgr = ConversationManager(db, session, profile)
        pipe = RecordingPipeline(PipelineContext(db=db, user=user, profile=profile, session=session, manager=mgr,
                                                 overview=build_overview(profile)))
        for prior in item.get("conversation", []):
            mgr.store_turn("interviewer", prior)
            async for ev in pipe.run(prior):
                if ev["event"] == "answer.complete":
                    mgr.store_turn("candidate", ev["answer"]["text"], kind="suggestion")
                    mgr.record_answer(ev["answer"]["text"])
        mgr.store_turn("interviewer", item["question"])
        RecordingPipeline.last_bundle = None
        start = time.perf_counter()
        classified = complete = None
        async for ev in pipe.run(item["question"]):
            if ev["event"] == "question.classified":
                classified = ev
            elif ev["event"] == "answer.complete":
                complete = ev
        wall = (time.perf_counter() - start) * 1000
        bundle = RecordingPipeline.last_bundle or ContextBundle()
        answer = complete["answer"] if complete else {"text": "", "grounding": {}, "insufficient_context": False}
        text = answer["text"]

        # retrieval
        rel = item.get("relevant", [])
        top = bundle.candidate[:K]
        recall = precision = None
        if rel:
            found = sum(1 for spec in rel if any(_matches(c, spec) for c in top))
            recall = found / len(rel)
            precision = (sum(1 for c in top if any(_matches(c, spec) for spec in rel)) / len(top)) if top else 0.0

        # correctness
        failures = [f"missing: {s}" for s in item.get("must_include", []) if s.lower() not in text.lower()]
        failures += [f"forbidden: {s}" for s in item.get("must_not_include", []) if s.lower() in text.lower()]
        if item.get("expect_insufficient") and not answer.get("insufficient_context"):
            failures.append("expected insufficient-context handling")

        # shipped hallucinations: re-validate the final text against the full knowledge base
        pool = ContextBundle(evidence_pool=HybridRetriever(db, user.id).evidence_pool(session.id))
        _, shipped = validator.validate(text, pool, rewrite=False)
        results.append({
            "id": item["id"], "category": item["category"], "question": item["question"],
            "expected_type": item["expected_type"],
            "predicted_type": classified["classification"]["type"] if classified else None,
            "recall": recall, "precision": precision,
            "correct": not failures, "failures": failures,
            "grounding_score": answer["grounding"].get("grounding_score"),
            "generated_unsupported": len(answer["grounding"].get("removed_claims", []) or []),
            "shipped_unsupported": shipped.unsupported_claims,
            "insufficient": answer.get("insufficient_context"),
            "expect_insufficient": bool(item.get("expect_insufficient")),
            "first_token_ms": (complete or {}).get("latency", {}).get("first_token_ms"),
            "total_ms": round(wall, 2),
            "answer": text,
        })
    db.close()
    return {"items": results}


def summarise(results: list[dict[str, Any]], provider: str) -> dict[str, Any]:
    def mean(vals):
        vals = [v for v in vals if v is not None]
        return round(statistics.mean(vals), 3) if vals else None

    cats = sorted({r["category"] for r in results})
    ins_expected = [r for r in results if r["expect_insufficient"]]
    ins_flagged = [r for r in results if r["insufficient"]]
    return {
        "provider": provider,
        "run_at": datetime.now(UTC).isoformat(),
        "n_items": len(results),
        "classification_accuracy": mean([1.0 if r["predicted_type"] == r["expected_type"] else 0.0 for r in results]),
        "classification_by_category": {c: mean([1.0 if r["predicted_type"] == r["expected_type"] else 0.0
                                                for r in results if r["category"] == c]) for c in cats},
        f"retrieval_recall@{K}": mean([r["recall"] for r in results]),
        f"retrieval_precision@{K}": mean([r["precision"] for r in results]),
        "answer_correctness": mean([1.0 if r["correct"] else 0.0 for r in results]),
        "answer_correctness_by_category": {c: mean([1.0 if r["correct"] else 0.0 for r in results if r["category"] == c])
                                           for c in cats},
        "mean_grounding_score": mean([r["grounding_score"] for r in results]),
        "answers_with_generated_unsupported_claims": mean([1.0 if r["generated_unsupported"] else 0.0 for r in results]),
        "hallucination_rate_shipped": mean([1.0 if r["shipped_unsupported"] else 0.0 for r in results]),
        "insufficient_context_recall": mean([1.0 if r["insufficient"] else 0.0 for r in ins_expected]),
        "insufficient_context_precision": mean([1.0 if r["expect_insufficient"] else 0.0 for r in ins_flagged]),
        "latency_ms": {
            "first_token_p50": _pct([r["first_token_ms"] for r in results if r["first_token_ms"] is not None], 0.5),
            "first_token_p95": _pct([r["first_token_ms"] for r in results if r["first_token_ms"] is not None], 0.95),
            "end_to_end_p50": _pct([r["total_ms"] for r in results], 0.5),
            "end_to_end_p95": _pct([r["total_ms"] for r in results], 0.95),
            "note": "In-process, excludes STT and network; measured on the machine that ran the benchmark.",
        },
        "failures": [{"id": r["id"], "question": r["question"], "failures": r["failures"],
                      "predicted_type": r["predicted_type"], "expected_type": r["expected_type"]}
                     for r in results if not r["correct"] or r["predicted_type"] != r["expected_type"]],
    }


def to_markdown(s: dict[str, Any]) -> str:
    lines = [f"# InterviewOS benchmark - {s['provider']}", "", f"Run at {s['run_at']} on {s['n_items']} items.", "",
             "| Metric | Value |", "|---|---|"]
    for key in ("classification_accuracy", f"retrieval_recall@{K}", f"retrieval_precision@{K}", "answer_correctness",
                "mean_grounding_score", "answers_with_generated_unsupported_claims", "hallucination_rate_shipped",
                "insufficient_context_recall", "insufficient_context_precision"):
        lines.append(f"| {key} | {s[key]} |")
    for k, v in s["latency_ms"].items():
        if k != "note":
            lines.append(f"| latency {k} (ms) | {v} |")
    lines += ["", f"_{s['latency_ms']['note']}_", "", "## By category", "", "| Category | Classification | Correctness |",
              "|---|---|---|"]
    for c in s["classification_by_category"]:
        lines.append(f"| {c} | {s['classification_by_category'][c]} | {s['answer_correctness_by_category'][c]} |")
    if s["failures"]:
        lines += ["", "## Items with errors", ""]
        for f in s["failures"]:
            lines.append(f"- **{f['id']}** {f['question']} - predicted {f['predicted_type']} (expected {f['expected_type']})"
                         + (f"; {', '.join(f['failures'])}" if f["failures"] else ""))
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", choices=["offline", "anthropic"], default="offline")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", default="evaluation_reports")
    args = ap.parse_args()
    _setup_env(args.provider)
    import logging

    logging.disable(logging.INFO)
    raw = asyncio.run(_run(args.provider, args.limit))
    summary = summarise(raw["items"], args.provider)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    (out / f"benchmark-{args.provider}-{stamp}.json").write_text(json.dumps({"summary": summary, **raw}, indent=2), encoding="utf-8")
    md = to_markdown(summary)
    (out / f"benchmark-{args.provider}-{stamp}.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
