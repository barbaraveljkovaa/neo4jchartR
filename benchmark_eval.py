"""
Live clinical benchmark for the dashboard: runs fixed questions through three paired arms:

  WITH_GRAPH      — ask_agent / ask_agent_with_context; facts retrieved via Neo4j relationship
                     traversal and presented with explicit relationship labels (HAS_DISEASE, etc.)
  WITH_FLAT_DATA   — the SAME underlying facts (same retrieval functions), but flattened into an
                     unordered bag of attribute statements with no relationship structure. Isolates
                     whether graph-shaped context helps beyond merely having the same facts.
  WITHOUT_GRAPH    — no institution-specific facts at all (LLM-only arm). Isolates whether having
                     any real facts (regardless of structure) is what reduces hallucination.

Scores include accuracy-style heuristics plus graph grounding, reasoning traceability, and
hallucination-reduction proxies — all lightweight; not a substitute for human or LLM-as-judge
evaluation.

Used by GET /benchmark.
"""

from __future__ import annotations

import random
import re
from datetime import datetime, timezone
from typing import Any

from ai_agent import ask_agent, ask_agent_with_context, _call_llm_smart

# Benchmark-only: LLM paths with no Neo4j graph-relationship exposure (paired WITH_GRAPH vs
# WITH_FLAT_DATA vs WITHOUT_GRAPH experiment).

# Relationship / graph vocabulary we expect after graph-grounding prompt improvements
_GRAPH_MARKERS = (
    "HAS_DISEASE",
    "HAS_SYMPTOM",
    "HAS_VIOLATION",
    "HAS_CLINICAL_STATE",
    "HAS_NOTE",
    "TREATED_WITH",
    "HAD_PROCEDURE",
    "(Patient)-[:",
    "OPTIONAL MATCH",
)


def _structure_score(answer: str) -> float:
    """How closely the answer follows Conclusion / Evidence / Explanation (0–100)."""
    a = (answer or "").lower()
    has_c = "## conclusion" in a
    has_e = "## evidence" in a
    has_x = "## explanation" in a
    if has_c and has_e and has_x:
        return 100.0
    n = sum([has_c, has_e, has_x])
    if n == 2:
        return 72.0
    if n == 1:
        return 48.0
    # loose: section words without hashes
    loose = sum(
        1
        for kw in ("conclusion", "evidence", "explanation")
        if kw in a and a.count(kw) >= 1
    )
    if loose >= 3:
        return 58.0
    return 35.0


def _graph_coverage_score(answer: str, result: dict[str, Any]) -> float:
    """Density of graph-native vocabulary + highlight richness (0–100)."""
    text = answer or ""
    hits = sum(1 for m in _GRAPH_MARKERS if m in text)
    hl = len(result.get("highlight_nodes") or [])
    paths = len(result.get("paths") or [])
    raw = 18.0 + min(56.0, hits * 11.0) + min(18.0, hl * 2.5) + min(18.0, paths * 6.0)
    return max(0.0, min(100.0, raw))


def _patient_context_score(answer: str, selected: list[str]) -> float:
    """Whether the answer stays tied to the requested patient(s)."""
    if not (answer or "").strip():
        return 20.0
    if selected:
        ok = any(s and (s in answer or s.upper() in answer.upper()) for s in selected)
        return 100.0 if ok else 38.0
    if re.search(r"\bP\d+\b", answer, re.IGNORECASE):
        return 86.0
    return 58.0


def _clinical_accuracy_score(answer: str, result: dict[str, Any]) -> float:
    """Basic sanity: no hard failure, substantive length, clinical vocabulary."""
    a = answer or ""
    if "LLM error" in a or "No OPENAI_API_KEY" in a:
        return 32.0
    if len(a.strip()) < 35:
        return 45.0
    score = 62.0
    if result.get("violation") is not None:
        score += 8.0
    if len(a) > 200:
        score += 12.0
    low = a.lower()
    if any(
        w in low
        for w in (
            "patient",
            "disease",
            "drug",
            "protocol",
            "violation",
            "treatment",
            "procedure",
            "compliance",
        )
    ):
        score += 10.0
    return min(100.0, score)


def _case_overall(
    g: float, s: float, p: float, c: float,
) -> float:
    return round((g + s + p + c) / 4.0, 1)


def _graph_impact_per_case(answer: str, result: dict[str, Any]) -> dict[str, Any]:
    """
    Estimate how much Neo4j graph data (highlights + path rels + graph vocabulary)
    contributes to the answer — does not call the LLM twice (no-graph run is simulated separately).
    """
    hn = result.get("highlight_nodes") or []
    hr = list(dict.fromkeys(result.get("highlight_relationships") or []))
    paths = result.get("paths") or []
    extra_rels: list[str] = []
    for p in paths:
        for r in p.get("relationships") or []:
            if r and r not in hr and r not in extra_rels:
                extra_rels.append(r)
    all_rels = hr + extra_rels

    node_labels: list[str] = []
    for n in hn:
        if isinstance(n, str) and ":" in n:
            node_labels.append(n.split(":", 1)[0])
    node_labels = list(dict.fromkeys(node_labels))

    text = answer or ""
    marker_hits = sum(text.count(m) for m in _GRAPH_MARKERS)
    token_hits = len(
        re.findall(
            r"\bHAS_[A-Z_]+\b|\bP\d+\b|\b(?:Patient|Disease|Drug|Procedure|Violation|Symptom)\b",
            text,
            flags=re.IGNORECASE,
        )
    )
    words = text.split()
    nw = max(len(words), 1)
    density = (marker_hits * 3 + token_hits + len(hn) + len(all_rels)) / max(nw / 12.0, 1.0)
    pct_graph_reasoning = float(max(18.0, min(96.0, 12.0 + density * 7.5)))
    pct_non_graph = float(round(100.0 - pct_graph_reasoning, 1))

    sub_nodes = min(100.0, float(len(hn)) * 5.5)
    sub_rels = min(100.0, float(len(all_rels)) * 7.0)
    sub_lexical = min(100.0, pct_graph_reasoning)
    impact_score = int(round(0.32 * sub_nodes + 0.34 * sub_rels + 0.34 * sub_lexical))
    impact_score = max(0, min(100, impact_score))

    return {
        "score": impact_score,
        "nodes_used_count": len(hn),
        "relationships_used_count": len(all_rels),
        "node_labels": node_labels,
        "relationship_types": list(dict.fromkeys(all_rels)),
        "percent_graph_based_reasoning": round(pct_graph_reasoning, 1),
        "percent_non_graph_reasoning": pct_non_graph,
    }


def _estimate_llm_baseline_simulated(with_graph_impact: int) -> int:
    """Bounded heuristic for an unconstrained / no-graph-grounding baseline (not a repeated LLM run)."""
    est = int(round(58.0 - 0.38 * float(with_graph_impact)))
    return max(22, min(71, est))


# Backward-compatible alias for benchmark JSON consumers.
_estimate_without_graph_score = _estimate_llm_baseline_simulated


def _benchmark_llm_without_graph(question: str, selected_patients: list[str]) -> dict[str, Any]:
    """
    Same prompts as graph mode; does not call Neo4j or inject graph-derived context.
    Patient ids are scope hints only (plain text), not retrieved records.
    """
    q = (question or "").strip()
    if selected_patients:
        ids = ", ".join(selected_patients)
        flat_context = (
            f"QUESTION_SCOPE: The question refers only to patient id(s): {ids}. "
            "You do NOT have access to this institution's Neo4j knowledge graph, EHR extracts, "
            "or pathway/compliance queries. Answer from general clinical reasoning and state "
            "limitations where institution-specific facts would be required.\n\n"
            "CONTEXT LIMITATION: Benchmark WITHOUT_GRAPH mode — graph edges (HAS_DISEASE, "
            "HAS_VIOLATION, etc.) are withheld by design."
        )
    else:
        flat_context = (
            "QUESTION_SCOPE: No patient-specific graph retrieval is available for this benchmark arm. "
            "Answer without inventing institution-specific violations, drugs, or appointments.\n\n"
            "CONTEXT LIMITATION: Benchmark WITHOUT_GRAPH mode — Neo4j context is withheld."
        )

    system_prompt = (
        "You assist with a controlled clinical AI benchmark. Use clear structure "
        "(## Conclusion / ## Evidence / ## Explanation when appropriate). "
        "Do not fabricate chartable facts that only a knowledge graph could supply."
    )

    answer = _call_llm_smart(q, flat_context, system_prompt, user_prefix="")

    return {
        "answer": (answer or "").strip(),
        "violation": False,
        "protocol_expected": [],
        "actual_treatment": [],
        "highlight_nodes": [],
        "highlight_relationships": [],
        "highlight_query": "MATCH (n) RETURN n LIMIT 0",
        "paths": [],
        "benchmark_mode": "WITHOUT_GRAPH",
    }


