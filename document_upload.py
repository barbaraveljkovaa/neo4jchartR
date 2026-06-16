"""
Medical document upload and AI extraction service.

Extracts structured patient data (symptoms, diseases, clinical values) from
uploaded PDF or plain-text documents. Uses OpenAI for intelligent extraction
with a regex-based fallback when no API key is configured.

Kept separate from neo4j_ops and dashboard so the extraction logic is modular.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

try:
    from PyPDF2 import PdfReader
    _HAS_PYPDF2 = True
except ImportError:
    _HAS_PYPDF2 = False


# ---------------------------------------------------------------------------
# 1. Text extraction from file bytes
# ---------------------------------------------------------------------------

def extract_text_from_upload(content: bytes, filename: str) -> str:
    """Return plain text from an uploaded file (PDF or text)."""
    lower = filename.lower()
    if lower.endswith(".pdf"):
        if not _HAS_PYPDF2:
            raise ValueError(
                "PDF support requires PyPDF2. Install with: pip install PyPDF2"
            )
        import io
        reader = PdfReader(io.BytesIO(content))
        pages = [page.extract_text() or "" for page in reader.pages]
        text = "\n".join(pages).strip()
        if not text:
            raise ValueError(
                "Could not extract text from PDF. The file may be scanned/image-based."
            )
        return text
    # Treat everything else as plain text
    try:
        return content.decode("utf-8", errors="replace").strip()
    except Exception:
        raise ValueError(
            f"Unsupported file type: {filename}. Please upload a PDF or text file."
        )


# ---------------------------------------------------------------------------
# 2. Structured medical-data extraction
# ---------------------------------------------------------------------------

def extract_medical_data(text: str) -> dict[str, Any]:
    """
    Extract structured medical data from document text.

    Returns::

        {
            "patient_name": str | None,
            "age": int | None,
            "sex": str | None,
            "symptoms": [str, ...],
            "diseases": [str, ...],
            "clinical_values": { "MAP": float, ... },
            "lab_results": [{name, result_value, unit, normal_range, date}, ...],
            "imaging_studies": [{name, modality, findings, date}, ...],
        }

    Tries OpenAI first; falls back to regex extraction.
    """
    if not text or not text.strip():
        raise ValueError("Document is empty — nothing to extract.")
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        try:
            return _extract_with_llm(text, api_key)
        except Exception:
            pass
    return _extract_with_regex(text)


# ---------------------------------------------------------------------------
# 2a. LLM-powered extraction
# ---------------------------------------------------------------------------

_LLM_PROMPT = """Extract structured medical data from this clinical document.
Return ONLY valid JSON (no markdown fences) with these fields:
{
  "patient_name": "string or null",
  "age": number or null,
  "sex": "M" or "F" or null,
  "symptoms": ["list of symptoms found"],
  "diseases": ["list of diseases/diagnoses found"],
  "clinical_values": {
    "MAP": number or null,
    "SOFA": number or null,
    "creatinine": number or null,
    "GCS": number or null,
    "lactate": number or null,
    "heart_rate": number or null,
    "temperature": number or null,
    "respiratory_rate": number or null
  },
  "lab_results": [
    {"name": "HbA1c", "result_value": "7.2", "unit": "%", "normal_range": "4-6", "date": "2024-06-01 or null"}
  ],
  "imaging_studies": [
    {"name": "CT chest", "modality": "CT", "findings": "brief impression text", "date": "2024-06-01 or null"}
  ]
}

Rules:
- Symptoms as simple lowercase terms (e.g. "fever", "hypotension")
- Diseases as proper medical names (e.g. "Sepsis", "Pneumonia")
- lab_results: blood tests, chemistry, CBC, etc. with numeric result_value as string
- imaging_studies: CT, MRI, X-ray, ultrasound — include modality and findings/impression when present
- Only include clinical values explicitly mentioned with numeric values
- Use null / empty list when not found

