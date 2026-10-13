"""Live read-only NotebookLM source/grounding smoke; never imports or uploads.

Requires an explicitly selected notebook, an existing expected-resource manifest,
and an existing lesson JSON (or content JSON containing ``lesson``). Cached source
text stays privately in a new local output directory. This is integration evidence,
not learner-performance or efficacy evidence. Do not include caches in a public PR.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills/adaptive-learn"))

from runtime import course_content as cc
from runtime import course_sources as sources


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--notebook-id", required=True)
    p.add_argument("--resources-file", required=True)
    p.add_argument("--lesson-file", required=True)
    p.add_argument("--out", required=True, help="new private local directory")
    args = p.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    resources = json.loads(Path(args.resources_file).read_text())
    expected = resources["expected"] if isinstance(resources, dict) else resources
    content = json.loads(Path(args.lesson_file).read_text())
    lesson = content.get("lesson", content)
    caps = sources.capabilities("notebooklm")
    manifest = sources.validate_resources(args.notebook_id, expected, caps, out / "sources", wait_timeout=0)
    refs = [r for part in cc.LESSON_PARTS for r in lesson[part]["refs"] if r.get("source_id")]
    grounded = [sources.find_quote(out / "sources", manifest, r["source_id"], r["quote"]) for r in refs]
    report = {"operation": "live_read_only", "provider": caps["provider"], "cli_version": caps["version"],
              "resources": [{k: r.get(k) for k in ("key", "status", "char_count", "content_sha256", "stale")}
                            for r in manifest["resources"]],
              "quotes_checked": len(refs), "quotes_grounded": sum(g["grounded"] for g in grounded),
              "quote_failures": [g.get("reason") for g in grounded if not g["grounded"]],
              "scope": "existing_imports_and_readable_grounding; no_new_import_or_learner_outcome"}
    ok = bool(refs) and all(g["grounded"] for g in grounded) and manifest["complete"]
    report["passed"] = ok
    # Private manifest/cache contain the already authorized source identities/text;
    # report contains only coarse status, counts, and hashes.
    sources_json = out / "manifest.json"
    sources_json.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    sources_json.chmod(0o600)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    (out / "report.json").chmod(0o600)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
