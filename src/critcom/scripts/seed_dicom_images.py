"""critcom-seed-images — load a distinct, viewable CT study per demo patient into Orthanc.

Each order gets its own real pydicom sample CT (offline, no download), re-tagged
to the patient and keyed by the same accession/study UID the worklist uses.

Env: CRITCOM_ORTHANC_URL / USER / PASSWORD.
"""

from __future__ import annotations

import copy
import io
import os
import sys
from datetime import datetime

import httpx
import structlog

from critcom.scripts._demo_data import DEMO_ORDERS

log = structlog.get_logger(__name__)

_SLICES_PER_STUDY = 4


def _save_bytes(ds) -> bytes:
    buf = io.BytesIO()
    try:
        ds.save_as(buf, enforce_file_format=True)
    except TypeError:
        ds.save_as(buf, write_like_original=False)
    return buf.getvalue()


def _build_study(template, entry: dict) -> list[bytes]:
    from pydicom.uid import generate_uid

    series_uid = generate_uid()
    today = datetime.now().strftime("%Y%m%d")
    instances = []
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
        from pydicom import dcmread
        from pydicom.data import get_testdata_file
    except ImportError:
        print("ERROR: pydicom not installed. Run: pip install pydicom", file=sys.stderr)
        sys.exit(1)

    orthanc_url = os.getenv("CRITCOM_ORTHANC_URL", "http://localhost:8042").rstrip("/")
    auth = (os.getenv("CRITCOM_ORTHANC_USER", "orthanc"),
            os.getenv("CRITCOM_ORTHANC_PASSWORD", "orthanc"))

    uploaded = failed = 0
    with httpx.Client(timeout=30.0, auth=auth) as client:
        for attempt in range(20):
            try:
                if client.get(f"{orthanc_url}/system").status_code < 400:
                    break
            except httpx.HTTPError:
                pass
            import time
            time.sleep(3)

        for entry in DEMO_ORDERS:
            template = dcmread(get_testdata_file(entry["image_file"]))
            for blob in _build_study(template, entry):
                try:
                    r = client.post(f"{orthanc_url}/instances", content=blob,
                                    headers={"Content-Type": "application/dicom"})
                    if r.status_code < 400:
                        uploaded += 1
                    else:
                        failed += 1
                        log.warning("seed_images.upload_failed", status=r.status_code, body=r.text[:200])
                except httpx.HTTPError as e:
                    failed += 1
                    log.warning("seed_images.upload_error", error=str(e))
            log.info("seed_images.study_done", patient=entry["patient_id"],
                     accession=entry["accession"], image=entry["image_file"])

    print(f"\n✓ Uploaded {uploaded} instances ({len(DEMO_ORDERS)} studies) to {orthanc_url}")
    if failed:
        print(f"  {failed} instance(s) failed — see logs.")
    for e in DEMO_ORDERS:
        print(f"    - {e['patient_id']} / {e['accession']} ({e['priority']}): {e['image_file']}")


if __name__ == "__main__":
    main()