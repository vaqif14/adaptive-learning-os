"""Optional Agent-Reach source-acquisition adapter (availability only).

Agent-Reach (github.com/Panniantong/Agent-Reach, MIT) gives a host agent read
access to the web, YouTube, Reddit, GitHub, RSS, etc. via one CLI. It is NOT a
structured-JSON API — after `agent-reach install` it drops a SKILL.md and the
HOST AGENT calls the underlying tools (Jina Reader, yt-dlp, gh, feedparser). So
this module does NOT parse content or fetch anything itself: it only detects
whether Agent-Reach is available and surfaces its `doctor` status, so the runtime
can tell the learner "you can pull outside sources with this" without taking a
dependency or touching the network. Core deps stay []. Actual content retrieval,
and turning fetched material into governed learning sources, is the host agent's
job (see SKILL.md), exactly as NotebookLM is used.
"""
from __future__ import annotations

import shutil
import subprocess


EXECUTABLE = "agent-reach"
_INSTALL_HINT = "Install Agent-Reach (github.com/Panniantong/Agent-Reach): ask the host agent to run its one-line installer, then `agent-reach install`."


def provider() -> str | None:
    return shutil.which(EXECUTABLE)


def available() -> bool:
    return provider() is not None


def doctor() -> dict:
    """Return Agent-Reach availability + a redacted `doctor` status summary.

    Never raises on a missing tool and never echoes raw diagnostics (they can
    include login/cookie state); only a coarse status is surfaced.
    """
    exe = provider()
    if exe is None:
        return {"status": "unavailable", "provider": EXECUTABLE, "action": _INSTALL_HINT}
    try:
        result = subprocess.run(
            [exe, "doctor"], capture_output=True, text=True, timeout=30,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "provider": EXECUTABLE, "action": "Retry `agent-reach doctor`."}
    except OSError:
        return {"status": "unavailable", "provider": EXECUTABLE, "action": _INSTALL_HINT}
    blob = (result.stdout + result.stderr).lower()
    if result.returncode:
        auth = any(w in blob for w in ("login", "auth", "cookie", "expired", "configure"))
        return {
            "status": "configuration_required" if auth else "cli_error",
            "provider": EXECUTABLE,
            "action": "Run `agent-reach configure <platform>`." if auth else "Run `agent-reach doctor` to inspect.",
        }
    # Report WHICH platforms doctor lists as ready, without dumping raw output.
    known = ("web", "youtube", "reddit", "twitter", "github", "bilibili", "rss", "xiaohongshu")
    ready = [p for p in known if p in blob]
    return {
        "status": "available",
        "provider": EXECUTABLE,
        "platforms_mentioned": ready,
        "note": "Host agent fetches sources via Agent-Reach, then registers them with `alearn sources-register` (core stays dependency-free).",
    }
