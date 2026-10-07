from __future__ import annotations

from pathlib import Path
import hashlib


def manual_resource_sync(resources: list[str], *, provider: str = "local_bundle") -> dict:
    """Register materials already available to the learner/runtime.

    NotebookLM CLI listing/selection lives in notebook_cli.py. This helper registers
    manually exported materials without assuming a public sync API.
    Missing materials are handled by the source-scope discovery policy instead of
    forcing the learner to upload a corpus every session.
    """
    synced = []
    for raw in resources:
        p = Path(raw).expanduser()
        if p.exists() and p.is_file():
            data = p.read_bytes()
            synced.append({
                "source_id": f"sha256:{hashlib.sha256(data).hexdigest()}",
                "title": p.name,
                "uri": str(p.resolve()),
                "kind": "local_file",
                "material_availability": "learner_available",
                "size_bytes": len(data),
            })
        else:
            synced.append({
                "source_id": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                "title": raw,
                "uri": raw,
                "kind": "reference",
                "material_availability": "learner_available",
            })
    return {
        "provider": provider,
        "status": "registered",
        "sources": synced,
        "raw_source_count": len(synced),
        "note": (
            "consumer NotebookLM is optional and human-operated; register/export sources when useful. "
            "No source upload is required to start learning because missing coverage is discovered automatically."
        ) if provider == "notebook_manual" else "available materials registered; missing coverage may be discovered automatically",
    }
