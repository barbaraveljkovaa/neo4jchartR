"""
In-memory graph for USE_GRAPH_DEMO=1 — mirrors seed_data.py + sepsis_data.py without Neo4j.
Supports reads, patient creation, document append, and violation sync for full dashboard demo.
"""
from __future__ import annotations

import copy
from datetime import date
from typing import Any

# -------- Static seed (aligned with seed_data.py / sepsis_data.py) --------

DOCTORS: dict[str, dict[str, str]] = {
    "DOC1": {"id": "DOC1", "name": "Dr. Evans", "specialty": "Cardiology"},
    "DOC2": {"id": "DOC2", "name": "Dr. Brown", "specialty": "General"},
    "DOC3": {"id": "DOC3", "name": "Dr. Patel", "specialty": "Endocrinology"},
    "DOC4": {"id": "DOC4", "name": "Dr. Nguyen", "specialty": "Pulmonology"},
    "DOC5": {"id": "DOC5", "name": "Dr. Taylor", "specialty": "Orthopedics"},
    "DOC6": {"id": "DOC6", "name": "Dr. Wright", "specialty": "Infectious Disease"},
    "DOC7": {"id": "DOC7", "name": "Dr. King", "specialty": "Hematology"},
    "DOC8": {"id": "DOC8", "name": "Dr. Scott", "specialty": "Psychiatry"},
    "DOC9": {"id": "DOC9", "name": "Dr. Green", "specialty": "Internal Medicine"},
    "DOC10": {"id": "DOC10", "name": "Dr. Hall", "specialty": "Radiology"},
}

DISEASES: dict[str, dict[str, str]] = {
    "D1": {"id": "D1", "name": "Hypertension", "icd10": "I10"},
    "D2": {"id": "D2", "name": "Type 2 Diabetes", "icd10": "E11"},
    "D3": {"id": "D3", "name": "Asthma", "icd10": "J45"},
    "D4": {"id": "D4", "name": "Osteoarthritis", "icd10": "M19"},
    "D5": {"id": "D5", "name": "Anxiety", "icd10": "F41"},
    "D6": {"id": "D6", "name": "Pneumonia", "icd10": "J18"},
    "D7": {"id": "D7", "name": "Anemia", "icd10": "D50"},
    "D8": {"id": "D8", "name": "COPD", "icd10": "J44"},
}

DRUGS: dict[str, str] = {
    "DRUG1": "Lisinopril",
    "DRUG2": "Metformin",
    "DRUG3": "Inhaled corticosteroid",
    "DRUG4": "NSAIDs",
    "DRUG5": "SSRI",
    "DRUG6": "Amoxicillin",
    "DRUG7": "Iron supplement",
    "DRUG8": "Short-acting beta-agonist",
    "DRUG9": "Azithromycin",
    "DRUG10": "Ferrous sulfate",
    "DRUG11": "LABA/ICS combination",
    "DRUG12": "Amlodipine",
    "DRUG13": "Sertraline",
    "DRUG14": "Ibuprofen",
    "DRUG15": "Omeprazole",
}

PROCEDURES: dict[str, str] = {
    "PROC1": "BP monitoring",
    "PROC2": "HbA1c test",
    "PROC3": "Spirometry",
    "PROC4": "Joint imaging",
    "PROC5": "Counseling",
    "PROC6": "Chest X-ray",
    "PROC7": "CBC draw",
    "PROC8": "Iron studies",
    "PROC9": "Blood glucose test",
    "PROC10": "Urinalysis",
    "PROC11": "Peak flow measurement",
    "PROC12": "CT chest",
}

FOLLOWUPS: dict[str, str] = {
    "FU1": "3-month BP check",
    "FU2": "Quarterly HbA1c",
    "FU3": "Annual lung function",
    "FU4": "6-month joint review",
    "FU5": "Monthly therapy review",
    "FU6": "Post-pneumonia follow-up",
    "FU7": "Anemia recheck in 3 months",
    "FU8": "COPD annual review",
}

HOSPITALS: dict[str, dict[str, str]] = {
    "H1": {"id": "H1", "name": "City General"},
    "H2": {"id": "H2", "name": "Central Clinic"},
    "H3": {"id": "H3", "name": "North Medical Center"},
    "H4": {"id": "H4", "name": "South Health Campus"},
    "H5": {"id": "H5", "name": "East Valley Hospital"},
}

