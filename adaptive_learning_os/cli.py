from __future__ import annotations

import importlib.util
from pathlib import Path


def _skill_script() -> Path:
    """Locate the runtime CLI (scripts/alearn.py) in-tree or in the installed package.

    In a source checkout it sits at ../skills/adaptive-learn/scripts/alearn.py.
    In a wheel install it ships as the ``alearn_skill`` package; we resolve that
    package's directory. An explicit ALEARN_SKILL_DIR overrides both.
    """
    import os
    candidates = []
    env = os.environ.get("ALEARN_SKILL_DIR")
    if env:
        candidates.append(Path(env) / "scripts" / "alearn.py")
    # in-tree (source checkout)
    candidates.append(Path(__file__).resolve().parents[1] / "skills" / "adaptive-learn" / "scripts" / "alearn.py")
    # installed package copy
    try:
        spec = importlib.util.find_spec("alearn_skill")
        if spec and spec.submodule_search_locations:
            candidates.append(Path(list(spec.submodule_search_locations)[0]) / "scripts" / "alearn.py")
    except (ImportError, ValueError):
        pass
    for c in candidates:
        if c.is_file():
            return c
    raise FileNotFoundError(
        "Could not locate the Adaptive Learning runtime (scripts/alearn.py). "
        "Set ALEARN_SKILL_DIR to the skill directory, or reinstall the package."
    )


def _load_runtime_cli():
    script = _skill_script()
    spec = importlib.util.spec_from_file_location("adaptive_learning_os_runtime_cli", script)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable_to_load_runtime_cli")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    module = _load_runtime_cli()
    raise SystemExit(module.main())
