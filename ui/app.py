"""CritCom demo UI — live priority-sorted worklist with a Send/sign trigger.

Reads the Modality Worklist straight from Orthanc (C-FIND), sorts by priority,
and lets the radiologist type findings and Send — which fires the agent on that
patient's real order. Nothing about the worklist is hardcoded here.
"""

from __future__ import annotations

import html
import json
import logging
import os
import time
import uuid

import httpx
import streamlit as st

AGENT_URL = os.getenv("CRITCOM_UI_AGENT_URL", "http://localhost:8002")
FHIR_URL = os.getenv("CRITCOM_UI_FHIR_URL", "http://localhost:8081/fhir")
ORTHANC_INTERNAL = os.getenv("CRITCOM_UI_ORTHANC_URL", "http://localhost:8042")
VIEWER_PUBLIC = os.getenv("CRITCOM_UI_VIEWER_PUBLIC", "http://localhost:8042")
ORTHANC_USER = os.getenv("CRITCOM_ORTHANC_USER", "orthanc")
ORTHANC_PW = os.getenv("CRITCOM_ORTHANC_PASSWORD", "orthanc")
API_KEY = os.getenv("CRITCOM_API_KEY", "")
DICOM_HOST = os.getenv("CRITCOM_UI_DICOM_HOST", "orthanc")
DICOM_PORT = int(os.getenv("CRITCOM_UI_DICOM_PORT", "4242"))
DICOM_CALLED_AET = os.getenv("CRITCOM_UI_DICOM_AET", "ORTHANC")
DICOM_CALLING_AET = os.getenv("CRITCOM_UI_DICOM_CALLING_AET", "CRITCOMUI")

PRIORITY_RANK = {"EMERGENCY": 0, "STAT": 1, "HIGH": 2, "MEDIUM": 3, "ROUTINE": 4, "LOW": 5}

TOOL_META = {
    "fetch_report_fhir_tool": ("📄", "Fetch report (FHIR)"),
    "fetch_report_dicom_tool": ("🩻", "Fetch DICOM worklist"),
    "fetch_radiologist_findings_tool": ("✍️", "Get signed findings"),
    "resolve_provider_tool": ("👩‍⚕️", "Resolve provider"),
    "dispatch_communication_tool": ("📣", "Dispatch Communication"),
    "track_acknowledgment_tool": ("⏱️", "Track acknowledgment"),
    "escalate_tool": ("🚨", "Escalate to on-call"),
    "query_audit_tool": ("📋", "Query audit trail"),
}

