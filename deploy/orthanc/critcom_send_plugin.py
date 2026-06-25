"""Orthanc plugin: 'Send to CritCom' button in the classic Explorer study page."""

import json
import os
import urllib.parse
import urllib.request
import uuid

import orthanc

AGENT = os.environ.get("CRITCOM_AGENT_URL", "http://critcom-agent:8001").rstrip("/")
API_KEY = os.environ.get("CRITCOM_API_KEY", "")
FHIR = os.environ.get("CRITCOM_FHIR_URL", "http://hapi-fhir:8080/fhir").rstrip("/")

BUTTON_JS = """
function critcomShow(title, body, loading) {
  $('#critcom-overlay').remove();
  var card = $("<div>").css({background:'#fff',maxWidth:'580px',width:'90%',borderRadius:'14px',
    padding:'22px 26px',fontFamily:'Inter,Helvetica,Arial,sans-serif',boxShadow:'0 20px 60px rgba(0,0,0,.45)'});
  $("<div>").text(title).css({margin:'0 0 14px',color:'#0f172a',fontSize:'20px',fontWeight:'800'}).appendTo(card);
  $("<pre>").text(body).css({whiteSpace:'pre-wrap',fontFamily:'ui-monospace,Menlo,monospace',fontSize:'13.5px',
    lineHeight:'1.5',color:'#1e293b',background:'#f4f7fb',border:'1px solid #d3deec',borderRadius:'8px',
    padding:'14px 16px',margin:0}).appendTo(card);
  if (!loading) {
    $("<button>").text('Close').css({marginTop:'16px',background:'#0284c7',color:'#fff',border:0,
      borderRadius:'8px',padding:'10px 22px',fontWeight:'700',fontSize:'15px',cursor:'pointer'})
      .appendTo(card).click(function(){ $('#critcom-overlay').remove(); });
  }
  $("<div>").attr('id','critcom-overlay').css({position:'fixed',top:0,left:0,right:0,bottom:0,
    background:'rgba(15,23,42,.6)',zIndex:99999,display:'flex',alignItems:'center',justifyContent:'center'})
    .append(card).appendTo('body');
}
$('#study').live('pagebeforeshow', function() {
  if ($('#critcom-send-btn').length > 0) return;
  var studyId = $.mobile.pageData.uuid;
  var b = $('<a>').attr('id','critcom-send-btn').attr('href','#')
      .attr('data-role','button').attr('data-icon','action').attr('data-theme','b')
      .text('Send to CritCom');
  var anchor = $('#study-delete').length ? $('#study-delete') : $('#study-info');
  b.insertBefore(anchor);
  $('#study').trigger('create');
  b.click(function(e) {
    e.preventDefault();
    var f = window.prompt('Signed findings / impression for this study:');
    if (!f) return;
    critcomShow('CritCom — running the workflow…',
      'Classifying the finding and running the critical-results pipeline.\\nThis takes about 20-30 seconds…', true);
    $.ajax({ url: '../critcom-send/' + studyId, type: 'POST', data: f,
      success: function(s){ critcomShow('CritCom — workflow complete', s, false); },
      error: function(x){ critcomShow('CritCom — error', (x.responseText || x.statusText), false); } });
  });
});
"""

ACR_LABEL = {
    "Cat1": "Cat1 — IMMEDIATE (notify within 60 minutes)",
    "Cat2": "Cat2 — URGENT (notify within 24 hours)",
    "Cat3": "Cat3 — ROUTINE (no critical communication)",
}