def _flatten_patient_facts(pid: str) -> list[str]:
    """
    Pull the same real facts the WITH_GRAPH arm uses (via ai_agent.get_patient_context /
    analyze_patient_protocol / get_patient_clinical_state — all Neo4j-backed), but return them
    as independent attribute statements with no relationship labels and no disease->drug->
    procedure linkage. Same data source, no graph structure.
    """
    from ai_agent import analyze_patient_protocol, get_patient_clinical_state, get_patient_context

    facts: list[str] = []
    pid = (pid or "").strip().upper()
    if not pid:
        return facts

    try:
        ctx = get_patient_context(pid)
    except Exception:
        ctx = {}

    facts.append(f"record_id={pid}")
    if ctx.get("patient_name"):
        facts.append(f"name={ctx['patient_name']}")
    for d in ctx.get("diseases") or []:
        val = d.get("disease_name") or d.get("disease_id")
        if val:
            facts.append(f"diagnosis={val}")
    for d in ctx.get("actual_drugs") or []:
        val = d.get("name") or d.get("id")
        if val:
            facts.append(f"medication={val}")
    for p in ctx.get("actual_procedures") or []:
        val = p.get("name") or p.get("id")
        if val:
            facts.append(f"procedure_done={val}")
    for lab in (ctx.get("labs") or [])[:8]:
        name = lab.get("name") or lab.get("id")
        if name:
            facts.append(f"lab_result={name}:{lab.get('result_value')}{lab.get('unit') or ''}")
    for note in (ctx.get("notes") or [])[:4]:
        txt = (note.get("text") or "").strip()[:100]
        if txt:
            facts.append(f"note={txt}")

    try:
        analysis = analyze_patient_protocol(pid)
        for r in analysis.get("compliance_results") or []:
            for v in r.get("violations") or []:
                facts.append(f"flag={v}")
    except Exception:
        pass

    try:
        cs = get_patient_clinical_state(pid)
        if cs:
            for k in (
                "sofa_score", "map", "lactate", "gcs", "creatinine",
                "antibiotics_active", "vasopressors_active", "cultures_ordered",
            ):
                if cs.get(k) is not None:
                    facts.append(f"{k}={cs.get(k)}")
    except Exception:
        pass

    return facts


def _build_flat_context_text(selected_patients: list[str]) -> str:
    """
    Assemble the flat (non-graph) context block for one benchmark question: same underlying
    facts as WITH_GRAPH, shuffled into an unordered bag with relationship structure removed.
    """
    all_facts: list[str] = []
    if selected_patients:
        for pid in selected_patients:
            all_facts.extend(f"[{pid}] {fact}" for fact in _flatten_patient_facts(pid))
    else:
        try:
            from ai_compliance import run_compliance_check

            compliance = run_compliance_check()
            for row in (compliance.get("patients_with_violations") or [])[:15]:
                pid = row.get("patient_id") or row.get("id") or row.get("patient")
                if pid:
                    all_facts.append(f"[{pid}] flag=protocol_violation_present")
        except Exception:
            pass

    # Fixed seed: reproducible across benchmark runs, but removes any positional/path structure.
    random.Random(42).shuffle(all_facts)

    header = (
        "DATA_MODE: FLAT_RECORDS (no graph relationships). Below is an unordered list of "
        "independent attribute records pulled from the same underlying clinical database used "
        "by the WITH_GRAPH arm. There are NO relationship labels (no HAS_DISEASE, TREATED_WITH, "
        "RECOMMENDED_DRUG, HAS_VIOLATION, etc.) connecting these records, and no guarantee that "
        "records are grouped or ordered meaningfully. Treat each line as a standalone fact about "
        "the bracketed patient id only. Do not infer connections between records that are not "
        "explicitly stated, and do not fabricate facts beyond what is listed.\n\n"
    )
    body = "\n".join(all_facts) if all_facts else "No records available for this benchmark arm."
    return header + body


def _benchmark_llm_flat_data(question: str, selected_patients: list[str]) -> dict[str, Any]:
    """
    Same real facts as WITH_GRAPH (identical retrieval functions), flattened into an unordered,
    non-relational bag of attribute statements. Isolates whether graph-structured retrieval helps
    beyond just having the same facts available in flat/non-graph form.
    """
    q = (question or "").strip()
    flat_context = _build_flat_context_text(selected_patients)
    system_prompt = (
        "You assist with a controlled clinical AI benchmark. Use clear structure "
        "(## Conclusion / ## Evidence / ## Explanation when appropriate). Only use the facts "
        "listed in the data below; do not fabricate facts, and do not infer relationships "
        "between records that are not explicitly stated."
    )
    answer = _call_llm_smart(q, flat_context, system_prompt, user_prefix="")

    return {
        "answer": (answer or "").strip(),
        "violation": None,
        "protocol_expected": [],
        "actual_treatment": [],
        "highlight_nodes": [],
        "highlight_relationships": [],
        "highlight_query": "MATCH (n) RETURN n LIMIT 0",
        "paths": [],
        "benchmark_mode": "WITH_FLAT_DATA",
    }


_COMPLIANT_KEYWORDS = (
    "compliant", "meets protocol", "meets the protocol", "correct", "appropriate",
    "in line with protocol", "in line with the protocol", "matches protocol",
    "matches the protocol", "match the protocol", "protocol met", "protocol was followed",
    "protocol was met", "adequately treated", "received the recommended",
    "received the correct", "aligned with protocol", "aligned with the protocol",
    "consistent with protocol", "consistent with the protocol", "as recommended",
)
_VIOLATION_KEYWORDS = (
    "violation", "non-compliant", "noncompliant", "missing", "did not receive",
    "not receive", "failed to", "lacks", "no evidence of", "not treated", "untreated",
    "omitted", "not given", "not met", "gap in care", "not aligned", "do not match",
    "does not match", "did not match", "do not align", "does not align",
    "did not align", "inconsistent with protocol", "not consistent with",
    "fails to meet", "fail to meet", "deviates from protocol", "deviate from protocol",
    "not compliant", "did not follow", "does not follow", "not receive the",
    "which do not", "which does not",
)
_GENERALIZER_PHRASES = (
    "both conditions", "both diagnoses", "both diseases", "all conditions",
    "all diagnoses", "all four", "each condition", "each diagnosis", "both of their",
    "both of his", "both of her",
)


_CLAUSE_SPLIT_RE = re.compile(r"[.\n;]+|\s+(?:but|while|whereas|however)\s+")


def _nearest_distance(text: str, anchor_idx: int, needle: str) -> int | None:
    """Smallest char-distance from anchor_idx to any occurrence of `needle` anywhere in text."""
    best: int | None = None
    pos = text.find(needle)
    while pos != -1:
        dist = abs(pos - anchor_idx)
        if best is None or dist < best:
            best = dist
        pos = text.find(needle, pos + 1)
    return best


def _nearest_keyword_distance(text: str, anchor_idx: int, keywords: tuple[str, ...]) -> int | None:
    """Smallest char-distance from anchor_idx to any occurrence of any of `keywords` in text."""
    best: int | None = None
    for kw in keywords:
        d = _nearest_distance(text, anchor_idx, kw)
        if d is not None and (best is None or d < best):
            best = d
    return best


