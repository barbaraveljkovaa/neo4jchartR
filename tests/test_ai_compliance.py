"""
Unit tests for ai_compliance.py — protocol-vs-actual-treatment compliance checking.

All Neo4j reads (get_protocol_for_disease, get_actual_patient_treatments,
get_patients_with_diseases, get_patients_with_doctor) are monkeypatched so
these tests run without a live database.
"""
from __future__ import annotations

import ai_compliance


PROTOCOL_D1 = [{
    "disease_id": "D1",
    "disease_name": "Hypertension",
    "drug_id": "DRUG1",
    "drug_name": "Lisinopril",
    "procedure_id": "PROC1",
    "procedure_name": "BP monitoring",
    "followup_id": "FU1",
    "followup_name": "3-month BP check",
}]


def _actual(drug_ids=None, drug_names=None, proc_ids=None, proc_names=None):
    return {
        "actual_drug_ids": drug_ids or [],
        "actual_drug_names": drug_names or [],
        "actual_procedure_ids": proc_ids or [],
        "actual_procedure_names": proc_names or [],
    }


class TestCheckPatientCompliance:
    def test_no_protocol_defined_returns_none_compliant(self, monkeypatch):
        monkeypatch.setattr(ai_compliance, "get_protocol_for_disease", lambda did: [])

        result = ai_compliance.check_patient_compliance("P1", "Alice", "D99", "Unknown Disease")

        assert result["compliant"] is None
        assert result["violations"] == ["No protocol defined for this disease"]
        assert result["recommended_drug_id"] is None

    def test_fully_compliant_patient_has_no_violations(self, monkeypatch):
        monkeypatch.setattr(ai_compliance, "get_protocol_for_disease", lambda did: PROTOCOL_D1)
        monkeypatch.setattr(
            ai_compliance,
            "get_actual_patient_treatments",
            lambda pid, did: _actual(["DRUG1"], ["Lisinopril"], ["PROC1"], ["BP monitoring"]),
        )

        result = ai_compliance.check_patient_compliance("P1", "Alice", "D1", "Hypertension")

        assert result["compliant"] is True
        assert result["violations"] == []

    def test_missing_drug_entirely_is_critical(self, monkeypatch):
        monkeypatch.setattr(ai_compliance, "get_protocol_for_disease", lambda did: PROTOCOL_D1)
        monkeypatch.setattr(
            ai_compliance,
            "get_actual_patient_treatments",
            lambda pid, did: _actual(proc_ids=["PROC1"], proc_names=["BP monitoring"]),
        )

        result = ai_compliance.check_patient_compliance("P1", "Alice", "D1", "Hypertension")

        assert result["compliant"] is False
        assert len(result["violations"]) == 1
        assert "Wrong or missing drug" in result["violations"][0]
        assert result["violations_structured"][0]["severity"] == "critical"

    def test_wrong_drug_present_is_warning_not_critical(self, monkeypatch):
        monkeypatch.setattr(ai_compliance, "get_protocol_for_disease", lambda did: PROTOCOL_D1)
        monkeypatch.setattr(
            ai_compliance,
            "get_actual_patient_treatments",
            lambda pid, did: _actual(["DRUG2"], ["Metformin"], ["PROC1"], ["BP monitoring"]),
        )

        result = ai_compliance.check_patient_compliance("P1", "Alice", "D1", "Hypertension")

        assert result["compliant"] is False
        assert result["violations_structured"][0]["severity"] == "warning"
        assert result["actual_drug_names"] == ["Metformin"]

    def test_missing_procedure_flagged_independently_of_drug(self, monkeypatch):
        monkeypatch.setattr(ai_compliance, "get_protocol_for_disease", lambda did: PROTOCOL_D1)
        monkeypatch.setattr(
            ai_compliance,
            "get_actual_patient_treatments",
            lambda pid, did: _actual(["DRUG1"], ["Lisinopril"]),
        )

        result = ai_compliance.check_patient_compliance("P1", "Alice", "D1", "Hypertension")

        assert result["compliant"] is False
        assert len(result["violations"]) == 1
        assert "Wrong or missing procedure" in result["violations"][0]

    def test_both_drug_and_procedure_wrong_yield_two_violations(self, monkeypatch):
        monkeypatch.setattr(ai_compliance, "get_protocol_for_disease", lambda did: PROTOCOL_D1)
        monkeypatch.setattr(ai_compliance, "get_actual_patient_treatments", lambda pid, did: _actual())

        result = ai_compliance.check_patient_compliance("P1", "Alice", "D1", "Hypertension")

        assert result["compliant"] is False
        assert len(result["violations"]) == 2


class TestRunComplianceCheck:
    def _setup(self, monkeypatch):
        # Two patients, each with one disease: P1 compliant, P2 in violation.
        monkeypatch.setattr(
            ai_compliance,
            "get_patients_with_diseases",
            lambda: [
                {"patient_id": "P1", "patient_name": "Alice", "disease_id": "D1", "disease_name": "Hypertension"},
                {"patient_id": "P2", "patient_name": "Bob", "disease_id": "D1", "disease_name": "Hypertension"},
                # duplicate row (as OPTIONAL MATCH can produce) must be deduplicated
                {"patient_id": "P1", "patient_name": "Alice", "disease_id": "D1", "disease_name": "Hypertension"},
            ],
        )
        monkeypatch.setattr(ai_compliance, "get_protocol_for_disease", lambda did: PROTOCOL_D1)

        def fake_actual(pid, did):
            if pid == "P1":
                return _actual(["DRUG1"], ["Lisinopril"], ["PROC1"], ["BP monitoring"])
            return _actual()  # P2: nothing given -> violation

        monkeypatch.setattr(ai_compliance, "get_actual_patient_treatments", fake_actual)
        monkeypatch.setattr(
            ai_compliance,
            "get_patients_with_doctor",
            lambda: [
                {"patient_id": "P1", "patient_name": "Alice", "doctor_id": "DOC1", "doctor_name": "Dr. Evans"},
                {"patient_id": "P2", "patient_name": "Bob", "doctor_id": "DOC1", "doctor_name": "Dr. Evans"},
            ],
        )

    def test_deduplicates_patient_disease_pairs(self, monkeypatch):
        self._setup(monkeypatch)

        result = ai_compliance.run_compliance_check()

        assert len(result["all_checks"]) == 2

    def test_identifies_only_the_violating_patient(self, monkeypatch):
        self._setup(monkeypatch)

        result = ai_compliance.run_compliance_check()

        violating_ids = {r["patient_id"] for r in result["patients_with_violations"]}
        assert violating_ids == {"P2"}

    def test_doctor_compliance_score_reflects_mixed_outcomes(self, monkeypatch):
        self._setup(monkeypatch)

        result = ai_compliance.run_compliance_check()

        scores = {s["doctor_id"]: s for s in result["doctor_compliance_scores"]}
        assert scores["DOC1"]["total_cases"] == 2
        assert scores["DOC1"]["compliant_cases"] == 1
        assert scores["DOC1"]["compliance_score"] == 50.0

    def test_violated_relationships_carry_recommended_and_actual_data(self, monkeypatch):
        self._setup(monkeypatch)

        result = ai_compliance.run_compliance_check()

        rels = result["violated_relationships"]
        assert len(rels) == 2  # P2 violates both drug and procedure
        assert all(r["patient_id"] == "P2" for r in rels)
        assert all(r["recommended_drug"] == "Lisinopril" for r in rels)
