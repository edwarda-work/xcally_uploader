"""Automatic QA call sampling.

Rules:
- Each collections agent gets 4 calls: one short, one medium, and one long
  call. The fourth is a second long call when the agent has one, otherwise any
  length, so agents get 2 long calls where possible and never fewer than 1.
- Short is under 2 minutes, medium 2 to 5 minutes inclusive, long over 5 minutes.
- xCALLY stores VoiceRecording.duration in milliseconds (20 ms audio frames,
  confirmed on the live server); it is converted to seconds before grouping.
- Every call counts, except empty recordings (under 1 second, shown as 0:00),
  which have nothing to listen to. A recording that was already evaluated is
  never picked again.
- Only campaigns whose name starts with the chosen market code (e.g. "GH") are
  sampled; calls from other markets or with no campaign are left out.
- Agents are shared out among the QA evaluators so each has a similar workload.

A seed makes the draw reproducible. It is stored with the plan, together with
the thresholds, so the same plan can be checked or rebuilt later.
"""

from __future__ import annotations

import random
import secrets
from collections import defaultdict

SHORT_MAX_SECONDS = 120   # under 2 minutes is short
LONG_MIN_SECONDS = 300    # over 5 minutes is long
CALLS_PER_AGENT = 4
MIN_SECONDS = 1          # under 1 second is an empty recording
REQUIRED_BUCKETS = ("short", "medium", "long")
EXTRA_PREFERENCE = "long"   # extra slots are filled with long calls first


def duration_seconds(recording: dict) -> float | None:
    """Recording length in seconds from xCALLY's millisecond `duration`."""
    value = recording.get("duration")
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return value / 1000


def bucket(duration_seconds: int | float | None) -> str | None:
    """Length group for a recording; None when xCALLY gave no usable duration."""
    if duration_seconds is None or isinstance(duration_seconds, bool) or duration_seconds < 0:
        return None
    if duration_seconds < SHORT_MAX_SECONDS:
        return "short"
    if duration_seconds > LONG_MIN_SECONDS:
        return "long"
    return "medium"


def _call(recording: dict, group: str) -> dict:
    return {
        "recording_id": recording["id"],
        "duration": duration_seconds(recording),
        "bucket": group,
        "campaign": recording.get("queue"),
        "created_at": recording.get("createdAt"),
    }


def sample_agent_calls(recordings: list[dict], rng: random.Random, size: int = CALLS_PER_AGENT) -> dict:
    """Pick one call from each length group, then fill the rest from any group.

    When a group has no calls, the slot is filled from the others and the gap is
    reported in `missing`, so evaluators know the mix rule could not be met.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for recording in sorted(recordings, key=lambda row: row["id"]):
        group = bucket(duration_seconds(recording))
        if group:
            groups[group].append(recording)

    picks: list[dict] = []
    missing: list[str] = []
    for group in REQUIRED_BUCKETS:
        if groups[group]:
            choice = rng.choice(groups[group])
            groups[group].remove(choice)
            picks.append(_call(choice, group))
        else:
            missing.append(group)

    while len(picks) < size and groups[EXTRA_PREFERENCE]:
        choice = rng.choice(groups[EXTRA_PREFERENCE])
        groups[EXTRA_PREFERENCE].remove(choice)
        picks.append(_call(choice, EXTRA_PREFERENCE))
    remaining = [(group, row) for group in REQUIRED_BUCKETS for row in groups[group]]
    extra = rng.sample(remaining, min(size - len(picks), len(remaining)))
    picks.extend(_call(row, group) for group, row in extra)
    picks.sort(key=lambda call: (REQUIRED_BUCKETS.index(call["bucket"]), call["recording_id"]))
    return {"calls": picks, "missing": missing, "short_of_target": size - len(picks)}


def assign_agents(agent_ids: list[int], evaluators: list[str], rng: random.Random) -> dict[str, list[int]]:
    """Share agents out evenly; evaluator workloads differ by at most one agent."""
    clean = sorted({name.strip() for name in evaluators if name and name.strip()})
    if not clean:
        raise ValueError("Add at least one QA evaluator.")
    shuffled = sorted(set(agent_ids))
    rng.shuffle(shuffled)
    assignments: dict[str, list[int]] = {name: [] for name in clean}
    for index, agent_id in enumerate(shuffled):
        assignments[clean[index % len(clean)]].append(agent_id)
    return {name: sorted(agents) for name, agents in assignments.items()}


def build_plan(recordings: list[dict], evaluators: list[str], *, excluded_recording_ids: set[int] = frozenset(),
               agent_names: dict[int, str] | None = None, seed: str | None = None,
               campaign_prefix: str | None = None) -> dict:
    """Build the full plan: evaluator -> agents -> sampled calls each."""
    prefix = (campaign_prefix or "").strip().upper()
    seed = seed or secrets.token_hex(8)
    rng = random.Random(seed)
    names = agent_names or {}

    by_agent: dict[int, list[dict]] = defaultdict(list)
    skipped = {"already_evaluated": 0, "no_agent": 0, "no_duration": 0, "empty": 0, "other_market": 0}
    for recording in recordings:
        if prefix and not str(recording.get("queue") or "").strip().upper().startswith(prefix):
            skipped["other_market"] += 1
        elif recording.get("id") in excluded_recording_ids:
            skipped["already_evaluated"] += 1
        elif type(recording.get("UserId")) is not int:
            skipped["no_agent"] += 1
        elif bucket(duration_seconds(recording)) is None:
            skipped["no_duration"] += 1
        elif duration_seconds(recording) < MIN_SECONDS:
            skipped["empty"] += 1
        else:
            by_agent[recording["UserId"]].append(recording)

    assignments = assign_agents(list(by_agent), evaluators, rng)
    plan = []
    for evaluator, agent_ids in assignments.items():
        agents = []
        for agent_id in agent_ids:
            sample = sample_agent_calls(by_agent[agent_id], rng)
            agents.append({"agent_id": agent_id, "agent_name": names.get(agent_id), **sample})
        plan.append({"evaluator": evaluator, "agents": agents})

    return {
        "seed": seed,
        "thresholds": {"short_under_seconds": SHORT_MAX_SECONDS, "long_over_seconds": LONG_MIN_SECONDS,
                       "duration_unit": "milliseconds", "min_seconds": MIN_SECONDS,
                       "extra_preference": EXTRA_PREFERENCE, "campaign_prefix": prefix or None,
                       "calls_per_agent": CALLS_PER_AGENT},
        "skipped": skipped,
        "assignments": plan,
    }