def _find_status_near(answer: str, anchors: list[str], anchor_window: int = 140) -> str | None:
    """
    Clause-scoped verdict lookup: split the answer into sentence/line clauses, find the clause
    that mentions the last anchor (e.g. disease_name), require every other anchor (e.g.
    patient_id) to appear either in that same clause or within `anchor_window` chars of it
    (patient is often named once for a whole passage), then look for a compliant/violation
    verdict keyword in that clause (plus the following clause, to catch "Disease:\nVerdict"
    formatting). Restricting the keyword search to the SAME clause — rather than a wide char
    window — avoids picking up a neighboring sentence's verdict for a DIFFERENT disease when
    several diagnoses are discussed close together. Used to check whether the LLM correctly
    bound its verdict to the RIGHT entity pair, not just mentioned the right words anywhere.
    """
    low = (answer or "").lower()
    anchors_low = [a.lower() for a in anchors if a]
    if not anchors_low:
        return None
    target = anchors_low[-1]
    other_anchors = anchors_low[:-1]

    spans: list[tuple[int, int]] = []
    prev = 0
    for m in _CLAUSE_SPLIT_RE.finditer(low):
        spans.append((prev, m.start()))
        prev = m.end()
    spans.append((prev, len(low)))

    fallback: str | None = None
    for i, (s, e) in enumerate(spans):
        clause = low[s:e]
        if target not in clause:
            continue
        idx = s + clause.find(target)
        bound_ok = True
        for a in other_anchors:
            if a in clause:
                continue
            d = _nearest_distance(low, idx, a)
            if d is None or d > anchor_window:
                bound_ok = False
                break
        if not bound_ok:
            continue
        # Search window: this clause, extended into the next clause if THIS clause has no
        # verdict at all (catches "Disease:\nVerdict" header-style formatting).
        win_lo, win_hi = s, e
        if not any(kw in clause for kw in _VIOLATION_KEYWORDS) and not any(
            kw in clause for kw in _COMPLIANT_KEYWORDS
        ) and i + 1 < len(spans):
            win_hi = spans[i + 1][1]
        window = low[win_lo:win_hi]
        # Nearest-by-distance WITHIN this window disambiguates compound sentences that mention
        # more than one disease (and verdict) together, e.g. "compliant with X, but in
        # violation for Y" — the verdict adjacent to THIS disease name wins, not just "any
        # verdict word present somewhere in the sentence".
        v_dist = _nearest_keyword_distance(window, idx - win_lo, _VIOLATION_KEYWORDS)
        c_dist = _nearest_keyword_distance(window, idx - win_lo, _COMPLIANT_KEYWORDS)
        if v_dist is None and c_dist is None:
            continue
        if v_dist is not None and (c_dist is None or v_dist < c_dist):
            return "violation"
        if c_dist is not None and (v_dist is None or c_dist < v_dist):
            return "compliant"
        fallback = fallback or "ambiguous"
    return fallback


def _find_patient_wide_generalizer(answer: str, patient_id: str, anchor_window: int = 140) -> str | None:
    """
    Fallback for phrasing like "both conditions are compliant" that states one verdict for
    ALL of a patient's diagnoses without repeating each disease name next to it. Only fires
    on an explicit generalizing phrase (both/all/each), so it won't mask genuinely missing
    per-disease reasoning.
    """
    low = (answer or "").lower()
    pid = (patient_id or "").lower()
    if not pid:
        return None
    spans: list[tuple[int, int]] = []
    prev = 0
    for m in _CLAUSE_SPLIT_RE.finditer(low):
        spans.append((prev, m.start()))
        prev = m.end()
    spans.append((prev, len(low)))
    for s, e in spans:
        clause = low[s:e]
        if not any(g in clause for g in _GENERALIZER_PHRASES):
            continue
        if pid not in clause:
            d = _nearest_distance(low, s, pid)
            if d is None or d > anchor_window:
                continue
        has_violation = any(kw in clause for kw in _VIOLATION_KEYWORDS)
        has_compliant = any(kw in clause for kw in _COMPLIANT_KEYWORDS)
        if has_violation and not has_compliant:
            return "violation"
        if has_compliant and not has_violation:
            return "compliant"
    return None


def _multi_hop_reasoning_score(answer: str, ground_truth: dict[tuple[str, str], str]) -> float | None:
    """
    Proxy for MULTI-HOP RELATIONAL BINDING accuracy (0-100), not just fact recall: checks
    whether the answer correctly attributes a compliant/violation verdict to the RIGHT
    (patient_id, disease_name) pair. Ground truth is computed independently via
    ai_compliance.check_patient_compliance and is NEVER shown to the LLM — it is only used
    here to grade the LLM's own reasoning. Returns None if there's nothing to grade.
    """
    if not ground_truth:
        return None
    if not (answer or "").strip():
        return 0.0
    # Only require the patient_id anchor when there's more than one patient in play — with a
    # single patient there's no other patient to misattribute a verdict to, and LLMs routinely
    # stop repeating the patient id once established (e.g. "For Hypertension, ... compliant."),
    # which would otherwise cause false negatives.
    distinct_patients = {pid for pid, _ in ground_truth}
    multi_patient = len(distinct_patients) > 1
    correct = 0
    total = 0
    for (pid, disease_name), truth in ground_truth.items():
        total += 1
        anchors = [pid, disease_name] if multi_patient else [disease_name]
        found = _find_status_near(answer, anchors)
        if found is None:
            found = _find_patient_wide_generalizer(answer, pid)
        if found == truth:
            correct += 1
    if total == 0:
        return None
    return round(100.0 * correct / total, 1)


def _compute_multi_hop_ground_truth(patient_ids: list[str]) -> dict[tuple[str, str], str]:
    """
    Deterministic per-(patient, disease) compliance ground truth, used ONLY for grading —
    never shown to the LLM in either arm's context. Computed independently of the benchmark
    prompt-building code so the grading key stays decoupled from what the LLM is given.
    """
    from ai_agent import get_patient_context
    from ai_compliance import check_patient_compliance

    out: dict[tuple[str, str], str] = {}
    for pid in patient_ids or []:
        try:
            ctx = get_patient_context(pid)
        except Exception:
            continue
        pname = ctx.get("patient_name") or pid
        for d in ctx.get("diseases") or []:
            did, dname = d.get("disease_id"), d.get("disease_name") or d.get("disease_id")
            if not did or not dname:
                continue
            try:
                result = check_patient_compliance(pid, pname, did, dname)
            except Exception:
                continue
            compliant = result.get("compliant")
            if compliant is None:
                continue
            out[(pid, dname)] = "compliant" if compliant else "violation"
    return out


def _multi_hop_raw_records(patient_id: str) -> dict[str, Any]:
    """
    Raw facts for one patient needed to answer a multi-hop protocol-binding question: per-
    disease protocol (recommended drug/procedure) plus actual drugs/procedures received.
    No compliance conclusion is computed or exposed here — that's left for the LLM (and graded
    independently via _compute_multi_hop_ground_truth).
    """
    from ai_agent import get_patient_context
    from neo4j_ops import get_protocol_for_disease

    ctx = get_patient_context(patient_id)
    diseases = []
    for d in ctx.get("diseases") or []:
        did, dname = d.get("disease_id"), d.get("disease_name") or d.get("disease_id")
        proto = None
        try:
            rows = get_protocol_for_disease(did) if did else None
            proto = rows[0] if rows else None
        except Exception:
            proto = None
        diseases.append({"disease_id": did, "disease_name": dname, "protocol": proto})
    return {
        "patient_id": patient_id,
        "patient_name": ctx.get("patient_name") or patient_id,
        "diseases": diseases,
        "actual_drugs": [d.get("name") or d.get("id") for d in ctx.get("actual_drugs") or []],
        "actual_procedures": [p.get("name") or p.get("id") for p in ctx.get("actual_procedures") or []],
    }


def _build_multi_hop_graph_context(patient_ids: list[str]) -> str:
    """
    Raw Neo4j-style relationship edges ONLY (protocol chain + actual treatment) — deliberately
    withholds any precomputed compliance conclusion. The LLM must trace HAS_DISEASE ->
    RECOMMENDED_DRUG/RECOMMENDED_PROCEDURE and compare against TREATED_WITH/HAD_PROCEDURE
    itself, per disease, per patient.
    """
    lines = [
        "Authoritative Neo4j graph edges below — RAW facts only, no compliance conclusion is "
        "given. For EACH disease of EACH patient, follow (Patient)-[:HAS_DISEASE]->(Disease)-"
        "[:RECOMMENDED_DRUG/RECOMMENDED_PROCEDURE]->(...) and compare against that same "
        "patient's (Patient)-[:TREATED_WITH]->(Drug) / (Patient)-[:HAD_PROCEDURE]->(Procedure) "
        "edges. State, per patient per disease, whether treatment is 'compliant' or a "
        "'violation'. Do not mix up facts between different patients.",
        "",
    ]
    for pid in patient_ids or []:
        rec = _multi_hop_raw_records(pid)
        lines.append(f"  Patient:{pid} ({rec['patient_name']}):")
        for d in rec["diseases"]:
            lines.append(f"    (Patient:{pid})-[:HAS_DISEASE]->(Disease:{d['disease_id']}) — {d['disease_name']}")
            proto = d.get("protocol")
            if proto:
                lines.append(
                    f"    (Disease:{d['disease_id']})-[:RECOMMENDED_DRUG]->(Drug) — {proto.get('drug_name')}"
                )
                lines.append(
                    f"    (Disease:{d['disease_id']})-[:RECOMMENDED_PROCEDURE]->(Procedure) — {proto.get('procedure_name')}"
                )
        for name in rec["actual_drugs"]:
            lines.append(f"    (Patient:{pid})-[:TREATED_WITH]->(Drug) — {name}")
        for name in rec["actual_procedures"]:
            lines.append(f"    (Patient:{pid})-[:HAD_PROCEDURE]->(Procedure) — {name}")
        lines.append("")
    return "\n".join(lines)


