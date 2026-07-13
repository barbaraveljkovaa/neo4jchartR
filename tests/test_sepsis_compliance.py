"""
Unit tests for sepsis_compliance.py — sepsis-bundle guideline evaluation.

get_patient_clinical_state, get_sepsis_guidelines and run_query are
monkeypatched so these tests run without a live database.
"""
from __future__ import annotations

import sepsis_compliance


GUIDELINE = [{
    "guideline_id": "SEPSIS_1",
    "guideline_name": "Sepsis Bundle",
    "sofa_threshold_high": 2,
    "lactate_threshold_mmol": 2,
    "map_threshold_mmhg": 65,
    "action_ids": ["ACT_ABX", "ACT_CULTURES"],
}]


def _state(sofa=0, lactate=1.0, map_=70, abx=True, cultures=True, vaso=True, state_id="CS_P1"):
    return {
        "state_id": state_id,
        "sofa_score": sofa,
        "lactate": lactate,
        "map": map_,
        "antibiotics_active": abx,
        "cultures_ordered": cultures,
        "vasopressors_active": vaso,
    }


def _patch_common(monkeypatch, state, guidelines=GUIDELINE, patient_name="Alice Smith"):
    monkeypatch.setattr(sepsis_compliance, "get_patient_clinical_state", lambda pid: state)
    monkeypatch.setattr(sepsis_compliance, "get_sepsis_guidelines", lambda: guidelines)
    monkeypatch.setattr(
        sepsis_compliance, "run_query", lambda cypher, params=None: [{"name": patient_name}]
    )


class TestRunSepsisGuidelines:
    def test_no_clinical_state_returns_non_compliant(self, monkeypatch):
        monkeypatch.setattr(sepsis_compliance, "get_patient_clinical_state", lambda pid: None)

        result = sepsis_compliance.run_sepsis_guidelines("P1")

        assert result["compliance"] is False
        assert result["violations"] == ["No clinical state found for this patient."]

    def test_no_guideline_returns_non_compliant(self, monkeypatch):
        monkeypatch.setattr(sepsis_compliance, "get_patient_clinical_state", lambda pid: _state())
        monkeypatch.setattr(sepsis_compliance, "get_sepsis_guidelines", lambda: [])

        result = sepsis_compliance.run_sepsis_guidelines("P1")

        assert result["compliance"] is False
        assert result["violations"] == ["No sepsis guideline found in graph."]

    def test_fully_compliant_patient_has_no_violations(self, monkeypatch):
        _patch_common(monkeypatch, _state(sofa=1, lactate=1.0, map_=70, abx=True, cultures=True, vaso=True))

        result = sepsis_compliance.run_sepsis_guidelines("P1")

        assert result["compliance"] is True
        assert result["violations"] == []
        assert result["patient_name"] == "Alice Smith"

    def test_high_sofa_without_antibiotics_is_critical_violation(self, monkeypatch):
        _patch_common(monkeypatch, _state(sofa=3, abx=False))

        result = sepsis_compliance.run_sepsis_guidelines("P1")

        assert result["compliance"] is False
        assert any("antibiotics not active" in v for v in result["violations"])
        crit = [v for v in result["violations_structured"] if "antibiotics not active" in v["text"]]
        assert crit[0]["severity"] == "critical"

    def test_high_sofa_with_antibiotics_but_no_cultures_is_warning(self, monkeypatch):
        _patch_common(monkeypatch, _state(sofa=3, abx=True, cultures=False))

        result = sepsis_compliance.run_sepsis_guidelines("P1")

        assert result["compliance"] is False
        warn = [v for v in result["violations_structured"] if "Blood cultures" in v["text"]]
        assert len(warn) == 1
        assert warn[0]["severity"] == "warning"

    def test_elevated_lactate_without_antibiotics_is_flagged(self, monkeypatch):
        _patch_common(monkeypatch, _state(sofa=0, lactate=3.0, abx=False))

        result = sepsis_compliance.run_sepsis_guidelines("P1")

        assert result["compliance"] is False
        lac = [v for v in result["violations_structured"] if "Lactate" in v["text"]]
        assert lac[0]["severity"] == "warning"

    def test_very_high_lactate_without_antibiotics_is_critical(self, monkeypatch):
        _patch_common(monkeypatch, _state(sofa=0, lactate=5.0, abx=False))

        result = sepsis_compliance.run_sepsis_guidelines("P1")

        lac = [v for v in result["violations_structured"] if "Lactate" in v["text"]]
        assert lac[0]["severity"] == "critical"

    def test_low_map_without_vasopressors_is_flagged(self, monkeypatch):
        _patch_common(monkeypatch, _state(sofa=0, lactate=1.0, map_=60, vaso=False))

        result = sepsis_compliance.run_sepsis_guidelines("P1")

        assert result["compliance"] is False
        map_violations = [v for v in result["violations_structured"] if "MAP" in v["text"]]
        assert map_violations[0]["severity"] == "warning"

    def test_severely_low_map_without_vasopressors_is_critical(self, monkeypatch):
        _patch_common(monkeypatch, _state(sofa=0, lactate=1.0, map_=50, vaso=False))

        result = sepsis_compliance.run_sepsis_guidelines("P1")

        map_violations = [v for v in result["violations_structured"] if "MAP" in v["text"]]
        assert map_violations[0]["severity"] == "critical"

    def test_recommended_actions_are_added_to_highlight_path(self, monkeypatch):
        _patch_common(monkeypatch, _state(sofa=3, abx=False))

        result = sepsis_compliance.run_sepsis_guidelines("P1")

        path = result["paths"][0]
        assert "RecommendedAction:ACT_ABX" in path["nodes"]
        assert "RecommendedAction:ACT_CULTURES" in path["nodes"]
        assert path["relationships"].count("RECOMMENDS_ACTION") == 2
