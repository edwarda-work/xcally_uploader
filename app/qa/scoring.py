"""Scorecard engine, following the QA lead's sheets.

- Each metric has a weight (each scorecard adds up to 100) and may belong to a
  domain; the Collections scorecard has 5 domains weighted 60/10/10/10/10.
- Each metric is graded 5, 3 or 1, or N/A when it does not apply to the call.
- The score is the points earned over the weight of the metrics that apply.
- Critical fail (chosen by the evaluator) makes the total 0%. A metric marked
  auto_fail does the same when it is graded 1 (the chat rule that missing
  Accuracy & Compliance or Resolution & Ownership fails the interaction).
- Overall fails counts the graded metrics that scored below 5, ignoring N/A.
- Each domain also gets its own percent, for coaching on the weakest area.

The full scorecard and grades stay local. xCALLY will receive only an integer
`rating` and a `commentNote` summary on the VoiceRecording.
"""

from __future__ import annotations

GRADES = {"5": 5, "3": 3, "1": 1}
NOT_APPLICABLE = "na"

# Built-in starting points, copied from the QA lead's sheet (weights in %).
COLLECTIONS_DOMAINS = {
    "Active Repayment Pursuit": [
        ("payment_facilitation", "Payment Facilitation", 10),
        ("commitment_quality", "Commitment Quality", 15),
        ("amount_due_date", "Amount and Due Date Stated", 10),
        ("objection_challenge", "Objection Challenge", 5),
        ("ptp_attempt_quality", "PTP Attempt Quality", 15),
        ("ladder_utilization", "Ladder Utilization", 5),
    ],
    "Contextual Intelligence": [
        ("profile_history", "Profile/History Awareness", 4),
        ("dpd_alignment", "DPD Alignment", 2),
        ("live_signal_parsing", "Live Signal Parsing (acting on information on the call)", 4),
    ],
    "Compliance & Privacy": [
        ("identity_verification", "Identity Verification", 3),
        ("third_party_protection", "Third-Party Data Protection", 3),
        ("consequence_language", "Approved Consequence Language", 2),
        ("complaint_handling", "Complaint Handling", 2),
    ],
    "Call Continuity": [
        ("accurate_documentation", "Accurate Documentation", 4),
        ("outcome_recording", "Outcome Recording", 4),
        ("follow_up_clarity", "Follow-Up Clarity", 2),
    ],
    "Customer Communication": [
        ("clarity", "Clarity", 2),
        ("confidence_control", "Confidence and Control", 2),
        ("empathy", "Empathy", 2),
        ("active_listening", "Active Listening", 2),
        ("professionalism", "Professionalism", 2),
    ],
}

# Built-in starting points, copied from the QA lead's sheets (weights in %).
TEMPLATES = {
    "collections": {
        "name": "Collections QA",
        "items": [{"key": key, "label": label, "weight": weight, "domain": domain}
                  for domain, metrics in COLLECTIONS_DOMAINS.items() for key, label, weight in metrics],
    },
    "chat": {
        "name": "Chat QA",
        "items": [
            {"key": "accuracy_compliance", "label": "Accuracy & Compliance", "weight": 30, "auto_fail": True},
            {"key": "resolution_ownership", "label": "Resolution & Ownership", "weight": 25, "auto_fail": True},
            {"key": "personalisation_tone", "label": "Personalisation & Tone", "weight": 20, "auto_fail": True},
            {"key": "communication", "label": "Communication Quality", "weight": 15},
            {"key": "writing", "label": "Writing Quality", "weight": 10},
        ],
    },
}


def validate_items(items: list[dict]) -> list[dict]:
    """Normalise scorecard metrics: unique key, label, positive weight, auto_fail flag.

    Older scorecards used "critical" for the same flag; it is read as auto_fail.
    """
    if not items:
        raise ValueError("A scorecard needs at least one item.")
    seen: set[str] = set()
    clean = []
    for item in items:
        key = str(item.get("key", "")).strip()
        label = str(item.get("label", "")).strip()
        weight = item.get("weight", 1)
        if not key or not label:
            raise ValueError("Every scorecard item needs a key and a label.")
        if key in seen:
            raise ValueError(f"Duplicate scorecard item key: {key}")
        if not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight <= 0:
            raise ValueError(f"Item {key} must have a positive weight.")
        seen.add(key)
        auto_fail = bool(item.get("auto_fail", item.get("critical", False)))
        domain = str(item.get("domain") or "").strip() or None
        clean.append({"key": key, "label": label, "weight": weight, "auto_fail": auto_fail, "domain": domain})
    return clean