def _fhir_sr(patient_id):
    url = FHIR + "/ServiceRequest?" + urllib.parse.urlencode(
        {"subject": "Patient/" + patient_id, "_count": "1"})
    req = urllib.request.Request(url, headers={"Accept": "application/fhir+json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read().decode())
    ent = data.get("entry") or []
    return ent[0]["resource"]["id"] if ent else None


def _call_agent(prompt):
    payload = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "message/send",
               "params": {"message": {"role": "user", "messageId": str(uuid.uuid4()),
                                      "contextId": str(uuid.uuid4()),
                                      "parts": [{"kind": "text", "text": prompt}]}}}
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["X-API-Key"] = API_KEY
    req = urllib.request.Request(AGENT + "/", data=json.dumps(payload).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=300) as r:
        res = json.loads(r.read().decode()).get("result", {})

    acr = provider = comm = task = None
    for e in res.get("history", []):
        if e.get("role") != "agent":
            continue
        for p in e.get("parts", []):
            if p.get("kind") != "data":
                continue
            d = p.get("data", {})
            name = d.get("name")
            args = d.get("args") or {}
            resp = d.get("response") if isinstance(d.get("response"), dict) else {}
            if args.get("acr_category"):
                acr = args["acr_category"]
            if name == "resolve_provider_tool":
                provider = resp.get("name") or provider
            if name == "dispatch_communication_tool":
                comm = resp.get("communication_id") or comm
            if name == "track_acknowledgment_tool" and resp.get("task_id"):
                task = resp.get("task_id")

    lines = ["CLASSIFIED:  " + ACR_LABEL.get(acr, acr or "—"), ""]
    if acr == "Cat3":
        lines.append("WORKFLOW STEPS")
        n = 1
        if provider:
            lines.append("  %d. Resolved ordering physician      ->  %s" % (n, provider)); n += 1
        if comm:
            lines.append("  %d. Logged routine review (no alert) ->  Communication %s" % (n, comm)); n += 1
        lines += ["", "Recorded in the patient chart (FHIR) for tracking.",
                  "No notification sent and no acknowledgment timer — finding is routine."]
    elif provider or comm or task:
        lines.append("WORKFLOW STEPS")
        n = 1
        if provider:
            lines.append("  %d. Resolved ordering physician      ->  %s" % (n, provider)); n += 1
        if comm:
            lines.append("  %d. Sent critical-result notification ->  Communication %s" % (n, comm)); n += 1
        if task:
            lines.append("  %d. Opened acknowledgment timer       ->  Task %s" % (n, task)); n += 1
        lines += ["", "Recorded in the patient chart (FHIR).",
                  "Escalates to on-call if not acknowledged in time."]
    else:
        lines.append("No critical communication needed — workflow stopped here.")
    return "\n".join(lines)


def on_send(output, uri, **request):
    if request.get("method") != "POST":
        output.SendMethodNotAllowed("POST")
        return
    study_id = request["groups"][0]
    findings = (request.get("body") or b"").decode("utf-8", "replace").strip()
    if not findings:
        output.AnswerBuffer("No findings provided.", "text/plain")
        return
    try:
        study = json.loads(orthanc.RestApiGet("/studies/" + study_id))
        tags = study.get("MainDicomTags", {})
        ptags = study.get("PatientMainDicomTags", {})
        accession = tags.get("AccessionNumber", "")
        patient_id = ptags.get("PatientID", "")
        study_desc = tags.get("StudyDescription", "CT study")
        sr_id = _fhir_sr(patient_id)
        if not sr_id:
            output.AnswerBuffer("No FHIR ServiceRequest found for patient " + patient_id, "text/plain")
            return
        prompt = (
            "A radiologist has signed a radiology report and the critical result must be handled.\n"
            "patient_id: %s\nservice_request_id: %s\nstudy: %s (accession %s)\n"
            "SIGNED FINDINGS:\n%s\n\n"
            "Classify the ACR category (Cat1, Cat2, or Cat3) from these findings.\n"
            "If Cat1 or Cat2, do all three steps, passing every argument explicitly:\n"
            "1. resolve_provider_tool(service_request_id).\n"
            "2. dispatch_communication_tool(service_request_id, patient_id, recipient_practitioner_id from step 1, acr_category, finding_summary).\n"
            "3. track_acknowledgment_tool(action='create', communication_id from step 2, practitioner_id from step 1, patient_id, timeout_minutes=60 for Cat1 or 1440 for Cat2).\n"
            "If Cat3 (routine), still LOG it for tracking — do not stay silent:\n"
            "1. resolve_provider_tool(service_request_id).\n"
            "2. dispatch_communication_tool(service_request_id, patient_id, recipient_practitioner_id from step 1, acr_category='Cat3', finding_summary='Routine - reviewed, no critical communication required').\n"
            "Do NOT create an acknowledgment task for Cat3. Confirm each step."
        ) % (patient_id, sr_id, study_desc, accession, findings)
        output.AnswerBuffer(_call_agent(prompt), "text/plain")
    except Exception as e:  # noqa: BLE001
        output.SendHttpStatus(500, str(e).encode())


orthanc.RegisterRestCallback("/critcom-send/(.*)", on_send)
orthanc.ExtendOrthancExplorer(BUTTON_JS)
