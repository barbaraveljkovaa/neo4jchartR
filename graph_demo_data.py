"""
Demo-mode graph reads/writes (USE_GRAPH_DEMO=1). Delegates to demo_graph_store.
"""
from __future__ import annotations

from typing import Any

from demo_graph_store import get_demo_store


def demo_patient_exists(patient_id: str) -> bool:
    return get_demo_store().patient_exists(patient_id)


def demo_get_patients_with_diseases() -> list[dict[str, Any]]:
    return get_demo_store().get_patients_with_diseases()


def demo_get_patients_with_doctor() -> list[dict[str, Any]]:
    return get_demo_store().get_patients_with_doctor()


def demo_get_protocol_for_disease(disease_id: str) -> list[dict[str, Any]]:
    return get_demo_store().get_protocol_for_disease(disease_id)


def demo_get_protocol_guidelines() -> list[dict[str, Any]]:
    return get_demo_store().get_protocol_guidelines()


def demo_get_sepsis_guidelines() -> list[dict[str, Any]]:
    return get_demo_store().get_sepsis_guidelines()


def demo_get_doctors_and_specialties() -> list[dict[str, str]]:
    return get_demo_store().get_doctors_and_specialties()


def demo_get_actual_patient_treatments(patient_id: str, disease_id: str) -> dict[str, Any]:
    return get_demo_store().get_actual_patient_treatments(patient_id, disease_id)


def demo_get_all_patients_graph_data() -> list[dict[str, Any]]:
    return get_demo_store().get_all_patients_graph_data()


def demo_get_patients_for_comparison(patient_ids: list[str]) -> list[dict[str, Any]]:
    return get_demo_store().get_patients_for_comparison(patient_ids)


def demo_get_patients_with_clinical_state() -> list[dict[str, Any]]:
    return get_demo_store().get_patients_with_clinical_state()


def demo_get_patient_clinical_state(patient_id: str) -> dict[str, Any] | None:
    return get_demo_store().get_patient_clinical_state(patient_id)


def demo_get_patient_notes(patient_id: str) -> list[dict[str, Any]]:
    return get_demo_store().get_patient_notes(patient_id)


def demo_get_patient_full_journey(patient_id: str) -> list[dict[str, Any]]:
    return get_demo_store().get_patient_full_journey(patient_id)


def demo_get_patient_appointments(patient_id: str) -> list[dict[str, Any]]:
    return get_demo_store().get_patient_appointments(patient_id)


def demo_get_hospitals_visited_by_patients() -> list[dict[str, Any]]:
    return get_demo_store().get_hospitals_visited_by_patients()


def demo_get_patient_timeline_data(patient_id: str) -> dict[str, Any]:
    return get_demo_store().get_patient_timeline_data(patient_id)


def demo_collect_patient_scoped_graph_rows(patient_id: str) -> list[dict[str, Any]]:
    return get_demo_store().collect_patient_scoped_graph_rows(patient_id)


def demo_get_patient_name(patient_id: str) -> str | None:
    return get_demo_store().get_patient_name(patient_id)


def demo_next_patient_id() -> str:
    return get_demo_store().next_patient_id()


def demo_next_note_id() -> str:
    return get_demo_store().next_note_id()


def demo_create_patient_from_document(data: dict) -> dict:
    return get_demo_store().create_patient_from_document(data)


def demo_append_document_to_patient(patient_id: str, data: dict, document_summary: str | None = None) -> dict:
    return get_demo_store().append_document_to_patient(patient_id, data, document_summary)


def demo_sync_violations() -> int:
    return get_demo_store().sync_violations()