def grade_points(grade: int, weight: float) -> float:
    """Points a metric earns for its grade, out of its weight.

    From the sheet: 5 = full weight, 3 = half weight, 1 = "1", read as a
    fifth of the weight (1 out of 5).
    """
    if grade == 5:
        return weight
    if grade == 3:
        return weight / 2
    if grade == 1:
        return weight / 5
    raise ValueError(f"Unknown grade: {grade}")


def score(items: list[dict], answers: dict[str, str], critical_fail: bool = False) -> dict:
    """Score one evaluation.

    answers maps every item key to "5", "3", "1" or "na". Returns:
    percent (0 when critically failed, None when every metric is N/A),
    raw_percent (the score before any critical fail), critical_failed,
    auto_failed (keys of auto_fail metrics graded 1), overall_fails and domains
    (name, weight, percent per domain, in scorecard order; empty without domains).
    """
    missing = [item["key"] for item in items if item["key"] not in answers]
    if missing:
        raise ValueError(f"Unanswered items: {', '.join(missing)}")
    unknown = set(answers) - {item["key"] for item in items}
    if unknown:
        raise ValueError(f"Answers for items not on this scorecard: {', '.join(sorted(unknown))}")
    invalid = [key for key, value in answers.items() if value not in GRADES and value != NOT_APPLICABLE]
    if invalid:
        raise ValueError(f"Grades must be 5, 3, 1 or na: {', '.join(invalid)}")

    graded = [(item, GRADES[answers[item["key"]]]) for item in items if answers[item["key"]] != NOT_APPLICABLE]
    possible = sum(item["weight"] for item, _ in graded)
    earned = sum(grade_points(grade, item["weight"]) for item, grade in graded)
    raw_percent = round(earned / possible * 100, 1) if possible else None
    auto_failed = [item["key"] for item, grade in graded if item["auto_fail"] and grade == 1]
    critical_failed = bool(critical_fail) or bool(auto_failed)
    return {
        "percent": 0.0 if critical_failed and raw_percent is not None else raw_percent,
        "raw_percent": raw_percent,
        "critical_failed": critical_failed,
        "auto_failed": auto_failed,
        "overall_fails": sum(1 for _, grade in graded if grade < 5),
        "domains": domain_scores(items, answers),
    }


def domain_scores(items: list[dict], answers: dict[str, str]) -> list[dict]:
    """Percent per domain over its graded metrics; None when all are N/A."""
    order: dict[str, dict] = {}
    for item in items:
        if not item.get("domain"):
            continue
        domain = order.setdefault(item["domain"], {"name": item["domain"], "weight": 0, "earned": 0.0, "possible": 0})
        domain["weight"] += item["weight"]
        if answers[item["key"]] != NOT_APPLICABLE:
            domain["possible"] += item["weight"]
            domain["earned"] += grade_points(GRADES[answers[item["key"]]], item["weight"])
    return [{"name": d["name"], "weight": d["weight"],
             "percent": round(d["earned"] / d["possible"] * 100, 1) if d["possible"] else None}
            for d in order.values()]


def to_xcally_rating(percent: float | None, critical_failed: bool) -> int | None:
    """Convert a local evaluation result to the integer stored in VoiceRecording.rating."""
    # TODO: decide the xCALLY rating scale with the QA lead before sync is built.
    raise NotImplementedError


def comment_note(scorecard_name: str, percent: float | None, critical_failed: bool, comment: str) -> str:
    """Short human-readable summary written to VoiceRecording.commentNote."""
    result = "N/A" if percent is None else f"{percent:g}%"
    parts = [f"QA {scorecard_name}: {result}"]
    if critical_failed:
        parts.append("CRITICAL FAIL")
    if comment.strip():
        parts.append(comment.strip())
    return " | ".join(parts)
