"""Single source of truth for demo orders: worklist + images both build from this."""

from __future__ import annotations

_STUDY_UID = "1.2.826.0.1.3680043.8.498.5000000000{n}"

DEMO_ORDERS = [
    {
        "accession": "00001", "patient_id": "patient-001",
        "patient_name": "Kowalski^Robert^James", "birth": "19660314", "sex": "M",
        "study_uid": _STUDY_UID.format(n=1), "priority": "EMERGENCY",
        "study_desc": "CT Chest/Abdomen/Pelvis with contrast",
        "series_desc": "Aortic dissection protocol", "image_file": "693_UNCR.dcm",
    },
    {
        "accession": "00002", "patient_id": "patient-002",
        "patient_name": "Nguyen^Linh^Thu", "birth": "19760722", "sex": "F",
        "study_uid": _STUDY_UID.format(n=2), "priority": "STAT",
        "study_desc": "CT Pulmonary Angiography",
        "series_desc": "CTPA", "image_file": "eCT_Supplemental.dcm",
    },
    {
        "accession": "00003", "patient_id": "patient-003",
        "patient_name": "Williams^Dorothy^Mae", "birth": "19511108", "sex": "F",
        "study_uid": _STUDY_UID.format(n=3), "priority": "HIGH",
        "study_desc": "CT Head without contrast",
        "series_desc": "Non-contrast head", "image_file": "CT_small.dcm",
    },
    {
        "accession": "00004", "patient_id": "patient-004",
        "patient_name": "Goldberg^Eleanor", "birth": "19430927", "sex": "F",
        "study_uid": _STUDY_UID.format(n=4), "priority": "ROUTINE",
        "study_desc": "CT Abdomen with contrast",
        "series_desc": "RUQ", "image_file": "explicit_VR-UN.dcm",
    },
]

PRIORITY_RANK = {"EMERGENCY": 0, "STAT": 1, "HIGH": 2, "MEDIUM": 3, "ROUTINE": 4, "LOW": 5}