_TRANSIENT = ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED")

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
html, body, [class*="css"], .stMarkdown, .stButton>button, .stTextArea textarea { font-family:'Inter',sans-serif; }
#MainMenu, header, footer {visibility:hidden;}
.stApp{background:#eef3f9;}
.block-container {padding-top:1.1rem; max-width:1320px; font-size:16px;}
.hero{background:linear-gradient(120deg,#1d4ed8 0%,#06b6d4 100%);color:#fff;padding:24px 30px;
  border-radius:18px;margin-bottom:14px;box-shadow:0 14px 40px rgba(6,182,212,.3);}
.hero h1{margin:0;font-size:34px;font-weight:800;letter-spacing:-.5px;color:#fff;}
.hero p{margin:8px 0 0;opacity:.97;font-size:15.5px;color:#fff;}
.badge{display:inline-block;padding:3px 12px;border-radius:999px;font-size:12.5px;font-weight:800;color:#fff;letter-spacing:.4px;}
.b-emergency{background:#991b1b;} .b-stat{background:#dc2626;} .b-high{background:#ea580c;}
.b-medium{background:#d97706;} .b-routine{background:#16a34a;} .b-low{background:#64748b;} .b-none{background:#64748b;}
.b-cat1{background:#dc2626;} .b-cat2{background:#ea8a04;} .b-cat3{background:#16a34a;} .b-audit{background:#64748b;}
.wlrow{display:flex;align-items:center;gap:14px;padding:6px 2px;}
.wlrow .nm{font-size:17px;font-weight:700;color:#0f172a;}
.wlrow .meta{font-size:13.5px;color:#5b6b85;}
.stButton>button{border-radius:11px;font-weight:700;font-size:15.5px;border:0;background:#0284c7;color:#fff;padding:.55rem 0;transition:.15s;}
.stButton>button:hover{background:#0369a1;transform:translateY(-1px);box-shadow:0 8px 20px rgba(3,105,161,.3);}
[data-testid="stLinkButton"] a{background:#0f172a!important;color:#fff!important;border:0!important;
  border-radius:11px!important;font-weight:800!important;font-size:15px!important;padding:.55rem 0!important;}
[data-testid="stVerticalBlockBorderWrapper"]{border-radius:14px!important;border:1px solid #d3deec!important;
  background:#ffffff!important;box-shadow:0 5px 16px rgba(15,23,42,.07);}
.step{border:1px solid #e3eaf4;border-left:4px solid #0284c7;padding:12px 16px;margin:0 0 12px 4px;background:#fff;border-radius:0 12px 12px 0;}
.step .h{font-weight:700;color:#0f172a;font-size:16px;}
.step .io{font-family:ui-monospace,monospace;font-size:12.5px;color:#1e293b;background:#f4f7fb;
  border:1px solid #d3deec;border-radius:8px;padding:7px 10px;margin-top:6px;white-space:pre-wrap;word-break:break-word;}
.step .io b{color:#0369a1;}
.fcard{border:1px solid #d3deec;border-radius:13px;padding:13px 16px;margin-bottom:11px;background:#fff;}
.fcard .t{font-weight:700;color:#0f172a;font-size:15.5px;} .fcard .s{font-size:13px;color:#475569;margin-top:4px;}
.sec{font-weight:800;font-size:20px;color:#0f172a;margin:16px 0 10px;}
.chips{display:flex;flex-wrap:wrap;gap:12px;margin:8px 0 6px;}
.chip{background:#fff;border:1px solid #d3deec;border-radius:14px;padding:12px 18px;min-width:108px;}
.chip .k{font-size:11.5px;color:#5b6b85;text-transform:uppercase;font-weight:700;letter-spacing:.5px;}
.chip .v{font-size:23px;font-weight:800;color:#0f172a;}
.muted{color:#5b6b85;font-weight:500;}
</style>
"""


def _final_text(result: dict) -> str:
    texts = [" ".join(p.get("text", "") for p in e.get("parts", []) if p.get("kind") == "text")
             for e in result.get("history", []) if e.get("role") == "agent"]
    texts = [t for t in texts if t.strip()]
    return texts[-1].strip() if texts else ""


def extract_steps(result: dict) -> list[dict]:
    calls: dict[str, dict] = {}
    order: list[str] = []
    for e in result.get("history", []):
        if e.get("role") != "agent":
            continue
        for p in e.get("parts", []):
            if p.get("kind") != "data":
                continue
            d = p.get("data", {})
            cid = d.get("id")
            if not cid:
                continue
            if cid not in calls:
                calls[cid] = {"name": d.get("name"), "args": None, "response": None}
                order.append(cid)
            if d.get("name"):
                calls[cid]["name"] = d["name"]
            if "args" in d:
                calls[cid]["args"] = d["args"]
            if "response" in d:
                calls[cid]["response"] = d["response"]
    return [calls[c] for c in order]


def _one_line(obj, limit=190) -> str:
    s = json.dumps(obj, default=str) if not isinstance(obj, str) else obj
    s = " ".join(s.split())
    return s if len(s) <= limit else s[:limit] + " …"


def key_facts(steps: list[dict]) -> dict:
    f = {"acr": None, "provider": None, "comm": None, "task": None}
    for s in steps:
        resp = s.get("response") if isinstance(s.get("response"), dict) else {}
        study = resp.get("study") or {}
        f["acr"] = study.get("acr_category") or resp.get("acr_category") or f["acr"]
        if (s.get("args") or {}).get("acr_category"):
            f["acr"] = s["args"]["acr_category"]
        if s["name"] == "resolve_provider_tool":
            f["provider"] = resp.get("name") or f["provider"]
        if s["name"] == "dispatch_communication_tool":
            f["comm"] = resp.get("communication_id") or f["comm"]
        if s["name"] == "track_acknowledgment_tool" and resp.get("task_id"):
            f["task"] = resp.get("task_id")
        if s["name"] == "escalate_tool":
            f["provider"] = resp.get("on_call_provider") or f["provider"]
            f["comm"] = resp.get("new_communication_id") or f["comm"]
            f["task"] = resp.get("new_task_id") or f["task"]
    return f


def call_agent(prompt: str, retries: int = 3) -> dict:
    payload = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "message/send",
               "params": {"message": {"role": "user", "messageId": str(uuid.uuid4()),
                                      "parts": [{"kind": "text", "text": prompt}]}}}
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["X-API-Key"] = API_KEY
    started = time.perf_counter()
    last: dict = {}
    for attempt in range(retries):
        with httpx.Client(timeout=300.0) as client:
            resp = client.post(AGENT_URL.rstrip("/") + "/", json=payload, headers=headers)
        resp.raise_for_status()
        result = resp.json().get("result", {})
        text = _final_text(result)
        last = {"result": result, "text": text, "elapsed": time.perf_counter() - started, "attempts": attempt + 1}
        if not any(t in text for t in _TRANSIENT):
            return last
        time.sleep(4)
    return last


@st.cache_data(ttl=20, show_spinner=False)
def fetch_worklist() -> list[dict]:
    logging.getLogger("pynetdicom").setLevel(logging.WARNING)
    from pydicom.dataset import Dataset
    from pynetdicom import AE
    from pynetdicom.sop_class import ModalityWorklistInformationFind

    ae = AE(ae_title=DICOM_CALLING_AET)
    ae.add_requested_context(ModalityWorklistInformationFind)
    q = Dataset()
    q.PatientName = ""
    q.PatientID = ""
    q.AccessionNumber = ""
    q.RequestedProcedurePriority = ""
    q.RequestedProcedureDescription = ""
    q.StudyInstanceUID = ""
    q.Modality = ""
    sps = Dataset()
    sps.Modality = ""
    sps.ScheduledProcedureStepStartDate = ""
    sps.ScheduledProcedureStepStartTime = ""
    sps.ScheduledProcedureStepStatus = ""
    q.ScheduledProcedureStepSequence = [sps]

    out: list[dict] = []
    assoc = ae.associate(DICOM_HOST, DICOM_PORT, ae_title=DICOM_CALLED_AET)
    if not assoc.is_established:
        return out
    try:
        for status, ident in assoc.send_c_find(q, ModalityWorklistInformationFind):
            if status and status.Status in (0xFF00, 0xFF01) and ident:
                out.append({
                    "accession": str(getattr(ident, "AccessionNumber", "") or ""),
                    "patient_id": str(getattr(ident, "PatientID", "") or ""),
                    "patient_name": str(getattr(ident, "PatientName", "") or "").replace("^", " ").strip(),
                    "priority": str(getattr(ident, "RequestedProcedurePriority", "") or "").upper(),
                    "description": str(getattr(ident, "RequestedProcedureDescription", "") or ""),
                    "study_uid": str(getattr(ident, "StudyInstanceUID", "") or ""),
                })
    finally:
        assoc.release()
    out.sort(key=lambda e: PRIORITY_RANK.get(e["priority"], 99))
    return out


def resolve_sr(patient_id: str) -> str | None:
    base = FHIR_URL.rstrip("/")
    try:
        with httpx.Client(timeout=10.0, headers={"Accept": "application/fhir+json"}) as c:
            r = c.get(f"{base}/ServiceRequest", params={"subject": f"Patient/{patient_id}", "_count": 1})
            r.raise_for_status()
            ent = r.json().get("entry") or []
            if ent:
                return ent[0].get("resource", {}).get("id")
    except httpx.HTTPError:
        pass
    return None


def send_prompt(entry: dict, sr_id: str, findings: str) -> str:
    return (
        "A radiologist has signed a radiology report and the critical result must be handled.\n"
        f"patient_id: {entry['patient_id']}\n"
        f"service_request_id: {sr_id}\n"
        f"study: {entry['description']} (accession {entry['accession']})\n"
        f"SIGNED FINDINGS:\n{findings}\n\n"
        "Classify the ACR category (Cat1, Cat2, or Cat3) from these findings.\n"
        "If Cat1 or Cat2, do all three steps, passing every argument explicitly:\n"
        "1. resolve_provider_tool(service_request_id).\n"
        "2. dispatch_communication_tool(service_request_id, patient_id, "
        "recipient_practitioner_id from step 1, acr_category, finding_summary).\n"
        "3. track_acknowledgment_tool(action='create', communication_id from step 2, "
        "practitioner_id from step 1, patient_id, timeout_minutes=60 for Cat1 or 1440 for Cat2).\n"
        "If Cat3, stop and report that no critical communication is needed. Confirm each step."
    )


def fetch_fhir_records(sr: str) -> dict:
    base = FHIR_URL.rstrip("/")
    with httpx.Client(timeout=10.0, headers={"Accept": "application/fhir+json"}) as client:
        cr = client.get(f"{base}/Communication", params={"based-on": f"ServiceRequest/{sr}", "_sort": "-sent"})
        cr.raise_for_status()
        comms = [e.get("resource", {}) for e in (cr.json().get("entry") or [])]
        tasks = []
        for c in comms:
            if c.get("id"):
                tr = client.get(f"{base}/Task", params={"focus": f"Communication/{c['id']}"})
                tr.raise_for_status()
                tasks.extend(e.get("resource", {}) for e in (tr.json().get("entry") or []))
    return {"communications": comms, "tasks": tasks}


def _study_id_by_accession(accession: str) -> str | None:
    with httpx.Client(timeout=6.0, auth=(ORTHANC_USER, ORTHANC_PW)) as c:
        r = c.post(f"{ORTHANC_INTERNAL.rstrip('/')}/tools/find",
                   json={"Level": "Study", "Query": {"AccessionNumber": accession}})
        r.raise_for_status()
        ids = r.json()
        return ids[0] if ids else None


def viewer_link(accession: str) -> str:
    try:
        sid = _study_id_by_accession(accession)
        if sid:
            return f"{VIEWER_PUBLIC.rstrip('/')}/ui/app/#/study/{sid}"
    except (httpx.HTTPError, ValueError, KeyError):
        pass
    return f"{VIEWER_PUBLIC.rstrip('/')}/ui/app/"


def ct_preview(accession: str):
    try:
        sid = _study_id_by_accession(accession)
        if not sid:
            return None
        with httpx.Client(timeout=8.0, auth=(ORTHANC_USER, ORTHANC_PW)) as c:
            inst = c.get(f"{ORTHANC_INTERNAL.rstrip('/')}/studies/{sid}/instances")
            inst.raise_for_status()
            ids = [i["ID"] for i in inst.json()]
            if not ids:
                return None
            pv = c.get(f"{ORTHANC_INTERNAL.rstrip('/')}/instances/{ids[len(ids)//2]}/preview")
            pv.raise_for_status()
            return pv.content
    except (httpx.HTTPError, ValueError, KeyError):
        return None


def render_result(L: dict):
    res, sr = L["res"], L["sr"]
    steps = extract_steps(res["result"])
    f = key_facts(steps)
    st.markdown(f'<div class="sec">▶ {L["name"]} &nbsp;<span class="muted">· {res["elapsed"]:.0f}s · {len(steps)} tool steps</span></div>',
                unsafe_allow_html=True)
    st.markdown('<div class="chips">'
                f'<div class="chip"><div class="k">ACR</div><div class="v">{f["acr"] or "—"}</div></div>'
                f'<div class="chip"><div class="k">Provider</div><div class="v" style="font-size:17px">{f["provider"] or "—"}</div></div>'
                f'<div class="chip"><div class="k">Communication</div><div class="v">{f["comm"] or "—"}</div></div>'
                f'<div class="chip"><div class="k">Ack Task</div><div class="v">{f["task"] or "—"}</div></div>'
                f'<div class="chip"><div class="k">Tool steps</div><div class="v">{len(steps)}</div></div>'
                '</div>', unsafe_allow_html=True)

    left, right = st.columns([3, 2])
    with left:
        st.markdown('<div class="sec">🔧 What the agent did — step by step</div>', unsafe_allow_html=True)
        timeline = ""
        for i, s in enumerate(steps, 1):
            icon, friendly = TOOL_META.get(s["name"], ("⚙️", s["name"] or "tool"))
            timeline += (f'<div class="step"><div class="h">{i}. {icon} {friendly}</div>'
                         f'<div class="io"><b>in </b>{html.escape(_one_line(s["args"] or {}))}</div>'
                         f'<div class="io"><b>out</b> {html.escape(_one_line(s["response"] if s["response"] is not None else {}))}</div></div>')
        st.markdown(timeline or "<i>Agent answered directly (no tool calls).</i>", unsafe_allow_html=True)
        st.markdown('<div class="sec">🗣️ Agent summary</div>', unsafe_allow_html=True)
        st.success(res["text"] or "(no text returned)")
    with right:
        st.markdown('<div class="sec">🖼️ CT (DICOM · Orthanc)</div>', unsafe_allow_html=True)
        img = ct_preview(L["study"])
        if img:
            st.image(img, caption="CT slice from this study in Orthanc", use_container_width=True)
        else:
            st.caption("No CT preview available.")
        st.link_button("🔎  Open full scrollable viewer (Orthanc)", viewer_link(L["study"]), use_container_width=True)
        if f["task"]:
            if st.button("✅  Provider acknowledges → close the loop", key="ack", use_container_width=True):
                with st.spinner("Recording acknowledgment…"):
                    ack = call_agent(f"Call track_acknowledgment_tool with action='mark_acknowledged' and "
                                     f"task_id='{f['task']}'. The ordering physician has acknowledged the finding.")
                st.session_state["last"]["ack"] = ack["text"]
        if st.session_state.get("last", {}).get("ack"):
            st.success("✅ " + st.session_state["last"]["ack"][:200])
        st.markdown('<div class="sec">🗂️ FHIR records (live from HAPI)</div>', unsafe_allow_html=True)
        try:
            rec = fetch_fhir_records(sr)
            cards = ""
            for c in rec["communications"][:4]:
                cat = (c.get("category") or [{}])[0].get("text", "?")
                bcls = cat.lower() if cat.lower().startswith("cat") else "audit"
                cards += (f'<div class="fcard"><div class="t">📣 Communication {c.get("id","?")} '
                          f'<span class="badge b-{bcls}">{cat}</span></div>'
                          f'<div class="s">{html.escape(((c.get("payload") or [{}])[0].get("contentString","") or "")[:130])}</div>'
                          f'<div class="s">→ {html.escape((c.get("recipient") or [{}])[0].get("reference","?"))}</div></div>')
            for t in rec["tasks"][:4]:
                period = (t.get("restriction") or {}).get("period") or {}
                cards += (f'<div class="fcard"><div class="t">⏱️ Task {t.get("id","?")} · {t.get("status","?")}</div>'
                          f'<div class="s">owner {html.escape((t.get("owner") or {}).get("reference","?"))}</div>'
                          f'<div class="s">deadline {period.get("end","—")}</div></div>')
            st.markdown(cards or '<div class="fcard">No Communication/Task — correct for a Cat3 stop case. ✅</div>',
                        unsafe_allow_html=True)
        except httpx.HTTPError as e:
            st.warning(f"Couldn't read FHIR: {e}")


def _badge_class(priority: str) -> str:
    p = priority.lower()
    return f"b-{p}" if p in ("emergency", "stat", "high", "medium", "routine", "low") else "b-none"


# ---------------------------------------------------------------------------
st.set_page_config(page_title="CritCom — Critical Results Agent", page_icon="🩻", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)


def _require_password():
    pw = os.getenv("CRITCOM_UI_PASSWORD", "")
    if not pw or st.session_state.get("auth_ok"):
        return
    st.markdown("## 🔒 CritCom demo")
    entered = st.text_input("Enter password", type="password")
    if entered == pw:
        st.session_state["auth_ok"] = True
        st.rerun()
    elif entered:
        st.error("Incorrect password.")
    st.stop()


_require_password()
st.markdown('<div class="hero"><h1>🩻 CritCom</h1>'
            '<p>Radiology worklist → read → sign. Signing fires the critical-results agent: '
            'classify (ACR), notify the ordering physician, track acknowledgment, escalate on timeout. '
            'Gemini on Vertex · FHIR R4 · DICOM.</p></div>', unsafe_allow_html=True)

hc1, hc2, hc3 = st.columns([3, 1, 1])
hc1.markdown('<div class="sec">📋 Modality Worklist — live from Orthanc, sorted by priority</div>', unsafe_allow_html=True)
hc2.link_button("🩻  Open DICOM viewer", VIEWER_PUBLIC.rstrip("/") + "/ui/app/", use_container_width=True)
if hc3.button("🔄  Refresh", use_container_width=True):
    fetch_worklist.clear()

try:
    worklist = fetch_worklist()
except Exception as e:  # noqa: BLE001
    worklist = []
    st.error(f"Could not query the worklist from Orthanc: {e}")

if not worklist:
    st.info("No worklist entries found. Seed them with `critcom-seed-dicom` (and `critcom-seed-images`).")

for entry in worklist:
    with st.container(border=True):
        st.markdown(
            f'<div class="wlrow"><span class="badge {_badge_class(entry["priority"])}">{entry["priority"] or "—"}</span>'
            f'<span class="nm">{html.escape(entry["patient_name"])}</span>'
            f'<span class="meta">· {html.escape(entry["description"])} · acc {html.escape(entry["accession"])} '
            f'· {html.escape(entry["patient_id"])}</span></div>',
            unsafe_allow_html=True)
        with st.expander("📝  Read & sign"):
            c1, c2 = st.columns([2, 3])
            with c1:
                img = ct_preview(entry["accession"])
                if img:
                    st.image(img, use_container_width=True)
                st.link_button("🔎 Open in viewer", viewer_link(entry["accession"]), use_container_width=True)
            with c2:
                findings = st.text_area(
                    "Signed findings / impression",
                    key=f"find_{entry['accession']}",
                    height=150,
                    placeholder="Type the radiologist's signed findings here, then Send…",
                )
                if st.button("📨  Send / sign report", key=f"send_{entry['accession']}", use_container_width=True):
                    if not findings.strip():
                        st.warning("Type some findings first.")
                    else:
                        sr_id = resolve_sr(entry["patient_id"])
                        if not sr_id:
                            st.error(f"No FHIR ServiceRequest found for {entry['patient_id']}.")
                        else:
                            with st.spinner("Agent reasoning through the critical-results workflow…"):
                                res = call_agent(send_prompt(entry, sr_id, findings.strip()))
                            st.session_state["last"] = {
                                "res": res, "name": f"{entry['patient_name']} · acc {entry['accession']}",
                                "sr": sr_id, "study": entry["accession"],
                            }

if "last" in st.session_state:
    st.divider()
    render_result(st.session_state["last"])