Document:
"""


def _extract_with_llm(text: str, api_key: str) -> dict[str, Any]:
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a medical data extraction system. "
                    "Return only valid JSON, no markdown."
                ),
            },
            {"role": "user", "content": _LLM_PROMPT + text[:4000]},
        ],
        max_tokens=600,
        temperature=0.1,
    )
    raw = (response.choices[0].message.content or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    data = json.loads(raw)
    return _normalize(data)


# ---------------------------------------------------------------------------
# 2b. Regex fallback extraction
# ---------------------------------------------------------------------------

_SYMPTOM_PATTERNS = [
    "fever", "hypotension", "tachycardia", "dyspnea", "cough",
    "fatigue", "nausea", "vomiting", "headache", "chest pain",
    "shortness of breath", "confusion", "dizziness", "chills",
    "abdominal pain", "diarrhea", "oliguria", "altered mental status",
    "wheezing", "sore throat", "myalgia", "malaise", "edema",
]

_DISEASE_PATTERNS: dict[str, str] = {
    "sepsis": "Sepsis",
    "pneumonia": "Pneumonia",
    "type 2 diabetes": "Type 2 Diabetes",
    "diabetes mellitus": "Type 2 Diabetes",
    "diabetes": "Type 2 Diabetes",
    "hypertension": "Hypertension",
    "asthma": "Asthma",
    "copd": "COPD",
    "anemia": "Anemia",
    "osteoarthritis": "Osteoarthritis",
    "anxiety": "Anxiety",
    "meningitis": "Meningitis",
    "urinary tract infection": "Urinary Tract Infection",
    "uti": "Urinary Tract Infection",
    "acute kidney injury": "Acute Kidney Injury",
    "heart failure": "Heart Failure",
    "atrial fibrillation": "Atrial Fibrillation",
}


def _extract_with_regex(text: str) -> dict[str, Any]:
    text_lower = text.lower()

    symptoms = [s for s in _SYMPTOM_PATTERNS if s in text_lower]

    seen_diseases: set[str] = set()
    diseases: list[str] = []
    for pattern, name in sorted(
        _DISEASE_PATTERNS.items(), key=lambda x: -len(x[0])
    ):
        if pattern in text_lower and name not in seen_diseases:
            diseases.append(name)
            seen_diseases.add(name)

    cv: dict[str, float] = {}
    for label, key in [
        (r"(?:MAP|mean arterial pressure)", "MAP"),
        (r"SOFA", "SOFA"),
        (r"creatinine", "creatinine"),
        (r"(?:GCS|glasgow coma scale?)", "GCS"),
        (r"lactate", "lactate"),
        (r"(?:heart rate|HR|pulse)", "heart_rate"),
        (r"(?:temperature|temp)", "temperature"),
        (r"(?:respiratory rate|RR)", "respiratory_rate"),
    ]:
        m = re.search(
            rf"{label}[:\s]*(\d+(?:\.\d+)?)", text, re.IGNORECASE
        )
        if m:
            cv[key] = float(m.group(1))

    patient_name = None
    m = re.search(
        r"(?:patient(?:\s+name)?|name)[:\s]+([A-Z][a-z]+ [A-Z][a-z]+)", text
    )
    if m:
        patient_name = m.group(1)

    age = None
    m = re.search(r"(\d{1,3})\s*[-–]?\s*(?:year|yr|y/?o)", text, re.IGNORECASE)
    if m:
        age = int(m.group(1))
    elif re.search(r"age", text, re.IGNORECASE):
        m2 = re.search(r"age[:\s]*(\d{1,3})", text, re.IGNORECASE)
        if m2:
            age = int(m2.group(1))

    sex = None
    if re.search(r"\b(?:male|man)\b", text_lower):
        sex = "M"
    elif re.search(r"\b(?:female|woman)\b", text_lower):
        sex = "F"

    lab_results = _extract_lab_results_regex(text)
    imaging_studies = _extract_imaging_regex(text)

    return _normalize({
        "patient_name": patient_name,
        "age": age,
        "sex": sex,
        "symptoms": symptoms,
        "diseases": diseases,
        "clinical_values": cv,
        "lab_results": lab_results,
        "imaging_studies": imaging_studies,
    })


def _extract_lab_results_regex(text: str) -> list[dict[str, Any]]:
    """Parse common blood-test lines from plain-text lab reports."""
    labs: list[dict[str, Any]] = []
    seen: set[str] = set()
    patterns: list[tuple[str, str, str, str]] = [
        (r"(?:hba1c|hemoglobin a1c)[:\s]*(\d+(?:\.\d+)?)\s*%?", "HbA1c", "%", "4-6"),
        (r"(?:fasting\s+)?glucose[:\s]*(\d+(?:\.\d+)?)\s*(?:mg/dl)?", "Glucose", "mg/dL", "70-100"),
        (r"(?:wbc|white blood cell(?: count)?|cbc wbc)[:\s]*(\d+(?:\.\d+)?)\s*(?:k/u?l)?", "CBC WBC", "K/uL", "4-11"),
        (r"(?:hemoglobin|hgb)[:\s]*(\d+(?:\.\d+)?)\s*(?:g/dl)?", "Hemoglobin", "g/dL", "12-16"),
        (r"(?:platelet(?: count)?|plt)[:\s]*(\d+(?:\.\d+)?)\s*(?:k/u?l)?", "Platelets", "K/uL", "150-400"),
        (r"creatinine[:\s]*(\d+(?:\.\d+)?)\s*(?:mg/dl)?", "Creatinine", "mg/dL", "0.6-1.2"),
        (r"lactate[:\s]*(\d+(?:\.\d+)?)\s*(?:mmol/l)?", "Lactate", "mmol/L", "<2.0"),
        (r"(?:sodium|na\+?)[:\s]*(\d+(?:\.\d+)?)\s*(?:meq/l|mmol/l)?", "Sodium", "mEq/L", "136-145"),
        (r"(?:potassium|k\+?)[:\s]*(\d+(?:\.\d+)?)\s*(?:meq/l|mmol/l)?", "Potassium", "mEq/L", "3.5-5.0"),
    ]
    for pattern, name, unit, normal in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m and name not in seen:
            seen.add(name)
            labs.append(
                {
                    "name": name,
                    "result_value": m.group(1),
                    "unit": unit,
                    "normal_range": normal,
                    "date": None,
                }
            )
    return labs


def _extract_imaging_regex(text: str) -> list[dict[str, Any]]:
    """Parse CT / X-ray / MRI mentions from imaging reports."""
    studies: list[dict[str, Any]] = []
    text_lower = text.lower()
    findings = ""
    for label in ("impression", "findings", "conclusion", "result"):
        m = re.search(rf"{label}[:\s]+(.{{10,240}}?)(?:\n\n|\n[A-Z]|$)", text, re.IGNORECASE | re.DOTALL)
        if m:
            findings = re.sub(r"\s+", " ", m.group(1)).strip()
            break

    if re.search(r"\bct\b.*\bchest\b|\bchest\s+ct\b|\bct\s+chest\b", text_lower):
        studies.append(
            {
                "name": "CT chest",
                "modality": "CT",
                "findings": findings or "Chest CT report uploaded.",
                "date": None,
            }
        )
    elif "ct scan" in text_lower or re.search(r"\bcomputed tomography\b", text_lower):
        studies.append(
            {
                "name": "CT scan",
                "modality": "CT",
                "findings": findings or "CT report uploaded.",
                "date": None,
            }
        )
    elif re.search(r"\bchest x-?ray\b|\bx-?ray chest\b|\bcxr\b", text_lower):
        studies.append(
            {
                "name": "Chest X-ray",
                "modality": "X-ray",
                "findings": findings or "Chest radiograph report uploaded.",
                "date": None,
            }
        )
    elif re.search(r"\bmri\b", text_lower):
        studies.append(
            {
                "name": "MRI",
                "modality": "MRI",
                "findings": findings or "MRI report uploaded.",
                "date": None,
            }
        )
    return studies


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_lab_results(raw: list) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        val = item.get("result_value")
        if val is None:
            val = item.get("value")
        if not name or val is None or str(val).strip() == "":
            continue
        out.append(
            {
                "name": name,
                "result_value": str(val).strip(),
                "unit": (item.get("unit") or "").strip(),
                "normal_range": (item.get("normal_range") or "").strip(),
                "date": item.get("date"),
            }
        )
    return out


def _normalize_imaging_studies(raw: list) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or item.get("modality") or "").strip()
        if not name:
            continue
        out.append(
            {
                "name": name,
                "modality": (item.get("modality") or "").strip(),
                "findings": (item.get("findings") or item.get("impression") or "").strip(),
                "date": item.get("date"),
            }
        )
    return out


def _normalize(data: dict) -> dict[str, Any]:
    return {
        "patient_name": data.get("patient_name"),
        "age": data.get("age"),
        "sex": data.get("sex"),
        "symptoms": [
            s.lower().strip() for s in (data.get("symptoms") or []) if s
        ],
        "diseases": [d.strip() for d in (data.get("diseases") or []) if d],
        "clinical_values": {
            k: v
            for k, v in (data.get("clinical_values") or {}).items()
            if v is not None
        },
        "lab_results": _normalize_lab_results(data.get("lab_results") or []),
        "imaging_studies": _normalize_imaging_studies(data.get("imaging_studies") or []),
    }