# (appointment_id, date, reason, status, patient_id, hospital_id) — from seed_data.py
APPOINTMENTS: list[tuple[str, str, str, str, str, str]] = [
    ("A1", "2024-01-10", "Routine BP check", "completed", "P1", "H1"),
    ("A2", "2024-02-15", "Follow-up hypertension", "completed", "P1", "H1"),
    ("A3", "2024-04-01", "Quarterly diabetes review", "completed", "P2", "H2"),
    ("A4", "2024-05-12", "HbA1c and medication review", "completed", "P2", "H2"),
    ("A5", "2024-03-05", "Asthma control", "completed", "P3", "H1"),
    ("A6", "2024-06-10", "Spirometry follow-up", "completed", "P3", "H4"),
    ("A7", "2024-01-20", "Hypertension + diabetes", "completed", "P4", "H3"),
    ("A8", "2024-04-18", "BP and glucose", "completed", "P4", "H3"),
    ("A9", "2024-02-01", "Diabetes new diagnosis", "completed", "P5", "H2"),
    ("A10", "2024-05-20", "Medication non-compliance", "completed", "P5", "H2"),
    ("A11", "2024-03-12", "Asthma exacerbation", "completed", "P6", "H4"),
    ("A12", "2024-07-01", "Annual lung function", "completed", "P6", "H4"),
    ("A13", "2024-01-08", "Anxiety assessment", "completed", "P7", "H2"),
    ("A14", "2024-04-22", "Therapy review", "completed", "P7", "H2"),
    ("A15", "2024-02-10", "Hypertension, knee pain", "completed", "P8", "H1"),
    ("A16", "2024-06-05", "BP and joint review", "completed", "P8", "H1"),
    ("A17", "2024-03-18", "Anxiety - no meds yet", "completed", "P9", "H2"),
    ("A18", "2024-01-25", "Diabetes annual", "completed", "P10", "H2"),
    ("A19", "2024-05-08", "HbA1c in range", "completed", "P10", "H2"),
    ("A20", "2024-02-28", "Hypertension follow-up", "completed", "P11", "H1"),
    ("A21", "2024-04-14", "Cough and fever", "completed", "P12", "H3"),
    ("A22", "2024-03-01", "Fatigue, possible anemia", "completed", "P13", "H4"),
    ("A23", "2024-06-20", "CBC and iron studies", "completed", "P13", "H4"),
    ("A24", "2024-01-15", "COPD exacerbation", "completed", "P14", "H4"),
    ("A25", "2024-05-30", "Breathing difficulty", "completed", "P15", "H1"),
    ("A26", "2024-04-08", "Pneumonia follow-up", "completed", "P12", "H3"),
    ("A27", "2024-07-10", "Anemia recheck", "completed", "P13", "H4"),
    ("A28", "2024-02-20", "Diabetes screening", "completed", "P16", "H2"),
    ("A29", "2024-06-15", "Asthma control", "completed", "P17", "H4"),
    ("A30", "2024-03-25", "Hypertension", "completed", "P18", "H1"),
    ("A31", "2024-05-05", "Osteoarthritis knee", "completed", "P19", "H5"),
    ("A32", "2024-04-28", "Anxiety and insomnia", "completed", "P20", "H2"),
]


