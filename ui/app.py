"""CritCom demo UI — a single-screen Streamlit control room.

Pick a scenario and watch the real tool-by-tool pipeline, FHIR records read
live from HAPI, an inline CT image from Orthanc, and close the loop with an
acknowledge button.
"""

from __future__ import annotations

import html
import json
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

SCENARIOS: dict[str, dict] = {
    "Cat1 — Aortic dissection": {
        "acr": "Cat1", "icon": "🫀", "prompt": "Process DiagnosticReport dr-001",
        "service_request_id": "sr-001", "study_uid": "1.2.826.0.1.3680043.8.498.50000000001",
        "blurb": "Type A aortic dissection → notify Dr. Chen → 60-min Cat1 ack Task."},
    "Cat1 — Brain hemorrhage (ICH)": {
        "acr": "Cat1", "icon": "🧠", "prompt": "Process DiagnosticReport dr-003",
        "service_request_id": "sr-003", "study_uid": "1.2.826.0.1.3680043.8.498.50000000003",
        "blurb": "Hypertensive intracranial hemorrhage (head CT) — not aortic-specific."},
    "Cat2 — Subsegmental PE": {
        "acr": "Cat2", "icon": "🫁", "prompt": "Process DiagnosticReport dr-002",
        "service_request_id": "sr-002", "study_uid": "1.2.826.0.1.3680043.8.498.50000000002",
        "blurb": "Urgent (not immediate) PE → 24-hour deadline tier (vs Cat1's 60 min)."},
    "Cat3 — Stable finding (STOP)": {
        "acr": "Cat3", "icon": "🟢", "prompt": "Process DiagnosticReport dr-004",
        "service_request_id": "sr-004", "study_uid": "1.2.826.0.1.3680043.8.498.50000000004",
        "blurb": "Stable cholelithiasis → agent classifies Cat3 and stops. No notification."},
    "DICOM — Saddle PE (00007)": {
        "acr": "Cat1", "icon": "🩻",
        "prompt": ("Process accession_number 00007 from the DICOM worklist. Retrieve the signed "
                   "findings via fetch_radiologist_findings_tool and complete the full "
                   "critical-results workflow."),
        "service_request_id": "sr-001", "study_uid": "1.2.826.0.1.3680043.8.498.50000000001",
        "blurb": "DICOM path: worklist (no findings) → report broker → classify → full pipeline."},
    "Escalation — overdue ack": {
        "acr": "Cat2", "icon": "🚨",
        "prompt": ("Check the acknowledgment status of Task task-overdue-001. If it is overdue and "
                   "not yet acknowledged, escalate it. The original case was service_request_id "
                   "sr-002, patient_id patient-002, ACR category Cat2, finding summary: Acute "
                   "pulmonary emboli involving segmental and subsegmental branches of the right "
                   "lower lobe pulmonary artery. The escalation timeout should be 1440 minutes."),
        "service_request_id": "sr-002", "study_uid": "1.2.826.0.1.3680043.8.498.50000000002",
        "blurb": "Overdue Task → mark failed → notify on-call Dr. Reyes → fresh 24h Task."},
    "Audit trail": {
        "acr": "Audit", "icon": "📋",
        "prompt": ("Use query_audit_tool to return the full Communication and Task history for "
                   "service_request_id sr-002."),
        "service_request_id": "sr-002", "study_uid": "1.2.826.0.1.3680043.8.498.50000000002",
        "blurb": "Read-only: every Communication + Task linked to the case."},
}

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
.block-container {padding-top:1.1rem; max-width:1380px; font-size:16px;}
.hero{background:linear-gradient(120deg,#1d4ed8 0%,#06b6d4 100%);color:#fff;padding:28px 34px;
  border-radius:20px;margin-bottom:10px;box-shadow:0 14px 40px rgba(6,182,212,.3);}
.hero h1{margin:0;font-size:38px;font-weight:800;letter-spacing:-.5px;color:#fff;}
.hero p{margin:9px 0 0;opacity:.97;font-size:16.5px;color:#fff;}
.badge{display:inline-block;padding:4px 13px;border-radius:999px;font-size:13px;font-weight:800;color:#fff;letter-spacing:.3px;}
.b-cat1{background:#dc2626;} .b-cat2{background:#ea8a04;} .b-cat3{background:#16a34a;} .b-audit{background:#64748b;} .b-none{background:#64748b;}
.stButton>button{border-radius:11px;font-weight:700;font-size:16px;border:0;background:#0284c7;color:#fff;padding:.6rem 0;transition:.15s;}
.stButton>button:hover{background:#0369a1;transform:translateY(-1px);box-shadow:0 8px 20px rgba(3,105,161,.3);}
[data-testid="stLinkButton"] a{background:#0f172a!important;color:#fff!important;border:0!important;
  border-radius:11px!important;font-weight:800!important;font-size:16px!important;padding:.6rem 0!important;
  box-shadow:0 6px 16px rgba(15,23,42,.28)!important;}
[data-testid="stLinkButton"] a:hover{background:#1e293b!important;color:#fff!important;transform:translateY(-1px);}
[data-testid="stVerticalBlockBorderWrapper"]{border-radius:16px!important;border:1px solid #d3deec!important;
  background:#ffffff!important;box-shadow:0 6px 18px rgba(15,23,42,.08);}
.stTabs [data-baseweb="tab"]{font-weight:700;font-size:17px;color:#0f172a;}
.chips{display:flex;flex-wrap:wrap;gap:13px;margin:10px 0 6px;}
.chip{background:#ffffff;border:1px solid #d3deec;border-radius:15px;padding:13px 20px;min-width:112px;box-shadow:0 3px 10px rgba(15,23,42,.05);}
.chip .k{font-size:12px;color:#5b6b85;text-transform:uppercase;font-weight:700;letter-spacing:.5px;}
.chip .v{font-size:25px;font-weight:800;color:#0f172a;}
.step{border:1px solid #e3eaf4;border-left:4px solid #0284c7;padding:13px 17px;margin:0 0 13px 4px;background:#ffffff;border-radius:0 13px 13px 0;}
.step .h{font-weight:700;color:#0f172a;font-size:16.5px;}
.step .io{font-family:ui-monospace,SFMono-Regular,monospace;font-size:13px;color:#1e293b;background:#f4f7fb;
  border:1px solid #d3deec;border-radius:8px;padding:8px 11px;margin-top:7px;white-space:pre-wrap;word-break:break-word;}
.step .io b{color:#0369a1;}
.fcard{border:1px solid #d3deec;border-radius:14px;padding:14px 17px;margin-bottom:12px;background:#ffffff;box-shadow:0 3px 10px rgba(15,23,42,.05);}
.fcard .t{font-weight:700;color:#0f172a;font-size:16px;} .fcard .s{font-size:13.5px;color:#475569;margin-top:5px;}
.sec{font-weight:800;font-size:21px;color:#0f172a;margin:18px 0 11px;}
.scard-title{font-size:17px;font-weight:700;color:#0f172a;}
.scard-blurb{font-size:14px;color:#475569;margin:9px 0 11px;min-height:58px;}
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


def viewer_link(study_uid: str) -> str:
    try:
        with httpx.Client(timeout=6.0, auth=(ORTHANC_USER, ORTHANC_PW)) as client:
            r = client.post(f"{ORTHANC_INTERNAL.rstrip('/')}/tools/lookup", content=study_uid)
            r.raise_for_status()
            sid = next((h["ID"] for h in r.json() if h.get("Type") == "Study"), None)
        if sid:
            return f"{VIEWER_PUBLIC.rstrip('/')}/ui/app/#/study/{sid}"
    except (httpx.HTTPError, ValueError, KeyError, StopIteration):
        pass
    return f"{VIEWER_PUBLIC.rstrip('/')}/ui/app/"


def ct_preview(study_uid: str):
    try:
        with httpx.Client(timeout=8.0, auth=(ORTHANC_USER, ORTHANC_PW)) as c:
            lk = c.post(f"{ORTHANC_INTERNAL.rstrip('/')}/tools/lookup", content=study_uid)
            lk.raise_for_status()
            sid = next((h["ID"] for h in lk.json() if h.get("Type") == "Study"), None)
            if not sid:
                return None
            inst = c.get(f"{ORTHANC_INTERNAL.rstrip('/')}/studies/{sid}/instances")
            inst.raise_for_status()
            ids = [i["ID"] for i in inst.json()]
            if not ids:
                return None
            pv = c.get(f"{ORTHANC_INTERNAL.rstrip('/')}/instances/{ids[len(ids)//2]}/preview")
            pv.raise_for_status()
            return pv.content
    except (httpx.HTTPError, ValueError, KeyError, StopIteration):
        return None


def render_result(L: dict):
    res, sr = L["res"], L["sr"]
    steps = extract_steps(res["result"])
    f = key_facts(steps)
    st.markdown(f'<div class="sec">▶ {L["name"]} &nbsp;<span class="muted">· {res["elapsed"]:.0f}s · {len(steps)} tool steps</span></div>',
                unsafe_allow_html=True)
    st.markdown('<div class="chips">'
                f'<div class="chip"><div class="k">ACR</div><div class="v">{f["acr"] or "—"}</div></div>'
                f'<div class="chip"><div class="k">Provider</div><div class="v" style="font-size:18px">{f["provider"] or "—"}</div></div>'
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
        st.markdown('<div class="sec">🖼️ CT images (DICOM · Orthanc)</div>', unsafe_allow_html=True)
        img = ct_preview(L["study"])
        if img:
            st.image(img, caption="CT slice rendered from the DICOM study in Orthanc", use_container_width=True)
        else:
            st.caption("No CT preview available for this case.")
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
            '<p>Critical Results Communication Agent — classifies a radiology finding (ACR), notifies the '
            'right physician, tracks acknowledgment in FHIR, and escalates on timeout. '
            'Gemini 2.5 Pro on Vertex · FHIR R4 · DICOM.</p></div>', unsafe_allow_html=True)

hc1, hc2 = st.columns([3, 1])
hc1.markdown('<div class="sec">Pick a scenario — full ACR spectrum, both trigger paths, both outcomes</div>',
             unsafe_allow_html=True)
hc2.link_button("🩻  Open DICOM viewer", VIEWER_PUBLIC.rstrip("/") + "/ui/app/", use_container_width=True)
names = list(SCENARIOS.keys())
selected = None
for r in range(0, len(names), 3):
    cols = st.columns(3)
    for col, name in zip(cols, names[r:r + 3]):
        sc = SCENARIOS[name]
        with col:
            with st.container(border=True):
                st.markdown(f'<span class="badge b-{sc["acr"].lower()}">{sc["acr"]}</span>'
                            f'&nbsp;<span class="scard-title">{sc["icon"]} {name}</span>'
                            f'<div class="scard-blurb">{sc["blurb"]}</div>', unsafe_allow_html=True)
                if st.button("▶  Run", key=f"run_{name}", use_container_width=True):
                    selected = name
if selected:
    sc = SCENARIOS[selected]
    try:
        with st.spinner(f"Running «{selected}» — Gemini 2.5 Pro reasons through each step "
                        "(~20–30 s), then the full trace appears below…"):
            res = call_agent(sc["prompt"])
        st.session_state["last"] = {"res": res, "name": selected, "sr": sc["service_request_id"], "study": sc["study_uid"]}
    except httpx.HTTPError as e:
        st.error(f"Could not reach the agent: {e}")
if "last" in st.session_state:
    st.divider()
    render_result(st.session_state["last"])
