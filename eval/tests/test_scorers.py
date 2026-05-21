"""Pytest coverage for the eval scorers — no agent calls, all pure-function."""

from __future__ import annotations

from eval import scorers


def test_extract_category_explicit():
    assert scorers.extract_category("ACR category Cat1, dispatching now.") == "Cat1"
    assert scorers.extract_category("Stable findings — Cat3, no action.") == "Cat3"


def test_extract_category_implicit_cat3():
    assert scorers.extract_category("No critical communication needed.") == "Cat3"


def test_extract_category_none():
    assert scorers.extract_category("") is None
    assert scorers.extract_category("Hello world.") is None


def test_extract_tools_in_order_unique():
    text = (
        "I called fetch_report_fhir_tool, then resolve_provider_tool, "
        "then dispatch_communication_tool and finally track_acknowledgment_tool. "
        "(Earlier I had also tried fetch_report_fhir_tool but it was the same call.)"
    )
    tools = scorers.extract_tools(text)
    assert tools == [
        "fetch_report_fhir_tool",
        "resolve_provider_tool",
        "dispatch_communication_tool",
        "track_acknowledgment_tool",
    ]


def test_extract_deadline_minutes():
    assert scorers.extract_deadline_minutes("Opening a 60-minute ack window.") == 60
    assert scorers.extract_deadline_minutes("Set the deadline to 24 hours.") == 1440
    assert scorers.extract_deadline_minutes("Done.") is None


def test_score_classification_perfect():
    pairs = [("Cat1", "Cat1"), ("Cat2", "Cat2"), ("Cat3", "Cat3")]
    rep = scorers.score_classification(pairs)
    assert rep.accuracy == 1.0
    assert rep.per_class["Cat1"]["precision"] == 1.0
    assert rep.per_class["Cat1"]["recall"] == 1.0


def test_score_classification_undercall_is_worst_case():
    # Cat1 under-called as Cat2 is the dangerous failure mode for clinical safety.
    pairs = [("Cat1", "Cat2"), ("Cat1", "Cat1"), ("Cat2", "Cat2")]
    rep = scorers.score_classification(pairs)
    assert rep.confusion["Cat1"]["Cat2"] == 1
    assert rep.per_class["Cat1"]["recall"] == 0.5


def test_score_trajectory_perfect():
    expected = ["fetch_report_fhir_tool", "resolve_provider_tool", "dispatch_communication_tool"]
    actual = list(expected)
    s = scorers.score_trajectory(expected, actual)
    assert s.selection_f1 == 1.0
    assert s.order_match is True
    assert s.missing == [] and s.extra == []


def test_score_trajectory_wrong_order_still_caught():
    expected = ["a_tool", "b_tool", "c_tool"]
    actual = ["c_tool", "a_tool", "b_tool"]
    s = scorers.score_trajectory(expected, actual)
    assert s.selection_f1 == 1.0
    assert s.order_match is False


def test_score_trajectory_missing_tool():
    expected = ["a_tool", "b_tool"]
    actual = ["a_tool"]
    s = scorers.score_trajectory(expected, actual)
    assert s.missing == ["b_tool"]
    assert s.selection_f1 < 1.0


def test_score_deadline_cat1():
    assert scorers.score_deadline("Cat1", 60) is True
    assert scorers.score_deadline("Cat1", 30) is True
    assert scorers.score_deadline("Cat1", 90) is False
    assert scorers.score_deadline("Cat1", None) is False


def test_score_deadline_cat3_should_not_dispatch():
    assert scorers.score_deadline("Cat3", None) is True
    assert scorers.score_deadline("Cat3", 60) is False


def test_score_reliability_pass_at_k():
    runs = {
        "c1": [True, True, True],     # all pass
        "c2": [True, False, True],    # one flake -> pass^3 fails
        "c3": [False, False, False],  # all fail
    }
    rel = scorers.score_reliability(runs)
    assert rel.k == 3
    assert rel.per_case_pass_rate["c1"] == 1.0
    assert abs(rel.per_case_pass_rate["c2"] - 2 / 3) < 1e-6
    assert rel.pass_at_k == 1 / 3  # only c1 passed all 3


def test_score_state_cat3_treats_absence_as_pass():
    s = scorers.score_state("Audit returned no Communication, no Task.", "Cat3")
    assert s.communication_present is True
    assert s.task_present is True


def test_aggregate_empty():
    summary = scorers.aggregate([])
    assert summary.n_cases == 0
    assert summary.overall_pass_rate == 0.0