def _appointments_for_patient(patient_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for aid, adate, reason, status, pid, hid in APPOINTMENTS:
        if pid != patient_id:
            continue
        out.append(
            {
                "id": aid,
                "date": adate,
                "reason": reason,
                "status": status,
                "hospital_id": hid,
                "hospital_name": HOSPITALS.get(hid, {}).get("name", hid),
            }
        )
    return out

PROTOCOLS: list[dict[str, str]] = [
    {"disease_id": "D1", "drug_id": "DRUG1", "procedure_id": "PROC1", "followup_id": "FU1"},
    {"disease_id": "D2", "drug_id": "DRUG2", "procedure_id": "PROC2", "followup_id": "FU2"},
    {"disease_id": "D3", "drug_id": "DRUG3", "procedure_id": "PROC3", "followup_id": "FU3"},
    {"disease_id": "D4", "drug_id": "DRUG4", "procedure_id": "PROC4", "followup_id": "FU4"},
    {"disease_id": "D5", "drug_id": "DRUG5", "procedure_id": "PROC5", "followup_id": "FU5"},
    {"disease_id": "D6", "drug_id": "DRUG6", "procedure_id": "PROC6", "followup_id": "FU6"},
    {"disease_id": "D7", "drug_id": "DRUG7", "procedure_id": "PROC8", "followup_id": "FU7"},
    {"disease_id": "D8", "drug_id": "DRUG11", "procedure_id": "PROC3", "followup_id": "FU8"},
]

VISITS: list[tuple[str, str]] = [
    ("P1", "DOC1"), ("P2", "DOC3"), ("P3", "DOC4"), ("P4", "DOC1"), ("P4", "DOC3"),
    ("P5", "DOC2"), ("P5", "DOC3"), ("P6", "DOC4"), ("P7", "DOC2"), ("P8", "DOC1"), ("P8", "DOC5"),
    ("P9", "DOC2"), ("P10", "DOC3"), ("P11", "DOC1"), ("P12", "DOC6"), ("P13", "DOC7"),
    ("P14", "DOC4"), ("P15", "DOC4"), ("P16", "DOC3"), ("P17", "DOC4"), ("P18", "DOC1"),
    ("P19", "DOC5"), ("P20", "DOC8"),
]

HAS_DISEASE: list[tuple[str, str, str]] = [
    ("P1", "D1", "2023-06-01"), ("P2", "D2", "2023-08-10"), ("P3", "D3", "2023-09-15"),
    ("P4", "D1", "2022-01-20"), ("P4", "D2", "2023-04-01"), ("P5", "D2", "2023-11-01"),
    ("P6", "D3", "2022-05-10"), ("P7", "D5", "2024-01-08"), ("P8", "D1", "2020-03-12"), ("P8", "D4", "2021-07-22"),
    ("P9", "D5", "2023-12-01"), ("P10", "D2", "2023-02-14"), ("P11", "D1", "2022-09-01"),
    ("P12", "D6", "2024-04-14"), ("P13", "D7", "2024-03-01"), ("P14", "D8", "2020-05-10"),
    ("P15", "D3", "2023-01-20"), ("P16", "D2", "2024-02-20"), ("P17", "D3", "2022-11-01"),
    ("P18", "D1", "2023-03-25"), ("P19", "D4", "2022-08-15"), ("P20", "D5", "2024-04-28"),
]

PATIENT_DRUGS: dict[str, list[str]] = {
    "P1": ["DRUG1"], "P2": ["DRUG2"], "P3": ["DRUG3"], "P4": ["DRUG1", "DRUG2"],
    "P5": ["DRUG1", "DRUG2"], "P6": ["DRUG3"], "P7": ["DRUG5"], "P8": ["DRUG1"],
    "P9": [], "P10": ["DRUG2"], "P11": ["DRUG1"], "P12": ["DRUG6"], "P13": ["DRUG7"],
    "P14": ["DRUG11"], "P15": ["DRUG3"], "P16": ["DRUG2"], "P17": ["DRUG3"],
    "P18": ["DRUG1"], "P19": ["DRUG4"], "P20": ["DRUG5"],
}

PATIENT_PROCEDURES: dict[str, list[str]] = {
    "P1": ["PROC1"], "P2": ["PROC2"], "P3": ["PROC3"], "P4": ["PROC1", "PROC2"],
    "P5": ["PROC2"], "P6": ["PROC3"], "P7": ["PROC5"], "P8": ["PROC1"],
    "P9": ["PROC5"], "P10": ["PROC2"], "P11": ["PROC1"], "P12": ["PROC6"],
    "P13": ["PROC7", "PROC8"], "P14": ["PROC3"], "P15": ["PROC11"], "P16": ["PROC2"],
    "P17": ["PROC3"], "P18": ["PROC1"], "P19": ["PROC4"], "P20": ["PROC5"],
}

PATIENT_META: dict[str, dict[str, Any]] = {
    "P1": {"name": "Alice Smith", "age": 34, "sex": "F"},
    "P2": {"name": "Bob Jones", "age": 45, "sex": "M"},
    "P3": {"name": "Carol White", "age": 28, "sex": "F"},
    "P4": {"name": "David Lee", "age": 52, "sex": "M"},
    "P5": {"name": "Eva Martinez", "age": 41, "sex": "F"},
    "P6": {"name": "Frank Chen", "age": 39, "sex": "M"},
    "P7": {"name": "Grace Kim", "age": 33, "sex": "F"},
    "P8": {"name": "Henry Wilson", "age": 61, "sex": "M"},
    "P9": {"name": "Iris Davis", "age": 29, "sex": "F"},
    "P10": {"name": "Jack Brown", "age": 47, "sex": "M"},
    "P11": {"name": "Kate Moore", "age": 55, "sex": "F"},
    "P12": {"name": "Leo Garcia", "age": 42, "sex": "M"},
    "P13": {"name": "Mia Johnson", "age": 38, "sex": "F"},
    "P14": {"name": "Noah Williams", "age": 67, "sex": "M"},
    "P15": {"name": "Olivia Clark", "age": 31, "sex": "F"},
    "P16": {"name": "Paul Lewis", "age": 44, "sex": "M"},
    "P17": {"name": "Quinn Taylor", "age": 50, "sex": "F"},
    "P18": {"name": "Ryan Adams", "age": 36, "sex": "M"},
    "P19": {"name": "Sofia Hernandez", "age": 59, "sex": "F"},
    "P20": {"name": "Tom Anderson", "age": 48, "sex": "M"},
}

# Clinical states from sepsis_data.py (attach-to-seed mode)
CLINICAL_STATES: dict[str, dict[str, Any]] = {
    "P1": {"sofa_score": 10, "map": 58, "gcs": 10, "creatinine": 2.1, "lactate": 3.2, "antibiotics_active": True, "vasopressors_active": True, "cultures_ordered": True},
    "P2": {"sofa_score": 12, "map": 52, "gcs": 8, "creatinine": 2.8, "lactate": 4.1, "antibiotics_active": True, "vasopressors_active": True, "cultures_ordered": True},
    "P3": {"sofa_score": 9, "map": 55, "gcs": 12, "creatinine": 1.9, "lactate": 2.8, "antibiotics_active": False, "vasopressors_active": False, "cultures_ordered": True},
    "P4": {"sofa_score": 11, "map": 50, "gcs": 7, "creatinine": 3.2, "lactate": 5.0, "antibiotics_active": True, "vasopressors_active": True, "cultures_ordered": True},
    "P5": {"sofa_score": 8, "map": 60, "gcs": 11, "creatinine": 1.8, "lactate": 2.5, "antibiotics_active": True, "vasopressors_active": False, "cultures_ordered": False},
    "P6": {"sofa_score": 13, "map": 48, "gcs": 6, "creatinine": 3.5, "lactate": 4.8, "antibiotics_active": True, "vasopressors_active": True, "cultures_ordered": True},
    "P7": {"sofa_score": 10, "map": 56, "gcs": 9, "creatinine": 2.2, "lactate": 3.0, "antibiotics_active": False, "vasopressors_active": False, "cultures_ordered": False},
    "P8": {"sofa_score": 14, "map": 45, "gcs": 5, "creatinine": 4.0, "lactate": 6.2, "antibiotics_active": True, "vasopressors_active": True, "cultures_ordered": True},
    "P9": {"sofa_score": 9, "map": 52, "gcs": 10, "creatinine": 2.0, "lactate": 2.9, "antibiotics_active": True, "vasopressors_active": False, "cultures_ordered": True},
    "P10": {"sofa_score": 11, "map": 53, "gcs": 8, "creatinine": 2.5, "lactate": 3.8, "antibiotics_active": False, "vasopressors_active": False, "cultures_ordered": False},
    "P11": {"sofa_score": 2, "map": 82, "gcs": 15, "creatinine": 0.9, "lactate": 1.0, "antibiotics_active": False, "vasopressors_active": False, "cultures_ordered": True},
    "P12": {"sofa_score": 3, "map": 78, "gcs": 15, "creatinine": 1.0, "lactate": 2.6, "antibiotics_active": False, "vasopressors_active": False, "cultures_ordered": True},
    "P13": {"sofa_score": 1, "map": 85, "gcs": 15, "creatinine": 0.8, "lactate": 0.8, "antibiotics_active": False, "vasopressors_active": False, "cultures_ordered": False},
    "P14": {"sofa_score": 4, "map": 75, "gcs": 14, "creatinine": 1.1, "lactate": 1.4, "antibiotics_active": True, "vasopressors_active": False, "cultures_ordered": True},
    "P15": {"sofa_score": 0, "map": 88, "gcs": 15, "creatinine": 0.7, "lactate": 0.7, "antibiotics_active": False, "vasopressors_active": False, "cultures_ordered": False},
    "P16": {"sofa_score": 2, "map": 80, "gcs": 15, "creatinine": 0.95, "lactate": 1.0, "antibiotics_active": False, "vasopressors_active": False, "cultures_ordered": True},
    "P17": {"sofa_score": 1, "map": 84, "gcs": 15, "creatinine": 0.85, "lactate": 0.9, "antibiotics_active": False, "vasopressors_active": False, "cultures_ordered": False},
    "P18": {"sofa_score": 3, "map": 76, "gcs": 15, "creatinine": 1.05, "lactate": 1.1, "antibiotics_active": False, "vasopressors_active": False, "cultures_ordered": True},
    "P19": {"sofa_score": 0, "map": 86, "gcs": 15, "creatinine": 0.75, "lactate": 0.8, "antibiotics_active": False, "vasopressors_active": False, "cultures_ordered": False},
    "P20": {"sofa_score": 2, "map": 79, "gcs": 15, "creatinine": 1.0, "lactate": 1.0, "antibiotics_active": False, "vasopressors_active": False, "cultures_ordered": True},
}

INITIAL_NOTES: dict[str, list[dict[str, str]]] = {
    "P1": [
        {"id": "N1", "text": "BP elevated at visit; started on lifestyle advice.", "date": "2024-01-10"},
        {"id": "N2", "text": "BP improved. Continue current medication.", "date": "2024-02-15"},
    ],
    "P2": [{"id": "N3", "text": "HbA1c 7.2%; discuss metformin adherence.", "date": "2024-04-01"}],
    "P5": [{"id": "N4", "text": "Patient reports difficulty with metformin; considering alternative.", "date": "2024-05-20"}],
    "P6": [{"id": "N5", "text": "Asthma control suboptimal; step up ICS.", "date": "2024-03-12"}],
    "P10": [{"id": "N6", "text": "Diabetes follow-up; insulin added due to persistent elevation.", "date": "2024-06-01"}],
}

SEPSIS_GUIDELINE: dict[str, Any] = {
    "guideline_id": "SEPSIS_1",
    "guideline_name": "Sepsis-3 / Hour-1 Bundle",
    "sofa_threshold": 2,
    "lactate_threshold": 2.0,
    "map_threshold": 65,
    "action_ids": ["ACT_ABX", "ACT_CULT", "ACT_FLUID", "ACT_VASO"],
    "action_names": [
        "Broad-spectrum antibiotics",
        "Blood cultures",
        "30 mL/kg crystalloid",
        "Vasopressors if MAP < 65",
    ],
    "lab_ids": ["LAB_LACT"],
    "lab_names": ["Lactate level"],
    "drug_ids": ["DRUG6"],
    "drug_names": ["Amoxicillin"],
    "procedure_ids": ["PROC7"],
    "procedure_names": ["CBC draw"],
    "followup_ids": ["FU6"],
    "followup_names": ["Post-pneumonia follow-up"],
}


class DemoGraphStore:
    """Mutable in-memory graph used when USE_GRAPH_DEMO=1."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.patients: dict[str, dict[str, Any]] = {}
        self.violations: dict[str, list[dict[str, Any]]] = {}
        self._note_seq = 100
        for pid, meta in PATIENT_META.items():
            diseases = []
            for p, did, diagnosed_on in HAS_DISEASE:
                if p == pid:
                    d = DISEASES[did]
                    diseases.append({"id": did, "name": d["name"], "icd10": d["icd10"], "diagnosed_on": diagnosed_on})
            doc_ids = [doc for p, doc in VISITS if p == pid]
            primary_doc = doc_ids[0] if doc_ids else None
            self.patients[pid] = {
                "patient_id": pid,
                "name": meta["name"],
                "age": meta["age"],
                "sex": meta["sex"],
                "source": "demo",
                "diseases": diseases,
                "symptoms": [],
                "drug_ids": list(PATIENT_DRUGS.get(pid, [])),
                "procedure_ids": list(PATIENT_PROCEDURES.get(pid, [])),
                "clinical_state": copy.deepcopy(CLINICAL_STATES.get(pid)),
                "doctor_id": primary_doc,
                "notes": copy.deepcopy(INITIAL_NOTES.get(pid, [])),
                "appointments": _appointments_for_patient(pid),
            }
        self.violations = {}

    def patient_ids(self) -> frozenset[str]:
        return frozenset(self.patients.keys())

    def patient_exists(self, patient_id: str) -> bool:
        return (patient_id or "").strip() in self.patients

    def get_patient_name(self, patient_id: str) -> str | None:
        p = self.patients.get((patient_id or "").strip())
        return p["name"] if p else None

    def next_patient_id(self) -> str:
        max_num = 0
        for pid in self.patients:
            if pid.startswith("P"):
                try:
                    max_num = max(max_num, int(pid[1:]))
                except ValueError:
                    pass
        return f"P{max_num + 1}"

    def next_note_id(self) -> str:
        self._note_seq += 1
        return f"N{self._note_seq}"

    def get_doctors_and_specialties(self) -> list[dict[str, str]]:
        return [
            {"doctor_id": d["id"], "doctor_name": d["name"], "specialty": d["specialty"]}
            for d in DOCTORS.values()
        ]

    def get_protocol_guidelines(self) -> list[dict[str, Any]]:
        out = []
        for pr in PROTOCOLS:
            did = pr["disease_id"]
            d = DISEASES[did]
            out.append(
                {
                    "disease_id": did,
                    "disease_name": d["name"],
                    "disease_icd10": d["icd10"],
                    "drug_id": pr["drug_id"],
                    "drug_name": DRUGS[pr["drug_id"]],
                    "procedure_id": pr["procedure_id"],
                    "procedure_name": PROCEDURES[pr["procedure_id"]],
                    "followup_id": pr["followup_id"],
                    "followup_name": FOLLOWUPS[pr["followup_id"]],
                }
            )
        return out

    def get_protocol_for_disease(self, disease_id: str) -> list[dict[str, Any]]:
        return [r for r in self.get_protocol_guidelines() if r["disease_id"] == disease_id]

    def get_sepsis_guidelines(self) -> list[dict[str, Any]]:
        return [dict(SEPSIS_GUIDELINE)]

    def get_patients_with_diseases(self) -> list[dict[str, Any]]:
        rows = []
        for pid, p in sorted(self.patients.items()):
            for d in p["diseases"]:
                rows.append(
                    {
                        "patient_id": pid,
                        "patient_name": p["name"],
                        "patient_age": p["age"],
                        "patient_sex": p["sex"],
                        "disease_id": d["id"],
                        "disease_name": d["name"],
                        "disease_icd10": d.get("icd10"),
                        "diagnosed_on": d.get("diagnosed_on"),
                    }
                )
        return rows

    def get_patients_with_doctor(self) -> list[dict[str, Any]]:
        rows = []
        for pid, p in sorted(self.patients.items()):
            doc_id = p.get("doctor_id")
            if not doc_id or doc_id not in DOCTORS:
                continue
            doc = DOCTORS[doc_id]
            rows.append(
                {
                    "patient_id": pid,
                    "patient_name": p["name"],
                    "doctor_id": doc_id,
                    "doctor_name": doc["name"],
                }
            )
        return rows

    def get_actual_patient_treatments(self, patient_id: str, disease_id: str) -> dict[str, Any]:
        p = self.patients.get((patient_id or "").strip())
        if not p:
            return {"actual_drug_ids": [], "actual_drug_names": [], "actual_procedure_ids": [], "actual_procedure_names": []}
        drug_ids = p.get("drug_ids") or []
        proc_ids = p.get("procedure_ids") or []
        return {
            "actual_drug_ids": drug_ids,
            "actual_drug_names": [DRUGS.get(x, x) for x in drug_ids],
            "actual_procedure_ids": proc_ids,
            "actual_procedure_names": [PROCEDURES.get(x, x) for x in proc_ids],
        }

    def get_all_patients_graph_data(self) -> list[dict[str, Any]]:
        out = []
        for pid, p in sorted(self.patients.items()):
            out.append(
                {
                    "patient_id": pid,
                    "patient_name": p["name"],
                    "age": p["age"],
                    "sex": p["sex"],
                    "source": p.get("source", "demo"),
                    "diseases": [{"id": d["id"], "name": d["name"]} for d in p["diseases"]],
                    "symptoms": list(p.get("symptoms") or []),
                    "clinical_state": copy.deepcopy(p.get("clinical_state")),
                }
            )
        return out

    def get_patients_with_clinical_state(self) -> list[dict[str, Any]]:
        rows = []
        for pid, p in sorted(self.patients.items()):
            cs = p.get("clinical_state")
            if not cs:
                continue
            rows.append(
                {
                    "patient_id": pid,
                    "patient_name": p["name"],
                    "patient_age": p["age"],
                    "patient_sex": p["sex"],
                    "sofa_score": cs.get("sofa_score"),
                    "lactate": cs.get("lactate"),
                    "map": cs.get("map"),
                    "antibiotics_active": cs.get("antibiotics_active"),
                    "cultures_ordered": cs.get("cultures_ordered"),
                }
            )
        return rows

    def get_patient_clinical_state(self, patient_id: str) -> dict[str, Any] | None:
        p = self.patients.get((patient_id or "").strip())
        if not p or not p.get("clinical_state"):
            return None
        cs = copy.deepcopy(p["clinical_state"])
        cs["state_id"] = f"CS_{patient_id}"
        return cs

    def get_patient_appointments(self, patient_id: str) -> list[dict[str, Any]]:
        p = self.patients.get((patient_id or "").strip())
        if not p:
            return []
        return [
            {"appointment_id": a["id"], "appointment_date": a["date"]}
            for a in sorted(p.get("appointments") or [], key=lambda x: x.get("date") or "")
        ]

    def get_hospitals_visited_by_patients(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for pid, p in sorted(self.patients.items()):
            for a in sorted(p.get("appointments") or [], key=lambda x: x.get("date") or ""):
                hid = a.get("hospital_id")
                h = HOSPITALS.get(hid or "", {})
                rows.append(
                    {
                        "patient_id": pid,
                        "patient_name": p["name"],
                        "appointment_id": a["id"],
                        "appointment_date": a["date"],
                        "hospital_id": hid,
                        "hospital_name": h.get("name", hid),
                    }
                )
        return rows

    def get_patient_notes(self, patient_id: str) -> list[dict[str, Any]]:
        p = self.patients.get((patient_id or "").strip())
        return list(p.get("notes") or []) if p else []

    def get_patient_full_journey(self, patient_id: str) -> list[dict[str, Any]]:
        p = self.patients.get((patient_id or "").strip())
        if not p:
            return []
        doc = DOCTORS.get(p.get("doctor_id") or "", {})
        drug_id = (p.get("drug_ids") or ["DRUG1"])[0]
        proc_id = (p.get("procedure_ids") or ["PROC1"])[0]
        base = {
            "patient_id": patient_id,
            "patient_name": p["name"],
            "age": p["age"],
            "sex": p["sex"],
        }
        rows: list[dict[str, Any]] = []
        for a in p.get("appointments") or []:
            rows.append(
                {
                    **base,
                    "appointment_id": a["id"],
                    "appointment_date": a["date"],
                    "appointment_reason": a["reason"],
                    "appointment_status": a["status"],
                    "hospital_id": a["hospital_id"],
                    "hospital_name": a["hospital_name"],
                }
            )
        rows.append(
            {
                **base,
                "encounter_id": f"E_{patient_id}_1",
                "encounter_date": "2024-06-01",
                "encounter_type": "Outpatient",
                "encounter_notes": "Assessment and treatment plan documented.",
                "doctor_name": doc.get("name", "Dr. Patel"),
                "lab_id": f"L_{patient_id}_1",
                "lab_name": "Basic metabolic panel",
                "lab_result_value": "1.0",
                "lab_unit": "mg/dL",
                "lab_date": "2024-06-01",
                "drug_id": drug_id,
                "drug_name": DRUGS.get(drug_id, drug_id),
                "prescribed_on": "2024-06-01",
                "dose": "500mg",
                "procedure_id": proc_id,
                "procedure_name": PROCEDURES.get(proc_id, proc_id),
                "procedure_date": "2024-06-02",
            }
        )
        return rows

    def get_patients_for_comparison(self, patient_ids: list[str]) -> list[dict[str, Any]]:
        from ai_compliance import check_patient_compliance

        out = []
        for pid in patient_ids:
            p = self.patients.get(pid)
            if not p:
                continue
            viol: list[str] = []
            for v in self.violations.get(pid) or []:
                desc = v.get("description")
                if desc and desc not in viol:
                    viol.append(desc)
            if not viol:
                for d in p["diseases"]:
                    try:
                        r = check_patient_compliance(pid, p["name"], d["id"], d["name"])
                        for v in r.get("violations") or []:
                            if v not in viol:
                                viol.append(v)
                    except Exception:
                        pass
            out.append(
                {
                    "patient_id": pid,
                    "patient_name": p["name"],
                    "age": p["age"],
                    "sex": p["sex"],
                    "diseases": [{"id": d["id"], "name": d["name"]} for d in p["diseases"]],
                    "symptoms": list(p.get("symptoms") or []),
                    "violations": viol,
                    "clinical_state": copy.deepcopy(p.get("clinical_state")),
                }
            )
        return out

    def sync_violations(self) -> int:
        from ai_compliance import run_compliance_check
        from sepsis_compliance import run_sepsis_guidelines_all

        self.violations = {}
        count = 0
        for r in run_sepsis_guidelines_all():
            if r.get("compliance"):
                continue
            pid = r.get("patient_id")
            if not pid:
                continue
            self.violations.setdefault(pid, [])
            for i, vtext in enumerate(r.get("violations") or []):
                structured = (r.get("violations_structured") or [{}] * (i + 1))[i] if r.get("violations_structured") else {}
                vid = f"V_{pid}_{i}"
                self.violations[pid].append(
                    {
                        "id": vid,
                        "description": (vtext or "")[:500],
                        "source": "sepsis",
                        "severity": structured.get("severity", "warning"),
                        "reason": (structured.get("reason") or "")[:500],
                    }
                )
                count += 1
        compliance = run_compliance_check()
        for r in compliance.get("patients_with_violations") or []:
            pid = r.get("patient_id")
            did = r.get("disease_id")
            if not pid:
                continue
            self.violations.setdefault(pid, [])
            for i, vtext in enumerate(r.get("violations") or []):
                structured = (r.get("violations_structured") or [{}] * (i + 1))[i] if r.get("violations_structured") else {}
                vid = f"V_D_{pid}_{did}_{i}"
                self.violations[pid].append(
                    {
                        "id": vid,
                        "description": (vtext or "")[:500],
                        "source": "protocol",
                        "disease_id": did,
                        "disease_name": r.get("disease_name"),
                        "severity": structured.get("severity", "warning"),
                        "reason": (structured.get("reason") or "")[:500],
                    }
                )
                count += 1
        return count

    def _apply_extracted_data(self, pid: str, data: dict) -> None:
        p = self.patients[pid]
        for symptom in data.get("symptoms") or []:
            sym_id = "SYM_" + "".join(c if c.isalnum() else "_" for c in symptom.lower())[:48]
            if not any(s.get("id") == sym_id for s in p["symptoms"]):
                p["symptoms"].append({"id": sym_id, "name": symptom})
        for disease_name in data.get("diseases") or []:
            existing = next((d for d in DISEASES.values() if d["name"].lower() == disease_name.lower()), None)
            if existing:
                did = existing["id"]
            else:
                did = "D_" + "".join(c if c.isalnum() else "_" for c in disease_name)[:40]
            if not any(d["id"] == did for d in p["diseases"]):
                p["diseases"].append({"id": did, "name": disease_name, "icd10": None, "diagnosed_on": date.today().isoformat()})
        clinical = data.get("clinical_values") or {}
        if clinical:
            key_map = {"MAP": "map", "SOFA": "sofa_score", "creatinine": "creatinine", "GCS": "gcs", "lactate": "lactate"}
            cs = dict(p.get("clinical_state") or {})
            for src, dst in key_map.items():
                if clinical.get(src) is not None:
                    cs[dst] = clinical[src]
            for k, v in clinical.items():
                if k not in key_map and v is not None:
                    cs[k.lower()] = v
            cs.setdefault("antibiotics_active", False)
            cs.setdefault("vasopressors_active", False)
            cs.setdefault("cultures_ordered", True)
            p["clinical_state"] = cs

    def create_patient_from_document(self, data: dict) -> dict:
        pid = self.next_patient_id()
        name = data.get("patient_name") or f"Patient {pid}"
        self.patients[pid] = {
            "patient_id": pid,
            "name": name,
            "age": data.get("age"),
            "sex": data.get("sex"),
            "source": "document_upload",
            "diseases": [],
            "symptoms": [],
            "drug_ids": [],
            "procedure_ids": [],
            "clinical_state": None,
            "doctor_id": None,
            "notes": [],
            "appointments": [],
        }
        self._apply_extracted_data(pid, data)
        note_text = self._build_document_note_text(data)
        note_id = self.next_note_id()
        if note_text:
            self.patients[pid]["notes"].append(
                {"id": note_id, "text": note_text, "date": date.today().isoformat()}
            )
        return {
            "patient_id": pid,
            "patient_name": name,
            "age": data.get("age"),
            "sex": data.get("sex"),
            "symptoms": data.get("symptoms") or [],
            "diseases": data.get("diseases") or [],
            "clinical_values": data.get("clinical_values") or {},
            "note_id": note_id,
            "note_text": note_text,
            "mode": "create",
        }

    def append_document_to_patient(self, patient_id: str, data: dict, document_summary: str | None = None) -> dict:
        pid = (patient_id or "").strip()
        if pid not in self.patients:
            raise ValueError(f"Patient not found: {pid}")
        p = self.patients[pid]
        if data.get("patient_name"):
            p["name"] = data["patient_name"]
        if data.get("age") is not None:
            p["age"] = data["age"]
        if data.get("sex"):
            p["sex"] = data["sex"]
        self._apply_extracted_data(pid, data)
        note_text = self._build_document_note_text(data, document_summary)
        note_id = self.next_note_id()
        p["notes"].append({"id": note_id, "text": note_text, "date": date.today().isoformat()})
        return {
            "patient_id": pid,
            "patient_name": p["name"],
            "age": p["age"],
            "sex": p["sex"],
            "symptoms": data.get("symptoms") or [],
            "diseases": data.get("diseases") or [],
            "clinical_values": data.get("clinical_values") or {},
            "note_id": note_id,
            "note_text": note_text,
            "mode": "append",
        }

    @staticmethod
    def _build_document_note_text(data: dict, document_summary: str | None = None) -> str:
        if document_summary and document_summary.strip():
            return document_summary.strip()
        parts = [f"Document uploaded on {date.today().isoformat()}."]
        if data.get("diseases"):
            parts.append("Diagnoses noted: " + ", ".join(data["diseases"]) + ".")
        if data.get("symptoms"):
            parts.append("Symptoms noted: " + ", ".join(data["symptoms"]) + ".")
        clinical = data.get("clinical_values") or {}
        if clinical:
            parts.append("Clinical values: " + ", ".join(f"{k}={v}" for k, v in clinical.items()) + ".")
        if len(parts) == 1:
            parts.append("Visit or discharge summary added to the chart.")
        return " ".join(parts)

    def collect_patient_scoped_graph_rows(self, patient_id: str) -> list[dict[str, Any]]:
        pid = (patient_id or "").strip()
        p = self.patients.get(pid)
        if not p:
            return []

        def _nid(label: str, node_id: str) -> str:
            return f"demo:{label}:{node_id}"

        def _row(sid, tid, rt, sl, tl, sp, tp):
            return {
                "src_id": sid,
                "tgt_id": tid,
                "rel_type": rt,
                "src_label": sl,
                "tgt_label": tl,
                "src_props": sp,
                "tgt_props": tp,
            }

        prow = {"id": pid, "name": p["name"], "age": p["age"], "sex": p["sex"]}
        pn = _nid("Patient", pid)
        rows: list[dict[str, Any]] = []

        for d in p["diseases"]:
            did = d["id"]
            dn = _nid("Disease", did)
            rows.append(_row(pn, dn, "HAS_DISEASE", "Patient", "Disease", prow, {"id": did, "name": d["name"]}))
            for pr in self.get_protocol_for_disease(did):
                drugn = _nid("Drug", pr["drug_id"])
                procn = _nid("Procedure", pr["procedure_id"])
                fun = _nid("FollowUp", pr["followup_id"])
                rows.append(_row(dn, drugn, "RECOMMENDED_DRUG", "Disease", "Drug", {"id": did, "name": d["name"]}, {"id": pr["drug_id"], "name": pr["drug_name"]}))
                rows.append(_row(drugn, procn, "RECOMMENDED_PROCEDURE", "Drug", "Procedure", {"id": pr["drug_id"], "name": pr["drug_name"]}, {"id": pr["procedure_id"], "name": pr["procedure_name"]}))
                rows.append(_row(procn, fun, "FOLLOW_UP", "Procedure", "FollowUp", {"id": pr["procedure_id"], "name": pr["procedure_name"]}, {"id": pr["followup_id"], "name": pr["followup_name"]}))

        for s in p.get("symptoms") or []:
            sn = _nid("Symptom", s["id"])
            rows.append(_row(pn, sn, "HAS_SYMPTOM", "Patient", "Symptom", prow, {"id": s["id"], "name": s["name"]}))

        doc_id = p.get("doctor_id")
        if doc_id and doc_id in DOCTORS:
            doc = DOCTORS[doc_id]
            rows.append(
                _row(
                    pn,
                    _nid("Doctor", doc_id),
                    "VISITS",
                    "Patient",
                    "Doctor",
                    prow,
                    {"id": doc_id, "name": doc["name"], "specialty": doc["specialty"]},
                )
            )

        for appt in p.get("appointments") or []:
            aid = appt["id"]
            an = _nid("Appointment", aid)
            appt_props = {
                "id": aid,
                "date": appt["date"],
                "reason": appt["reason"],
                "status": appt["status"],
            }
            rows.append(_row(pn, an, "HAS_APPOINTMENT", "Patient", "Appointment", prow, appt_props))
            hid = appt.get("hospital_id")
            if hid and hid in HOSPITALS:
                h = HOSPITALS[hid]
                rows.append(
                    _row(
                        an,
                        _nid("Hospital", hid),
                        "AT_HOSPITAL",
                        "Appointment",
                        "Hospital",
                        appt_props,
                        {"id": hid, "name": h["name"]},
                    )
                )

        cs = p.get("clinical_state")
        if cs:
            cn = _nid("ClinicalState", f"CS_{pid}")
            cprops = {"id": f"CS_{pid}", **cs}
            rows.append(_row(pn, cn, "HAS_CLINICAL_STATE", "Patient", "ClinicalState", prow, cprops))

        for drug_id in p.get("drug_ids") or []:
            rows.append(_row(pn, _nid("Drug", drug_id), "TREATED_WITH", "Patient", "Drug", prow, {"id": drug_id, "name": DRUGS.get(drug_id, drug_id)}))
        for proc_id in p.get("procedure_ids") or []:
            rows.append(_row(pn, _nid("Procedure", proc_id), "HAD_PROCEDURE", "Patient", "Procedure", prow, {"id": proc_id, "name": PROCEDURES.get(proc_id, proc_id)}))

        for v in self.violations.get(pid) or []:
            vn = _nid("Violation", v["id"])
            rows.append(
                _row(
                    pn,
                    vn,
                    "HAS_VIOLATION",
                    "Patient",
                    "Violation",
                    prow,
                    {
                        "id": v["id"],
                        "description": v.get("description"),
                        "source": v.get("source"),
                        "severity": v.get("severity"),
                    },
                )
            )

        for note in p.get("notes") or []:
            nn = _nid("PatientNote", note["id"])
            rows.append(
                _row(
                    pn,
                    nn,
                    "HAS_NOTE",
                    "Patient",
                    "PatientNote",
                    prow,
                    {"id": note["id"], "text": note.get("text"), "date": note.get("date")},
                )
            )

        return rows

    def get_patient_timeline_data(self, patient_id: str) -> dict[str, Any]:
        pid = (patient_id or "").strip()
        p = self.patients.get(pid)
        if not p:
            return {"patient_id": pid, "patient_name": pid, "diseases": [], "clinical_state": {}, "encounters": [], "labs": [], "drugs": [], "procedures": [], "notes": []}
        journey = self.get_patient_full_journey(pid)
        encounters, labs, drugs, procedures = [], [], [], []
        seen_e, seen_l, seen_d, seen_p = set(), set(), set(), set()
        for r in journey:
            eid = r.get("encounter_id")
            if eid and eid not in seen_e:
                seen_e.add(eid)
                encounters.append({"id": eid, "date": r.get("encounter_date"), "type": r.get("encounter_type"), "notes": r.get("encounter_notes") or "", "doctor": r.get("doctor_name")})
            lid = r.get("lab_id")
            if lid and lid not in seen_l:
                seen_l.add(lid)
                labs.append({"id": lid, "name": r.get("lab_name"), "value": r.get("lab_result_value"), "unit": r.get("lab_unit"), "date": r.get("lab_date")})
            did = r.get("drug_id")
            if did and did not in seen_d:
                seen_d.add(did)
                drugs.append({"id": did, "name": r.get("drug_name"), "date": r.get("prescribed_on"), "dose": r.get("dose")})
            prid = r.get("procedure_id")
            if prid and prid not in seen_p:
                seen_p.add(prid)
                procedures.append({"id": prid, "name": r.get("procedure_name"), "date": r.get("procedure_date")})
        return {
            "patient_id": pid,
            "patient_name": p["name"],
            "age": p["age"],
            "sex": p["sex"],
            "diseases": [{"id": d["id"], "name": d["name"]} for d in p["diseases"]],
            "clinical_state": self.get_patient_clinical_state(pid) or {},
            "encounters": encounters,
            "labs": labs,
            "drugs": drugs,
            "procedures": procedures,
            "notes": self.get_patient_notes(pid),
        }


_STORE = DemoGraphStore()


def get_demo_store() -> DemoGraphStore:
    return _STORE