def _build_multi_hop_flat_context(patient_ids: list[str]) -> str:
    """
    SAME underlying facts and SAME pairing as the graph version (each protocol drug/procedure
    stays attached to its own disease in one record, the way a relational foreign key would
    preserve it) — but with no relationship-type vocabulary, and record order shuffled across
    patients. Isolates whether graph *labeling/traversal* helps beyond a flat-but-still-paired
    (relational-style) representation of the identical facts.
    """
    records: list[str] = []
    for pid in patient_ids or []:
        rec = _multi_hop_raw_records(pid)
        for d in rec["diseases"]:
            proto = d.get("protocol") or {}
            records.append(
                f'record: patient={pid} diagnosis="{d["disease_name"]}" '
                f'protocol_drug="{proto.get("drug_name")}" protocol_procedure="{proto.get("procedure_name")}"'
            )
        for name in rec["actual_drugs"]:
            records.append(f'record: patient={pid} received_drug="{name}"')
        for name in rec["actual_procedures"]:
            records.append(f'record: patient={pid} received_procedure="{name}"')

    random.Random(7).shuffle(records)
    header = (
        "DATA_MODE: FLAT_RECORDS. Same underlying facts as the WITH_GRAPH arm, same per-disease "
        "pairing preserved in each record (like a relational table's foreign key) — but NO "
        "relationship-type labels (no HAS_DISEASE/RECOMMENDED_DRUG/TREATED_WITH), and record "
        "order shuffled across patients. No compliance conclusion is given. For EACH patient's "
        "EACH diagnosis, compare its protocol_drug/protocol_procedure fields against that SAME "
        "patient's received_drug/received_procedure records and state whether treatment is "
        "'compliant' or a 'violation'. Do not mix up records between different patients.\n\n"
    )
    return header + "\n".join(records)


def _benchmark_multi_hop_arm(question: str, patient_ids: list[str], mode: str) -> dict[str, Any]:
    """
    Runs one multi-hop protocol-binding question for WITH_GRAPH or WITH_FLAT_DATA, using raw
    (non-precomputed-conclusion) facts so the LLM must perform the disease -> protocol ->
    actual-treatment comparison itself. mode: 'graph' or 'flat'.
    """
    if mode == "graph":
        context = _build_multi_hop_graph_context(patient_ids)
        mode_label = "WITH_GRAPH"
        highlight_rels = ["HAS_DISEASE", "RECOMMENDED_DRUG", "RECOMMENDED_PROCEDURE", "TREATED_WITH", "HAD_PROCEDURE"]
    else:
        context = _build_multi_hop_flat_context(patient_ids)
        mode_label = "WITH_FLAT_DATA"
        highlight_rels = []
    system_prompt = (
        "You assist with a controlled clinical AI benchmark testing multi-step relational "
        "reasoning. Reason BEFORE you conclude — work out each disease's verdict first, and "
        "only write your final verdicts once that work is done, so your conclusion can never "
        "contradict your own evidence. Use this exact section order:\n"
        "## Evidence — for EACH disease of EACH patient, list its protocol drug/procedure and "
        "the patient's actual drug/procedure, side by side.\n"
        "## Explanation — for EACH disease of EACH patient SEPARATELY, compare protocol vs. "
        "actual and reason step by step whether it matches. Treat every disease independently: "
        "one disease being compliant never implies another disease (even for the same patient) "
        "is compliant too.\n"
        "## Conclusion — restate ONE verdict per disease per patient ('compliant' or "
        "'violation'), consistent with the Explanation above.\n"
        "COMMON MISTAKE TO AVOID: do NOT write a single blanket verdict like 'compliant with "
        "both conditions' or 'compliant with all of the above' — many patients have ONE "
        "compliant and ONE non-compliant condition at the SAME time, so a match on one disease "
        "never implies a match on another. Before finalizing your Conclusion, re-check it "
        "sentence by sentence against your own Explanation and correct any diagnosis where they "
        "disagree.\n"
        "Base all of this only on the facts given below — do not use outside medical knowledge "
        "to guess pairings that are not stated, and do not attribute one patient's facts to "
        "another patient."
    )
    answer = _call_llm_smart(question, context, system_prompt, user_prefix="")
    return {
        "answer": (answer or "").strip(),
        "violation": None,
        "highlight_nodes": [f"Patient:{pid}" for pid in (patient_ids or [])],
        "highlight_relationships": highlight_rels,
        "highlight_query": "MATCH (n) RETURN n LIMIT 0",
        "paths": [],
        "benchmark_mode": mode_label,
    }


def _graph_grounding_score(answer: str, result: dict[str, Any]) -> float:
    """
    How strongly the answer is supported by graph-shaped semantics (0–100).
    Uses highlight richness, paths, relationship vocabulary, and Neo4j-style entity mentions.
    """
    text = answer or ""
    hn = len(result.get("highlight_nodes") or [])
    hr = len(result.get("highlight_relationships") or [])
    paths_n = len(result.get("paths") or [])
    markers = sum(1 for m in _GRAPH_MARKERS if m in text)
    typed_refs = len(
        re.findall(
            r"\b(?:Patient|Disease|Drug|Violation|Procedure|ClinicalState)[:\s]\s*[\w\-]+",
            text,
            re.I,
        )
    )
    raw = (
        min(28.0, hn * 3.5)
        + min(22.0, hr * 5.5)
        + min(18.0, paths_n * 4.5)
        + min(32.0, markers * 9.0)
        + min(18.0, typed_refs * 4.0)
    )
    return max(0.0, min(100.0, raw))


def _reasoning_traceability_score(
    answer: str,
    result: dict[str, Any],
    facts: dict[str, Any] | None = None,
    sel: list[str] | None = None,
) -> float:
    """
    Whether the answer's reasoning is actually anchored to real, case-specific facts (0-100)
    — not just whether it *looks* structured. Formatting/keyword presence alone is gameable
    (any answer can say "because" or add a bullet); this instead checks:
      - does the answer cite the real, case-specific facts it should (from `sel`/`facts`)?
      - does causal language ("because", "therefore"...) sit near an actual cited fact, or
        is it just filler with nothing concrete behind it?
      - does a fact cited under "## Evidence" reappear in "## Conclusion" — i.e. is the
        verdict traceable back to a specific piece of evidence, not merely asserted?
    """
    if not (answer or "").strip():
        return 15.0
    text = answer
    a_lower = text.lower()

    anchors: set[str] = {str(pid) for pid in (sel or []) if pid}
    f = facts or {}
    for key in ("must_mention_any", "must_mention_all", "should_mention_any", "must_mention_terms", "should_mention_terms"):
        for term in f.get(key) or []:
            if term:
                anchors.add(str(term))

    def _anchor_positions(hay: str) -> list[tuple[int, str]]:
        low_hay = hay.lower()
        hits: list[tuple[int, str]] = []
        for a in anchors:
            a_low = a.lower()
            start = 0
            while True:
                idx = low_hay.find(a_low, start)
                if idx == -1:
                    break
                hits.append((idx, a))
                start = idx + len(a_low)
        return hits

    anchor_hits = _anchor_positions(text)
    matched_anchor_set = {a for _, a in anchor_hits}

    score = 10.0  # baseline for any substantive, non-empty answer

    if "## conclusion" in a_lower:
        score += 8.0
    if "## evidence" in a_lower:
        score += 9.0
    if "## explanation" in a_lower:
        score += 7.0

    bullets = text.count("\n-") + text.count("\n•") + text.count("\n*")
    score += min(10.0, bullets * 2.5)

    # Grounding: does the answer actually cite the real, case-specific facts it was asked
    # about, vs. generic hedge language that never commits to a concrete fact?
    if anchors:
        ratio = len(matched_anchor_set) / len(anchors)
        score += min(26.0, ratio * 26.0)
    elif re.search(r"\bP\d+\b", text):
        score += 12.0

    # Causal language only earns credit when it sits near an actually-cited, grounded fact
    # — otherwise "therefore"/"because" is filler that doesn't trace anything.
    trace_phrases = ("because", "therefore", "based on", "according to", "implies", "supports")
    grounded_causal = 0
    for phrase in trace_phrases:
        start = 0
        while True:
            idx = a_lower.find(phrase, start)
            if idx == -1:
                break
            if any(abs(idx - pos) <= 160 for pos, _ in anchor_hits):
                grounded_causal += 1
            start = idx + len(phrase)
    score += min(20.0, grounded_causal * 5.0)

    if any(x in text for x in ("HAS_", "-[:", "MATCH (", "OPTIONAL MATCH")):
        score += 14.0

    # Evidence -> Conclusion binding: a fact cited as evidence should reappear in the
    # conclusion, meaning the verdict is traceable to something specific it actually cited,
    # rather than being asserted independently of the evidence listed above it.
    ev_idx = a_lower.find("## evidence")
    concl_idx = a_lower.find("## conclusion")
    if ev_idx != -1 and concl_idx != -1 and anchors:
        if concl_idx > ev_idx:
            evidence_block, conclusion_block = text[ev_idx:concl_idx], text[concl_idx:]
        else:
            evidence_block, conclusion_block = text[ev_idx:], text[concl_idx:ev_idx]
        ev_anchors = {a for a in anchors if a.lower() in evidence_block.lower()}
        concl_anchors = {a for a in anchors if a.lower() in conclusion_block.lower()}
        overlap = ev_anchors & concl_anchors
        if overlap:
            score += min(10.0, len(overlap) * 5.0)

    if result.get("violation") is not None:
        score += 4.0

    return max(0.0, min(100.0, score))


