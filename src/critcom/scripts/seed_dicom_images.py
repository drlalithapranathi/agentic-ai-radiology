"""
critcom-seed-images — load viewable DICOM image studies into Orthanc.

The rest of the stack only ever had Modality Worklist (scheduling) data, so a
DICOM viewer had no pixels to show. This script takes pydicom's bundled sample
CT slice (real CT pixel data, ships with the package — no download, fully
offline) and re-tags it to each demo patient, building a short multi-slice
study per case whose PatientID / AccessionNumber / StudyInstanceUID line up
with the FHIR + worklist seed. Uploads them to Orthanc via the REST API.

The deterministic StudyInstanceUIDs below are what the demo UI deep-links to
in Orthanc's viewer — keep them in sync with ui/app.py.

Usage:
    critcom-seed-images
    # or:
    python -m critcom.scripts.seed_dicom_images

Env vars:
    CRITCOM_ORTHANC_URL       (default: http://localhost:8042)
    CRITCOM_ORTHANC_USER      (default: orthanc)
    CRITCOM_ORTHANC_PASSWORD  (default: orthanc)
"""

from __future__ import annotations

import copy
import io
import os
import sys
from datetime import datetime

import httpx
import structlog

log = structlog.get_logger(__name__)

# StudyInstanceUID base — ".5xxx" namespace so it never collides with the
# worklist UIDs (".1xxx") in seed_dicom.py. The UI links to these exact UIDs.
_STUDY_UID = "1.2.826.0.1.3680043.8.498.5000000000{n}"

# One image study per demo patient. Pixels are identical sample CT data; only
# the labels differ — judges see the workflow + a real CT in the viewer, not a
# diagnosis. Names/DOBs match the worklist + FHIR seed.
IMAGE_STUDIES = [
    {
        "patient_id": "patient-001", "patient_name": "Kowalski^Robert^James",
        "birth": "19660314", "sex": "M", "accession": "ACC0001",
        "study_uid": _STUDY_UID.format(n=1),
        "study_desc": "CT Chest/Abdomen/Pelvis with contrast",
        "series_desc": "Aortic dissection protocol",
    },
    {
        "patient_id": "patient-002", "patient_name": "Nguyen^Linh^Thu",
        "birth": "19760722", "sex": "F", "accession": "ACC0002",
        "study_uid": _STUDY_UID.format(n=2),
        "study_desc": "CT Pulmonary Angiography",
        "series_desc": "CTPA",
    },
    {
        "patient_id": "patient-003", "patient_name": "Williams^Dorothy^Mae",
        "birth": "19511108", "sex": "F", "accession": "ACC0003",
        "study_uid": _STUDY_UID.format(n=3),
        "study_desc": "CT Head without contrast",
        "series_desc": "Non-contrast head",
    },
    {
        "patient_id": "patient-004", "patient_name": "Goldberg^Eleanor",
        "birth": "19430927", "sex": "F", "accession": "ACC0004",
        "study_uid": _STUDY_UID.format(n=4),
        "study_desc": "CT Abdomen with contrast",
        "series_desc": "RUQ",
    },
]

_SLICES_PER_STUDY = 4


def _save_bytes(ds) -> bytes:
    buf = io.BytesIO()
    try:
        ds.save_as(buf, enforce_file_format=True)  # pydicom >= 3.0
    except TypeError:
        ds.save_as(buf, write_like_original=False)  # pydicom 2.x
    return buf.getvalue()


def _build_study(template, entry: dict) -> list[bytes]:
    """Clone the sample slice into a short re-tagged series for one patient."""
    from pydicom.uid import generate_uid

    series_uid = generate_uid()
    today = datetime.now().strftime("%Y%m%d")
    instances: list[bytes] = []

    for i in range(_SLICES_PER_STUDY):
        ds = copy.deepcopy(template)
        ds.PatientID = entry["patient_id"]
        ds.PatientName = entry["patient_name"]
        ds.PatientBirthDate = entry["birth"]
        ds.PatientSex = entry["sex"]
        ds.AccessionNumber = entry["accession"]
        ds.StudyInstanceUID = entry["study_uid"]
        ds.SeriesInstanceUID = series_uid
        ds.StudyDescription = entry["study_desc"]
        ds.SeriesDescription = entry["series_desc"]
        ds.Modality = "CT"
        ds.StudyDate = today
        ds.SeriesNumber = "1"
        ds.InstanceNumber = str(i + 1)
        # Spread the slices in space so the viewer shows a scrollable stack.
        ds.SliceLocation = float(i * 5)
        if "ImagePositionPatient" in ds:
            pos = list(ds.ImagePositionPatient)
            pos[2] = float(i * 5)
            ds.ImagePositionPatient = pos

        sop_uid = generate_uid()
        ds.SOPInstanceUID = sop_uid
        if hasattr(ds, "file_meta") and ds.file_meta is not None:
            ds.file_meta.MediaStorageSOPInstanceUID = sop_uid

        instances.append(_save_bytes(ds))

    return instances


def main() -> None:
    try:
        import pydicom  # noqa: F401
        from pydicom import dcmread
        from pydicom.data import get_testdata_file
    except ImportError:
        print("ERROR: pydicom not installed. Run: pip install pydicom", file=sys.stderr)
        sys.exit(1)

    orthanc_url = os.getenv("CRITCOM_ORTHANC_URL", "http://localhost:8042").rstrip("/")
    auth = (
        os.getenv("CRITCOM_ORTHANC_USER", "orthanc"),
        os.getenv("CRITCOM_ORTHANC_PASSWORD", "orthanc"),
    )

    template = dcmread(get_testdata_file("CT_small.dcm"))

    uploaded = 0
    failed = 0
    with httpx.Client(timeout=30.0, auth=auth) as client:
        # Wait for Orthanc to be reachable (it may still be starting in compose).
        for attempt in range(20):
            try:
                if client.get(f"{orthanc_url}/system").status_code < 400:
                    break
            except httpx.HTTPError:
                pass
            import time
            time.sleep(3)

        for entry in IMAGE_STUDIES:
            for blob in _build_study(template, entry):
                try:
                    r = client.post(
                        f"{orthanc_url}/instances",
                        content=blob,
                        headers={"Content-Type": "application/dicom"},
                    )
                    if r.status_code < 400:
                        uploaded += 1
                    else:
                        failed += 1
                        log.warning("seed_images.upload_failed", status=r.status_code, body=r.text[:200])
                except httpx.HTTPError as e:
                    failed += 1
                    log.warning("seed_images.upload_error", error=str(e))
            log.info(
                "seed_images.study_done",
                patient=entry["patient_id"],
                accession=entry["accession"],
                study_uid=entry["study_uid"],
            )

    print(f"\n✓ Uploaded {uploaded} instances ({len(IMAGE_STUDIES)} studies) to {orthanc_url}")
    if failed:
        print(f"  {failed} instance(s) failed — see logs.")
    print("  StudyInstanceUIDs (for the viewer):")
    for e in IMAGE_STUDIES:
        print(f"    - {e['patient_id']} / {e['accession']}: {e['study_uid']}")


if __name__ == "__main__":
    main()
