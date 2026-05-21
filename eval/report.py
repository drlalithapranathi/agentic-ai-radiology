"""Render an EvalSummary as a human-readable markdown report."""

from __future__ import annotations

from eval import scorers


def render_markdown(summary: scorers.EvalSummary, base_url: str, generated_at: str) -> str:
    lines: list[str] = []
    lines.append(f"# CritCom eval report — {generated_at}")
    lines.append("")
    lines.append(f"- **Target**: `{base_url}`")
    lines.append(f"- **Cases**: {summary.n_cases}")
    lines.append(f"- **Mean latency**: {summary.elapsed_seconds_mean:.1f}s")
    lines.append("")

    lines.append("## Headline metrics")
    lines.append("")
    lines.append("| Metric | Value | Source |")
    lines.append("|---|---|---|")
    lines.append(f"| Overall pass rate | **{summary.overall_pass_rate:.1%}** | combined |")
    lines.append(f"| Classification accuracy | {summary.classification.accuracy:.1%} | ART / MedAgentBench |")
    lines.append(f"| Trajectory F1 (tool selection) | {summary.trajectory_f1_mean:.2f} | TRAJECT-Bench (2025) |")
    lines.append(f"| Trajectory order correctness | {summary.trajectory_order_rate:.1%} | TRAJECT-Bench (2025) |")
    lines.append(f"| State validity (Communication + Task) | {summary.state_pass_rate:.1%} | FHIR-AgentEval (2026) |")
    lines.append(f"| Deadline compliance (ACR) | {summary.deadline_pass_rate:.1%} | ACR Practice Parameter |")
    if summary.reliability:
        lines.append(f"| pass^{summary.reliability.k} reliability | {summary.reliability.pass_at_k:.1%} | tau-bench (Sierra) |")
    lines.append("")

    lines.append("## Classification confusion matrix")
    lines.append("")
    lines.append("Rows = expected, columns = predicted.")
    lines.append("")
    cats = list(summary.classification.confusion.keys()) or ["Cat1", "Cat2", "Cat3", "None"]
    lines.append("| | " + " | ".join(cats) + " |")
    lines.append("|---|" + "|".join("---" for _ in cats) + "|")
    for row in cats:
        cells = [str(summary.classification.confusion.get(row, {}).get(col, 0)) for col in cats]
        lines.append(f"| **{row}** | " + " | ".join(cells) + " |")
    lines.append("")

    lines.append("### Per-class precision / recall / F1")
    lines.append("")
    lines.append("| Category | Precision | Recall | F1 | Support |")
    lines.append("|---|---|---|---|---|")
    for cat, m in summary.classification.per_class.items():
        if m.get("support", 0) == 0:
            continue
        lines.append(f"| {cat} | {m['precision']:.2f} | {m['recall']:.2f} | {m['f1']:.2f} | {int(m['support'])} |")
    lines.append("")

    lines.append("## Per-case detail")
    lines.append("")
    lines.append("| Case | Expected | Predicted | Cls | Traj F1 | Order | State | Deadline | Overall | Latency |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in summary.per_case:
        lines.append(
            "| `{cid}` | {ex} | {pr} | {cls} | {f1:.2f} | {ord} | {st} | {ddl} | {ov} | {lat:.1f}s |".format(
                cid=r.case_id,
                ex=r.expected_category,
                pr=r.predicted_category or "—",
                cls="✓" if r.classification_correct else "✗",
                f1=r.trajectory.selection_f1,
                ord="✓" if r.trajectory.order_match else "✗",
                st="✓" if (r.state.communication_present and r.state.task_present) or r.expected_category == "Cat3" else "✗",
                ddl="✓" if r.deadline_compliant else "✗",
                ov="**PASS**" if r.overall_pass else "FAIL",
                lat=r.elapsed_seconds,
            )
        )
    lines.append("")

    errors = [r for r in summary.per_case if r.error]
    if errors:
        lines.append("## Errors")
        lines.append("")
        for r in errors:
            lines.append(f"- `{r.case_id}`: {r.error}")
        lines.append("")

    lines.append("## Methodology notes")
    lines.append("")
    lines.append("- Trajectory scoring reads the agent's narrative reply, not its internal tool calls.")
    lines.append("  This is a conservative lower bound — tools called silently won't be credited.")
    lines.append("- State validity uses `query_audit_tool` as a side-channel into HAPI FHIR.")
    lines.append("- Deadline compliance uses ACR Practice Parameter caps: Cat1=60min, Cat2=24hr.")
    lines.append("- For higher statistical confidence, increase `--k` (pass^k) or add more cases to")
    lines.append("  `eval/fixtures/labeled_cases.json` (requires seeding the corresponding")
    lines.append("  DiagnosticReport / DICOM findings JSON).")
    return "\n".join(lines)