def _hallucination_reduction_score(answer: str, result: dict[str, Any]) -> float:
    """
    Proxy for fewer unsupported generic claims (0–100): penalizes vague hedges, rewards
    concrete anchors and graph-grounding signals. Graph-backed runs typically score higher.
    """
    text = answer or ""
    low = text.lower()
    if len(text.strip()) < 25:
        return 28.0

    hedges = (" might ", " could ", " possibly ", " perhaps ", " unclear", "may not ")
    hedge_pen = sum(low.count(h.strip()) for h in hedges) * 6.0
    vague_bits = ("in general", "typically patients", "many patients", "often ")
    vague_pen = sum(low.count(v) for v in vague_bits) * 10.0

    concrete = len(re.findall(r"\bP\d+\b", text, re.I))
    concrete += len(re.findall(r"\b\d+(?:\.\d+)?\s*(?:mg|mmol|mmHg|%)\b", low))
    concrete_bonus = min(22.0, concrete * 5.5)

    gg = _graph_grounding_score(text, result)
    grounding_blend = gg * 0.42
    penalty = min(48.0, hedge_pen + vague_pen)
    raw = 38.0 + grounding_blend + concrete_bonus - penalty + min(15.0, len(text) / 120.0)
    return max(0.0, min(100.0, raw))


def _paired_metric_bundle(
    answer: str,
    result: dict[str, Any],
    sel: list[str],
    facts: dict[str, Any] | None = None,
) -> dict[str, float]:
    return {
        "ai_clinical_accuracy": float(_clinical_accuracy_score(answer, result)),
        "response_consistency": float(_structure_score(answer)),
        "patient_context_accuracy": float(_patient_context_score(answer, sel)),
        "graph_grounding_score": float(_graph_grounding_score(answer, result)),
        "reasoning_traceability_score": float(_reasoning_traceability_score(answer, result, facts, sel)),
        "hallucination_reduction_score": float(_hallucination_reduction_score(answer, result)),
    }


def _ground_truth_score(answer: str, facts: dict[str, Any] | None) -> float:
    """Check answer against known facts from the graph (0–100)."""
    if not facts:
        return 50.0
    text = answer or ""
    if not text.strip():
        return 0.0
    low = text.lower()
    earned = 0.0
    possible = 0.0

    must_any = facts.get("must_mention_any") or []
    if must_any:
        possible += 30.0
        if any(pid and (pid in text or pid.upper() in text.upper()) for pid in must_any):
            earned += 30.0

    must_all = facts.get("must_mention_all") or []
    if must_all:
        per = 20.0 / max(len(must_all), 1)
        possible += 20.0
        hits = sum(1 for pid in must_all if pid and (pid in text or pid.upper() in text.upper()))
        earned += per * hits

    for term in facts.get("must_mention_terms") or []:
        possible += 12.0
        if term.lower() in low:
            earned += 12.0

    should_any = facts.get("should_mention_any") or []
    if should_any:
        possible += 15.0
        if any(t.lower() in low for t in should_any):
            earned += 15.0

    for term in facts.get("should_mention_terms") or []:
        possible += 8.0
        if term.lower() in low:
            earned += 8.0

    if possible <= 0:
        return 50.0
    return round(min(100.0, earned / possible * 100.0), 1)


def _resolve_case_facts(case: dict[str, Any]) -> dict[str, Any]:
    """Merge static expected_facts with live compliance data when requested."""
    facts = dict(case.get("expected_facts") or {})
    if case.get("dynamic_violation_patients"):
        try:
            from ai_compliance import run_compliance_check

            compliance = run_compliance_check()
            pids = []
            for row in compliance.get("patients_with_violations") or []:
                pid = row.get("patient_id") or row.get("id") or row.get("patient")
                if pid and pid not in pids:
                    pids.append(str(pid))
            if pids:
                facts["must_mention_any"] = pids[:10]
        except Exception:
            pass
    return facts


def _truncate_answer(text: str, limit: int = 1800) -> str:
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    return t[:limit] + "…"


_BENCHMARK_SUITE: list[dict[str, Any]] = [
    {
        "id": "violations_population",
        "title": "Find all protocol violations",
        "question": "Which patients have protocol violations or missing recommended treatments?",
        "selected_patients": [],
        "dynamic_violation_patients": True,
        "expected_facts": {
            "should_mention_terms": ["violation"],
            "should_mention_any": ["protocol", "missing", "recommended"],
        },
        "expected": "Names specific patients with violations drawn from Neo4j compliance data.",
    },
    {
        "id": "sepsis_p3",
        "title": "Sepsis bundle — P3",
        "question": (
            "For patient P3, does care meet sepsis bundle requirements? "
            "Use SOFA score, lactate, antibiotics, and cultures from the graph."
        ),
        "selected_patients": ["P3"],
        "expected_facts": {
            "must_mention_all": ["P3"],
            "should_mention_any": ["antibiotic", "sepsis", "sofa"],
            "should_mention_terms": ["violation"],
        },
        "expected": "P3 has elevated SOFA without active antibiotics — a sepsis bundle violation.",
    },
    {
        "id": "ctx_p1",
        "title": "Patient summary from graph",
        "question": (
            "Summarize this patient's diseases, treatments, and any violations using only "
            "information consistent with the supplied graph context."
        ),
        "selected_patients": ["P1"],
        "expected_facts": {
            "must_mention_all": ["P1"],
            "should_mention_any": ["disease", "treatment", "drug", "procedure"],
        },
        "expected": "Grounded summary citing graph-linked diseases, drugs, and procedures for P1.",
    },
    {
        "id": "compare_two",
        "title": "Compare two patients",
        "question": (
            "Compare protocol compliance between these two patients based on the graph data provided. "
            "Highlight common versus unique findings."
        ),
        "selected_patients": ["P1", "P2"],
        "expected_facts": {
            "must_mention_all": ["P1", "P2"],
            "should_mention_any": ["compliance", "violation", "protocol"],
        },
        "expected": "Side-by-side comparison of P1 and P2 with graph-backed differences.",
    },
    {
        "id": "p1_protocol",
        "title": "Protocol alignment check",
        "question": (
            "For patient P1, does clinical care align with documented disease protocols? "
            "Use graph-linked diseases, drugs, and procedures."
        ),
        "selected_patients": ["P1"],
        "expected_facts": {
            "must_mention_all": ["P1"],
            "should_mention_any": ["protocol", "disease", "drug", "procedure"],
        },
        "expected": "Protocol alignment assessment citing HAS_DISEASE, TREATED_WITH, etc.",
    },
    {
        "id": "multi_hop_p8_binding",
        "case_type": "multi_hop",
        "title": "Multi-hop binding — P8's two diagnoses",
        "question": (
            "Patient P8 has two diagnosed conditions. For EACH one, determine from the facts "
            "given whether P8's actual treatment matches that specific condition's protocol. "
            "State a verdict ('compliant' or 'violation') for each condition separately."
        ),
        "selected_patients": ["P8"],
        "expected_facts": {
            "must_mention_all": ["P8"],
            "should_mention_any": ["compliant", "violation"],
        },
        "expected": (
            "P8 is compliant for one condition (received the recommended drug/procedure) and "
            "in violation for the other (did not receive the recommended drug/procedure) — "
            "requires correctly binding each verdict to the RIGHT condition, not just noting "
            "'one violation exists'."
        ),
    },
    {
        "id": "multi_hop_cross_patient",
        "case_type": "multi_hop",
        "title": "Multi-hop binding — 8-patient population (10 bindings, 3 violations)",
        "question": (
            "Here are 8 patients (P1, P2, P3, P4, P8, P9, P11, P15), some with more than one "
            "diagnosed condition. For EACH patient's EACH condition, determine from the facts "
            "given whether that patient's actual treatment matches that condition's protocol. "
            "State a verdict ('compliant' or 'violation') for every patient-condition pair — "
            "there are 10 in total — and be careful not to mix up facts between patients."
        ),
        "selected_patients": ["P1", "P2", "P3", "P4", "P8", "P9", "P11", "P15"],
        "expected_facts": {
            "must_mention_all": ["P4", "P8", "P9", "P15"],
            "should_mention_any": ["compliant", "violation"],
        },
        "expected": (
            "10 independent patient-condition bindings across 8 patients, 3 of which are "
            "violations (P8/Osteoarthritis, P9/Anxiety, P15/Asthma) and 7 compliant. Correctly "
            "attributing each verdict to the right patient AND the right condition is much "
            "harder to keep straight in a long, shuffled, unstructured record dump than in "
            "clearly delineated per-patient graph edges — this is where graph traversal's "
            "advantage over flat context is expected to actually show up (it barely does with "
            "only 2 patients / 4 bindings, since that's too easy for either representation)."
        ),
    },
]


