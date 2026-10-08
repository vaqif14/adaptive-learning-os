"""Shared evidence ordering and attempt support, independent of cached projections."""
from datetime import datetime, timezone
import math
import hashlib
import json


def execution_fingerprint(capability, artifact, checks):
    data = json.dumps([capability, artifact, checks], sort_keys=True, ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(data.encode()).hexdigest()


def trial_keys(records):
    """Group revisions by attempt OR exact artifact replay, including renamed IDs.

    The two identities form connected components: changing a revision's code must
    not split its attempt; changing an attempt's name must not hide a replay.
    """
    parents = list(range(len(records)))
    aliases = {}

    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    for i, r in enumerate(records):
        e = r["payload"]
        for field in ("attempt_id", "task_fingerprint"):
            if not e.get(field):
                continue
            alias = (r.get("session_id"), e.get("capability_id"), field, e[field])
            if alias in aliases:
                parents[root(i)] = root(aliases[alias])
            else:
                aliases[alias] = i
    return [root(i) for i in range(len(records))]


def score_fraction(value):
    if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("graded score must be a finite number in [0, 1]")
    return float(value)


def parse_time(value):
    try:
        t = datetime.fromisoformat(value)
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def ordered_observations(observations, now):
    """Undated/future observations cannot establish present capability or retention."""
    return sorted(
        (o for o in observations if (t := parse_time(o.get("timestamp"))) is not None and t <= now),
        key=lambda o: parse_time(o["timestamp"]),
    )


def coverage_only(e, provenance=None):
    return (e.get("verification") == "term_coverage"
            or e.get("note") == "listener_source_coverage"
            or (provenance or {}).get("source") == "listener")


def unique_evidence(records):
    """Ignore replayed evidence IDs consistently in every model consumer."""
    seen = set()
    now = datetime.now(timezone.utc)
    for r in records:
        if r.get("record_type") != "evidence":
            continue
        e = r.get("payload")
        t = parse_time(r.get("timestamp"))
        if not isinstance(e, dict) or t is None or t > now:
            continue
        if not isinstance(e.get("capability_id"), str) or not e["capability_id"].strip():
            continue
        if e.get("evidence_id") is not None and not isinstance(e["evidence_id"], str):
            continue
        if e.get("attempt_id") is not None and not isinstance(e["attempt_id"], str):
            continue
        if e.get("task_fingerprint") is not None and not isinstance(e["task_fingerprint"], str):
            continue
        if e.get("scope") is not None and not isinstance(e["scope"], str):
            continue
        if not isinstance(e.get("outcome"), str) or e["outcome"] not in {"correct", "incorrect", "partial", "unknown"}:
            continue
        key = (r.get("session_id"), e.get("evidence_id"))
        if e.get("evidence_id") is not None:
            if key in seen:
                continue
            seen.add(key)
        if not coverage_only(e, r.get("provenance")):
            yield r


def merge_attempt_support(records, capability, attempt_id=None, supplied=None, *, fingerprint=None):
    """Support is monotone within an attempt; a fresh task needs a fresh attempt ID.

    Without an ID the capability's implicit attempt is used conservatively. The
    host remains responsible for observing assistance and identifying new tasks.
    """
    if attempt_id is not None and (not isinstance(attempt_id, str) or not attempt_id.strip()):
        raise ValueError("attempt_id must be a non-empty string")
    if supplied is not None and not isinstance(supplied, dict):
        raise ValueError("support_provenance must be an object")
    merged = {"hints_count": 0, "worked_example_shown": False,
              "ai_direct_answer_revealed": False, "conceptual_scaffold": False}
    supports = []
    for r in records:
        e = r.get("payload", {})
        same_attempt = e.get("attempt_id") == attempt_id
        same_artifact = fingerprint is not None and e.get("task_fingerprint") == fingerprint
        if e.get("capability_id") == capability and (same_attempt or same_artifact):
            supports.append(e.get("support_provenance") or {})
            if e.get("independence") == "assisted" or e.get("support_kind") in {"pedagogical", "cognitive_delegation"}:
                supports.append({"conceptual_scaffold": True})
    supports.append(supplied or {})
    for support in supports:
        if not isinstance(support, dict):
            raise ValueError("support_provenance must be an object")
        count = support.get("hints_count", 0)
        if type(count) is not int or count < 0:
            raise ValueError("hints_count must be a non-negative integer")
        merged["hints_count"] = max(merged["hints_count"], count)
        for key in ("worked_example_shown", "ai_direct_answer_revealed", "conceptual_scaffold"):
            value = support.get(key, False)
            if type(value) is not bool:
                raise ValueError(f"{key} must be boolean")
            merged[key] = merged[key] or value
    return merged


def learner_history(sessions_dir, learner_id=None, *, topic=None):
    """Select a single learner/topic before merging local capability IDs."""
    from pathlib import Path
    from .utils import read_json
    from .ledger import EventLedger
    root = Path(sessions_dir)
    sessions = []
    if root.exists():
        for directory in sorted(p for p in root.iterdir() if p.is_dir()):
            if not (directory / "ledger.jsonl").exists():
                continue
            metadata = read_json(directory / "session.json")
            if learner_id is not None and metadata.get("learner_id") != learner_id:
                continue
            sessions.append((directory, metadata))
    if len({meta.get("learner_id") for _, meta in sessions}) > 1:
        raise ValueError("multiple learners found; select --learner-id")
    if topic is not None:
        sessions = [(d, m) for d, m in sessions if m.get("topic", "").casefold() == topic.casefold()]
    if len({meta.get("topic", "").casefold() for _, meta in sessions}) > 1:
        raise ValueError("multiple topics found; select --topic to avoid merging unrelated capability IDs")
    records = []
    for directory, _ in sessions:
        records.extend(unique_evidence(EventLedger(directory / "ledger.jsonl", directory.name).verified_records()))
    records.sort(key=lambda r: (parse_time(r["timestamp"]), r["session_id"], r["seq"]))
    return len(sessions), records
