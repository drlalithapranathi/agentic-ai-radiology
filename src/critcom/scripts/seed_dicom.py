"""critcom-seed-dicom — write DICOM Modality Worklist (.wl) files to Orthanc.

Env: CRITCOM_DICOM_WORKLIST_DIR (default ./orthanc-worklists),
     CRITCOM_ORTHANC_URL / USER / PASSWORD.
"""

from __future__ import annotations

import os
import pathlib
import sys
from datetime import datetime

import httpx
import structlog

from critcom.scripts._demo_data import DEMO_ORDERS

log = structlog.get_logger(__name__)


def _build_worklist_dataset(entry: dict):
    from pydicom.dataset import Dataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian, generate_uid

    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.31"
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    file_meta.ImplementationClassUID = generate_uid()

    ds = Dataset()
    ds.file_meta = file_meta
    ds.is_little_endian = True
    ds.is_implicit_VR = False

    today = datetime.now()
    ds.PatientName = entry["patient_name"]
    ds.PatientID = entry["patient_id"]
    ds.PatientBirthDate = entry["birth"]
    ds.PatientSex = entry["sex"]
    ds.AccessionNumber = entry["accession"]
    # Worklist UID must differ from the image study UID, or Orthanc's worklist
    # housekeeper deletes this entry as "performed" once the images exist.
    # The UI links worklist -> image by accession, not by this UID.
    ds.StudyInstanceUID = entry["study_uid"].replace(".498.5", ".498.1")
    ds.RequestedProcedureID = entry["accession"]
    ds.RequestedProcedureDescription = entry["study_desc"]
    ds.RequestedProcedurePriority = entry["priority"]
    ds.Modality = "CT"

    sps = Dataset()
    sps.ScheduledStationAETitle = "MODALITY1"
    sps.ScheduledProcedureStepStartDate = today.strftime("%Y%m%d")
    sps.ScheduledProcedureStepStartTime = today.strftime("%H%M%S")
    sps.Modality = "CT"
    sps.ScheduledPerformingPhysicianName = ""
    sps.ScheduledProcedureStepDescription = entry["study_desc"]
    sps.ScheduledProcedureStepID = entry["accession"]
    sps.ScheduledProcedureStepStatus = "SCHEDULED"
    ds.ScheduledProcedureStepSequence = [sps]
    return ds


def main() -> None:
    try:
        import pydicom  # noqa: F401
        from pydicom import dcmwrite
    except ImportError:
        print("ERROR: pydicom not installed. Run: pip install pydicom pynetdicom", file=sys.stderr)
        sys.exit(1)

    out_dir = pathlib.Path(os.getenv("CRITCOM_DICOM_WORKLIST_DIR", "./orthanc-worklists"))
    out_dir.mkdir(parents=True, exist_ok=True)

    written = []
    for entry in DEMO_ORDERS:
        ds = _build_worklist_dataset(entry)
        out_path = out_dir / f"{entry['accession']}.wl"
        dcmwrite(str(out_path), ds, write_like_original=False)
        written.append(out_path)
        log.info("seed_dicom.wrote", file=str(out_path), accession=entry["accession"], priority=entry["priority"])

    print(f"✓ Wrote {len(written)} worklist files to {out_dir}/")
    for p in written:
        print(f"  - {p.name}")


if __name__ == "__main__":
    main()