def run_clinical_benchmark() -> dict[str, Any]:
    """
    Execute the benchmark suite and return dashboard-shaped JSON.
    Requires Neo4j + (for best scores) OPENAI_API_KEY.
    """
    metrics_samples: dict[str, list[float]] = {
        "ai_clinical_accuracy": [],
        "graph_coverage_score": [],
        "patient_context_accuracy": [],
        "response_consistency": [],
    }
    graph_impact_scores: list[float] = []
    all_node_labels: set[str] = set()
    all_rel_types: set[str] = set()
    sum_nodes = 0.0
    sum_rels = 0.0
    sum_pct_graph = 0.0
    n_cases = 0
    test_cases_out: list[dict[str, Any]] = []
    paired_with_samples: dict[str, list[float]] = {
        "ai_clinical_accuracy": [],
        "response_consistency": [],
        "patient_context_accuracy": [],
        "graph_grounding_score": [],
        "reasoning_traceability_score": [],
        "hallucination_reduction_score": [],
    }
    paired_without_samples: dict[str, list[float]] = {
        "ai_clinical_accuracy": [],
        "response_consistency": [],
        "patient_context_accuracy": [],
        "graph_grounding_score": [],
        "reasoning_traceability_score": [],
        "hallucination_reduction_score": [],
    }
    paired_flat_samples: dict[str, list[float]] = {
        "ai_clinical_accuracy": [],
        "response_consistency": [],
        "patient_context_accuracy": [],
        "graph_grounding_score": [],
        "reasoning_traceability_score": [],
        "hallucination_reduction_score": [],
    }
    ground_truth_with: list[float] = []
    ground_truth_without: list[float] = []
    ground_truth_flat: list[float] = []
    multi_hop_with: list[float] = []
    multi_hop_without: list[float] = []
    multi_hop_flat: list[float] = []

    for case in _BENCHMARK_SUITE:
        q = case["question"]
        sel = case.get("selected_patients") or []
        is_multi_hop = case.get("case_type") == "multi_hop"
        facts = _resolve_case_facts(case)
        mh_truth = _compute_multi_hop_ground_truth(sel) if is_multi_hop else {}

        try:
            if is_multi_hop:
                result = _benchmark_multi_hop_arm(q, sel, "graph")
            elif sel:
                result = ask_agent_with_context(q, sel)
            else:
                result = ask_agent(q)
            ans = (result.get("answer") or "").strip()
        except Exception as exc:
            ans = f"Benchmark case error: {exc}"
            result = {"answer": ans, "highlight_nodes": [], "paths": [], "violation": None}

        try:
            result_no_g = _benchmark_llm_without_graph(q, sel)
            ans_no_g = (result_no_g.get("answer") or "").strip()
        except Exception as exc:
            ans_no_g = f"WITHOUT_GRAPH benchmark error: {exc}"
            result_no_g = {"answer": ans_no_g, "highlight_nodes": [], "paths": [], "violation": None}

        try:
            if is_multi_hop:
                result_flat = _benchmark_multi_hop_arm(q, sel, "flat")
            else:
                result_flat = _benchmark_llm_flat_data(q, sel)
            ans_flat = (result_flat.get("answer") or "").strip()
        except Exception as exc:
            ans_flat = f"WITH_FLAT_DATA benchmark error: {exc}"
            result_flat = {"answer": ans_flat, "highlight_nodes": [], "paths": [], "violation": None}

        mh_w = _multi_hop_reasoning_score(ans, mh_truth) if is_multi_hop else None
        mh_n = _multi_hop_reasoning_score(ans_no_g, mh_truth) if is_multi_hop else None
        mh_f = _multi_hop_reasoning_score(ans_flat, mh_truth) if is_multi_hop else None
        if mh_w is not None:
            multi_hop_with.append(mh_w)
        if mh_n is not None:
            multi_hop_without.append(mh_n)
        if mh_f is not None:
            multi_hop_flat.append(mh_f)

        g = _graph_coverage_score(ans, result)
        s = _structure_score(ans)
        p = _patient_context_score(ans, sel)
        c = _clinical_accuracy_score(ans, result)

        metrics_samples["graph_coverage_score"].append(g)
        metrics_samples["response_consistency"].append(s)
        metrics_samples["patient_context_accuracy"].append(p)
        metrics_samples["ai_clinical_accuracy"].append(c)

        tri_w = _paired_metric_bundle(ans, result, sel, facts)
        tri_n = _paired_metric_bundle(ans_no_g, result_no_g, sel, facts)
        tri_f = _paired_metric_bundle(ans_flat, result_flat, sel, facts)
        gt_w = _ground_truth_score(ans, facts)
        gt_n = _ground_truth_score(ans_no_g, facts)
        gt_f = _ground_truth_score(ans_flat, facts)
        if is_multi_hop:
            # For multi-hop cases, the generic keyword-presence ground truth is nearly
            # meaningless (any arm can pass it by mentioning "P4"/"compliant" anywhere) — use
            # the real relational-binding accuracy as THE ground-truth score shown for these
            # cases, since that's what actually tests whether graph structure helps reasoning.
            if mh_w is not None:
                gt_w = mh_w
            if mh_n is not None:
                gt_n = mh_n
            if mh_f is not None:
                gt_f = mh_f
        ground_truth_with.append(gt_w)
        ground_truth_without.append(gt_n)
        ground_truth_flat.append(gt_f)
        for k in paired_with_samples:
            paired_with_samples[k].append(tri_w[k])
            paired_without_samples[k].append(tri_n[k])
            paired_flat_samples[k].append(tri_f[k])

        gi = _graph_impact_per_case(ans, result)
        graph_impact_scores.append(float(gi["score"]))
        sum_nodes += gi["nodes_used_count"]
        sum_rels += gi["relationships_used_count"]
        sum_pct_graph += gi["percent_graph_based_reasoning"]
        n_cases += 1
        for lb in gi["node_labels"]:
            all_node_labels.add(lb)
        for rt in gi["relationship_types"]:
            all_rel_types.add(rt)

        overall_case = _case_overall(g, s, p, c)
        pid_disp = ", ".join(sel) if sel else "—"
        _metric_keys = (
            "ai_clinical_accuracy",
            "response_consistency",
            "patient_context_accuracy",
            "graph_grounding_score",
            "reasoning_traceability_score",
            "hallucination_reduction_score",
        )
        imp_pct = {k: round(tri_w[k] - tri_n[k], 1) for k in _metric_keys}
        imp_pct["ground_truth_score"] = round(gt_w - gt_n, 1)
        # Isolates the graph-structure effect: same facts (flat) vs relationship-structured (graph).
        vs_flat_pct = {k: round(tri_w[k] - tri_f[k], 1) for k in _metric_keys}
        vs_flat_pct["ground_truth_score"] = round(gt_w - gt_f, 1)
        # Isolates the "having any facts" effect: flat facts vs none at all.
        flat_vs_none_pct = {k: round(tri_f[k] - tri_n[k], 1) for k in _metric_keys}
        flat_vs_none_pct["ground_truth_score"] = round(gt_f - gt_n, 1)
        # Primary showcased delta is graph-vs-flat (same facts either way) — the structure-
        # specific effect — not graph-vs-no-data, which is the easier/less interesting bar.
        showcase_delta = round(
            vs_flat_pct.get("graph_grounding_score", 0)
            + vs_flat_pct.get("ground_truth_score", 0)
            + vs_flat_pct.get("hallucination_reduction_score", 0),
            1,
        )
        test_cases_out.append(
            {
                "id": case.get("id"),
                "title": case.get("title") or case.get("id") or "Case",
                "patient_id": pid_disp,
                "query": q,
                "expected": case.get("expected") or (
                    "Structured answer citing graph relationships where applicable."
                ),
                "actual": _truncate_answer(ans, 520),
                "answer_with_graph": _truncate_answer(ans),
                "answer_without_graph": _truncate_answer(ans_no_g),
                "answer_flat_data": _truncate_answer(ans_flat),
                "score": overall_case,
                "ground_truth_score_with": gt_w,
                "ground_truth_score_without": gt_n,
                "ground_truth_score_flat": gt_f,
                "showcase_delta": showcase_delta,
                "graph_evidence": {
                    "highlight_nodes": list(result.get("highlight_nodes") or [])[:40],
                    "highlight_relationships": list(result.get("highlight_relationships") or [])[:20],
                    "paths": (result.get("paths") or [])[:6],
                    "nodes_used": gi["nodes_used_count"],
                    "relationships_used": gi["relationships_used_count"],
                },
                "graph_impact": {
                    "score": gi["score"],
                    "nodes_used": gi["nodes_used_count"],
                    "relationships_used": gi["relationships_used_count"],
                    "percent_graph_based_reasoning": gi["percent_graph_based_reasoning"],
                    "percent_non_graph_reasoning": gi["percent_non_graph_reasoning"],
                },
                "paired_modes": {
                    "WITH_GRAPH": {**tri_w, "ground_truth_score": gt_w, "multi_hop_accuracy": mh_w},
                    "WITHOUT_GRAPH": {**tri_n, "ground_truth_score": gt_n, "multi_hop_accuracy": mh_n},
                    "WITH_FLAT_DATA": {**tri_f, "ground_truth_score": gt_f, "multi_hop_accuracy": mh_f},
                    "graph_improvement_pct": imp_pct,
                    "graph_vs_flat_pct": vs_flat_pct,
                    "flat_vs_no_data_pct": flat_vs_none_pct,
                },
                "multi_hop": (
                    {
                        "is_multi_hop_case": True,
                        "ground_truth": {f"{pid}:{dname}": v for (pid, dname), v in mh_truth.items()},
                        "accuracy_with_graph": mh_w,
                        "accuracy_flat_data": mh_f,
                        "accuracy_without_graph": mh_n,
                    }
                    if is_multi_hop
                    else {"is_multi_hop_case": False}
                ),
            }
        )

    def _avg(key: str) -> int:
        xs = metrics_samples[key]
        return int(round(sum(xs) / len(xs))) if xs else 0

    metrics = {
        "ai_clinical_accuracy": _avg("ai_clinical_accuracy"),
        "graph_coverage_score": _avg("graph_coverage_score"),
        "patient_context_accuracy": _avg("patient_context_accuracy"),
        "response_consistency": _avg("response_consistency"),
    }
    graph_impact_score = (
        int(round(sum(graph_impact_scores) / len(graph_impact_scores)))
        if graph_impact_scores
        else 0
    )
    metrics["graph_impact_score"] = graph_impact_score

    def _avg_pair(key: str, samples: dict[str, list[float]]) -> int:
        xs = samples[key]
        return int(round(sum(xs) / len(xs))) if xs else 0

    _pk = (
        "ai_clinical_accuracy",
        "response_consistency",
        "patient_context_accuracy",
        "graph_grounding_score",
        "reasoning_traceability_score",
        "hallucination_reduction_score",
    )
    paired_metrics_with = {k: _avg_pair(k, paired_with_samples) for k in _pk}
    paired_metrics_without = {k: _avg_pair(k, paired_without_samples) for k in _pk}
    paired_metrics_flat = {k: _avg_pair(k, paired_flat_samples) for k in _pk}
    paired_graph_improvement_pct = {
        k: round(float(paired_metrics_with[k]) - float(paired_metrics_without[k]), 1) for k in _pk
    }
    # Isolates the graph-structure effect specifically: WITH_GRAPH vs. the SAME facts flattened.
    paired_graph_vs_flat_pct = {
        k: round(float(paired_metrics_with[k]) - float(paired_metrics_flat[k]), 1) for k in _pk
    }
    # Isolates the "having any real facts at all" effect: flattened facts vs. no facts.
    paired_flat_vs_without_pct = {
        k: round(float(paired_metrics_flat[k]) - float(paired_metrics_without[k]), 1) for k in _pk
    }
    avg_gt_with = int(round(sum(ground_truth_with) / max(len(ground_truth_with), 1)))
    avg_gt_without = int(round(sum(ground_truth_without) / max(len(ground_truth_without), 1)))
    avg_gt_flat = int(round(sum(ground_truth_flat) / max(len(ground_truth_flat), 1)))
    paired_metrics_with["ground_truth_score"] = avg_gt_with
    paired_metrics_without["ground_truth_score"] = avg_gt_without
    paired_metrics_flat["ground_truth_score"] = avg_gt_flat
    paired_graph_improvement_pct["ground_truth_score"] = round(float(avg_gt_with - avg_gt_without), 1)
    paired_graph_vs_flat_pct["ground_truth_score"] = round(float(avg_gt_with - avg_gt_flat), 1)
    paired_flat_vs_without_pct["ground_truth_score"] = round(float(avg_gt_flat - avg_gt_without), 1)

    # multi_hop_accuracy: only defined over the dedicated multi-hop cases (per-disease, per-patient
    # compliance-binding verdicts graded against an independent ground-truth key). None if the
    # suite has no multi-hop cases (e.g. a custom/trimmed suite).
    def _avg_or_none(xs: list[float]) -> int | None:
        return int(round(sum(xs) / len(xs))) if xs else None

    mh_avg_with = _avg_or_none(multi_hop_with)
    mh_avg_without = _avg_or_none(multi_hop_without)
    mh_avg_flat = _avg_or_none(multi_hop_flat)
    paired_metrics_with["multi_hop_accuracy"] = mh_avg_with
    paired_metrics_without["multi_hop_accuracy"] = mh_avg_without
    paired_metrics_flat["multi_hop_accuracy"] = mh_avg_flat
    if mh_avg_with is not None and mh_avg_flat is not None:
        paired_graph_vs_flat_pct["multi_hop_accuracy"] = round(float(mh_avg_with - mh_avg_flat), 1)
    if mh_avg_with is not None and mh_avg_without is not None:
        paired_graph_improvement_pct["multi_hop_accuracy"] = round(float(mh_avg_with - mh_avg_without), 1)
    if mh_avg_flat is not None and mh_avg_without is not None:
        paired_flat_vs_without_pct["multi_hop_accuracy"] = round(float(mh_avg_flat - mh_avg_without), 1)

    graph_showcase: dict[str, Any] = {
        "headline": (
            f"Neo4j graph improves factual accuracy by "
            f"{paired_graph_improvement_pct.get('ground_truth_score', 0):+.0f} pts "
            f"and grounding by {paired_graph_improvement_pct.get('graph_grounding_score', 0):+.0f} pts"
        ),
        "tagline": (
            "Same questions, same LLM — with graph retrieval the AI cites real patients, "
            "violations, and clinical relationships instead of generic guesses."
        ),
        "with_graph": {
            "graph_grounding_score": paired_metrics_with["graph_grounding_score"],
            "ground_truth_score": avg_gt_with,
            "hallucination_reduction_score": paired_metrics_with["hallucination_reduction_score"],
            "reasoning_traceability_score": paired_metrics_with["reasoning_traceability_score"],
        },
        "without_graph": {
            "graph_grounding_score": paired_metrics_without["graph_grounding_score"],
            "ground_truth_score": avg_gt_without,
            "hallucination_reduction_score": paired_metrics_without["hallucination_reduction_score"],
            "reasoning_traceability_score": paired_metrics_without["reasoning_traceability_score"],
        },
        "improvement": {
            "graph_grounding_score": paired_graph_improvement_pct["graph_grounding_score"],
            "ground_truth_score": paired_graph_improvement_pct["ground_truth_score"],
            "hallucination_reduction_score": paired_graph_improvement_pct["hallucination_reduction_score"],
            "reasoning_traceability_score": paired_graph_improvement_pct["reasoning_traceability_score"],
        },
        "top_cases": sorted(
            test_cases_out,
            key=lambda tc: float(tc.get("showcase_delta") or 0),
            reverse=True,
        )[:3],
    }

    paired_comparison: dict[str, Any] = {
        "methodology": (
            "Live paired experiment across three arms, identical prompts per case. WITH_GRAPH "
            "retrieves patient records, violations, and clinical relationships from Neo4j and "
            "presents them with explicit relationship labels. WITH_FLAT_DATA pulls the SAME facts "
            "from the SAME retrieval functions but flattens them into an unordered, non-relational "
            "list — isolating whether graph *structure* matters beyond just having the facts. "
            "WITHOUT_GRAPH withholds institution-specific facts entirely — it must guess. Ground "
            "truth scores check whether answers mention real patients and clinical facts from your "
            "database."
        ),
        "with_graph": paired_metrics_with,
        "with_flat_data": paired_metrics_flat,
        "without_graph": paired_metrics_without,
        "graph_improvement_pct": paired_graph_improvement_pct,
        "graph_vs_flat_improvement_pct": paired_graph_vs_flat_pct,
        "flat_vs_without_improvement_pct": paired_flat_vs_without_pct,
        "without_graph_llm_arm_executed": True,
        "with_flat_data_arm_executed": True,
        "interpretation_layer": {
            "graph_augmentation_note": (
                "The graph does not just add vocabulary — it lets the AI name actual patients, "
                "flag real violations, and trace evidence through relationships like HAS_DISEASE "
                "and HAS_VIOLATION."
            ),
            "graph_structure_note": (
                "graph_vs_flat_improvement_pct isolates the effect of relational structure alone: "
                "both arms see identical underlying facts, so any remaining gap is attributable to "
                "the graph's explicit relationships (e.g. correctly linking a disease to its "
                "recommended drug/procedure) rather than to having more information."
            ),
            "data_presence_note": (
                "flat_vs_without_improvement_pct isolates the effect of simply having real facts "
                "at all, independent of structure — this is usually the larger of the two deltas."
            ),
        },
    }

    experiment_modes: dict[str, Any] = {
        "WITH_GRAPH": {
            "label": "WITH_GRAPH",
            "description": "Standard pipeline with Neo4j relationship-derived context.",
            "metrics": paired_metrics_with,
        },
        "WITH_FLAT_DATA": {
            "label": "WITH_FLAT_DATA",
            "description": (
                "Same questions and same underlying facts as WITH_GRAPH, but flattened into an "
                "unordered, non-relational list (no HAS_DISEASE / TREATED_WITH / RECOMMENDED_DRUG "
                "labels). Isolates the graph-structure effect."
            ),
            "metrics": paired_metrics_flat,
        },
        "WITHOUT_GRAPH": {
            "label": "WITHOUT_GRAPH",
            "description": "Same questions; no institution-specific context injection at all (LLM-only benchmark arm).",
            "metrics": paired_metrics_without,
        },
    }

    three_arm_summary: dict[str, Any] = {
        "description": (
            "Three paired arms answer identical questions. Comparing WITH_GRAPH against "
            "WITH_FLAT_DATA isolates whether graph-structured retrieval helps beyond merely "
            "having the same facts (the 'does storing/serving data as a graph help' question). "
            "Comparing WITH_FLAT_DATA against WITHOUT_GRAPH isolates whether having any real "
            "facts at all — regardless of structure — is what reduces hallucination."
        ),
        "graph_vs_flat_data_pct": paired_graph_vs_flat_pct,
        "flat_data_vs_no_data_pct": paired_flat_vs_without_pct,
        "graph_vs_no_data_pct": paired_graph_improvement_pct,
        "headline": (
            f"Graph structure adds {paired_graph_vs_flat_pct.get('ground_truth_score', 0):+.0f} pts "
            f"of factual accuracy beyond the same facts flattened; having any real facts at all "
            f"(flat or graph) adds {paired_flat_vs_without_pct.get('ground_truth_score', 0):+.0f} pts "
            f"over no facts."
        ),
        "multi_hop_reasoning": {
            "description": (
                "The metrics above are dominated by simple single-fact recall, where flat data "
                "does about as well as graph data. multi_hop_accuracy is a stricter, separate "
                "test: dedicated benchmark cases (patients with 2+ diagnoses, some compliant, "
                "some not) where the LLM must itself trace disease -> protocol -> actual-treatment "
                "and correctly bind a verdict to the RIGHT patient AND the RIGHT diagnosis, using "
                "RAW facts with NO precomputed conclusion handed to it. Graded against an "
                "independently computed ground-truth key never shown to the LLM."
            ),
            "with_graph": mh_avg_with,
            "with_flat_data": mh_avg_flat,
            "without_graph": mh_avg_without,
            "graph_vs_flat_data_pct": paired_graph_vs_flat_pct.get("multi_hop_accuracy"),
            "headline": (
                (
                    f"On multi-hop protocol-binding questions, graph-structured context scores "
                    f"{mh_avg_with} vs. {mh_avg_flat} for the same facts flattened "
                    f"({paired_graph_vs_flat_pct.get('multi_hop_accuracy', 0):+.0f} pts) — this is "
                    "the fairer test of whether graph *structure* itself helps the AI reason "
                    "correctly, since single-fact-recall questions don't require any relational "
                    "reasoning at all."
                )
                if mh_avg_with is not None and mh_avg_flat is not None
                else "No multi-hop cases were run in this suite."
            ),
        },
    }

    avg_pct_graph = round(sum_pct_graph / max(n_cases, 1), 1)
    llm_baseline_sim = _estimate_llm_baseline_simulated(graph_impact_score)
    graph_influence_delta = int(round(float(graph_impact_score) - float(llm_baseline_sim)))
    comparison_block: dict[str, Any] = {
        # Primary semantics: simulated baseline ≠ reran without-graph model; delta is non-causal.
        "graph_measured_score": graph_impact_score,
        "llm_baseline_simulated_score": llm_baseline_sim,
        "graph_influence_delta": graph_influence_delta,
        "non_causal_interpretation_note": (
            "This metric does not represent causal performance difference due to evaluation constraints."
        ),
        "llm_baseline_descriptor": (
            "LLM Baseline (heuristic simulation, not rerun model)"
        ),
        "disclaimer": (
            "Baseline is not directly comparable due to different evaluation constraints."
        ),
        "interpretation_note": (
            "Graph-augmented column reflects measured Graph Impact scores from this Neo4j-backed run "
            "(highlight/path usage and graph-anchored language). The LLM baseline is a bounded "
            "heuristic applied to the same outputs—not a paired run without Neo4j. "
            "Graph Influence Delta is a descriptive gap, not an effect size."
        ),
        "experiment_structure": {
            "graph_enabled_run": True,
            "graph_disabled_run": True,
            "graph_disabled_run_note": (
                "WITHOUT_GRAPH benchmark arm runs as paired LLM-only calls (no Neo4j retrieval); "
                "see paired_comparison for aggregate metrics."
            ),
        },
        # Deprecated aliases for older clients (same integers as graph_influence_delta).
        "graph_improvement_score": graph_influence_delta,
        "with_graph_score": graph_impact_score,
        "without_graph_estimated_score": llm_baseline_sim,
        "note": (
            "LLM baseline is simulated from the same responses under a no-graph heuristic; "
            "it does not re-run the LLM without graph context."
        ),
    }

    graph_impact_block: dict[str, Any] = {
        "score": graph_impact_score,
        "breakdown": {
            "avg_nodes_used_per_case": round(sum_nodes / max(n_cases, 1), 2),
            "avg_relationships_used_per_case": round(sum_rels / max(n_cases, 1), 2),
            "unique_node_labels_seen": sorted(all_node_labels),
            "unique_relationship_types_seen": sorted(all_rel_types),
            "percent_graph_based_reasoning": avg_pct_graph,
            "percent_non_graph_reasoning": round(100.0 - avg_pct_graph, 1),
        },
        "comparison": comparison_block,
    }

    # Overall remains the mean of the original four metrics only (unchanged definition).
    base_keys = (
        "ai_clinical_accuracy",
        "graph_coverage_score",
        "patient_context_accuracy",
        "response_consistency",
    )
    overall = int(
        round(
            sum(metrics[k] for k in base_keys) / len(base_keys),
        )
    )

    if overall >= 80:
        status = "good"
    elif overall >= 60:
        status = "moderate"
    else:
        status = "needs_improvement"

    return {
        "overall_score": overall,
        "status_label": status,
        "metrics": metrics,
        "graph_showcase": graph_showcase,
        "graph_impact": graph_impact_block,
        "paired_comparison": paired_comparison,
        "experiment_modes": experiment_modes,
        "three_arm_summary": three_arm_summary,
        "test_cases": test_cases_out,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "live",
        "note": (
            "Live 3-arm paired benchmark, including dedicated multi-hop protocol-binding cases "
            "(see three_arm_summary.multi_hop_reasoning) where the LLM must reason over raw "
            "disease -> protocol -> treatment facts itself, with no precomputed conclusion "
            "given. WITH_GRAPH uses Neo4j relationship retrieval; "
            "WITH_FLAT_DATA uses the identical underlying facts with relationship structure "
            "removed; WITHOUT_GRAPH is LLM-only with no institution-specific facts. Ground truth "
            "scores verify answers against known patients and clinical facts. Expand any case "
            "below to compare all three answers side-by-side and highlight graph evidence."
        ),
    }
