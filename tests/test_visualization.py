"""
Unit tests for visualization.py — pure helpers that don't require Neo4j or pyvis.
"""
from __future__ import annotations

from visualization import _build_violation_set


def test_empty_compliance_result_yields_no_violation_edges():
    edges, tooltips = _build_violation_set({"patients_with_violations": []})

    assert edges == set()
    assert tooltips == {}


def test_violation_always_marks_the_has_disease_edge():
    compliance = {
        "patients_with_violations": [
            {
                "patient_id": "P2",
                "disease_id": "D1",
                "recommended_drug_id": "DRUG1",
                "recommended_drug_name": "Lisinopril",
                "recommended_procedure_id": "PROC1",
                "recommended_procedure_name": "BP monitoring",
                "actual_drug_ids": [],
                "actual_drug_names": [],
                "actual_procedure_ids": [],
                "actual_procedure_names": [],
            }
        ]
    }

    edges, tooltips = _build_violation_set(compliance)

    assert ("P2", "D1", "HAS_DISEASE") in edges
    assert "Lisinopril" in tooltips[("P2", "D1", "HAS_DISEASE")]


def test_wrong_drug_actually_taken_is_marked_as_violation_edge():
    compliance = {
        "patients_with_violations": [
            {
                "patient_id": "P2",
                "disease_id": "D1",
                "recommended_drug_id": "DRUG1",
                "recommended_drug_name": "Lisinopril",
                "recommended_procedure_id": "PROC1",
                "recommended_procedure_name": "BP monitoring",
                "actual_drug_ids": ["DRUG2"],
                "actual_drug_names": ["Metformin"],
                "actual_procedure_ids": ["PROC1"],
                "actual_procedure_names": ["BP monitoring"],
            }
        ]
    }

    edges, tooltips = _build_violation_set(compliance)

    assert ("P2", "DRUG2", "TREATED_WITH") in edges
    # Procedure matches the recommendation, so it should NOT be flagged.
    assert ("P2", "PROC1", "HAD_PROCEDURE") not in edges


def test_matching_drug_is_not_flagged_as_a_treated_with_violation():
    compliance = {
        "patients_with_violations": [
            {
                "patient_id": "P1",
                "disease_id": "D1",
                "recommended_drug_id": "DRUG1",
                "recommended_drug_name": "Lisinopril",
                "recommended_procedure_id": "PROC1",
                "recommended_procedure_name": "BP monitoring",
                "actual_drug_ids": ["DRUG1"],
                "actual_drug_names": ["Lisinopril"],
                "actual_procedure_ids": [],
                "actual_procedure_names": [],
            }
        ]
    }

    edges, _ = _build_violation_set(compliance)

    assert ("P1", "DRUG1", "TREATED_WITH") not in edges
    # Missing procedure entirely still shouldn't add a TREATED_WITH/HAD_PROCEDURE
    # edge since there is no actual procedure id to attach it to.
    assert not any(rel == "HAD_PROCEDURE" for _, _, rel in edges)


def test_multiple_patients_produce_independent_edges():
    compliance = {
        "patients_with_violations": [
            {
                "patient_id": "P2",
                "disease_id": "D1",
                "recommended_drug_id": "DRUG1",
                "recommended_procedure_id": "PROC1",
                "actual_drug_ids": ["DRUG2"],
                "actual_procedure_ids": ["PROC2"],
            },
            {
                "patient_id": "P3",
                "disease_id": "D2",
                "recommended_drug_id": "DRUG3",
                "recommended_procedure_id": "PROC3",
                "actual_drug_ids": [],
                "actual_procedure_ids": [],
            },
        ]
    }

    edges, _ = _build_violation_set(compliance)

    assert ("P2", "D1", "HAS_DISEASE") in edges
    assert ("P3", "D2", "HAS_DISEASE") in edges
    assert ("P2", "DRUG2", "TREATED_WITH") in edges
    assert ("P2", "PROC2", "HAD_PROCEDURE") in edges
