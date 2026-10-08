"""run_capped kills the whole process group on timeout (S5)."""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "adaptive-learn"
sys.path.insert(0, str(SKILL))

from runtime.utils import run_capped  # noqa: E402

pytestmark = pytest.mark.skipif(os.name != "posix", reason="process-group kill is POSIX-only")


def test_timeout_kills_grandchild(tmp_path):
    marker = tmp_path / "grandchild_ran"
    child = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', \"import time; time.sleep(2.5); "
        f"open(r'{marker}', 'w').write('x')\"]); "
        "time.sleep(30)"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        run_capped([sys.executable, "-c", child], timeout=1,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(3.0)  # past when the grandchild would have written the marker
    assert not marker.exists(), "grandchild survived the timeout (process group not killed)"


def test_normal_completion_returns_completedprocess():
    r = run_capped([sys.executable, "-c", "print('ok')"], timeout=10,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert r.returncode == 0
    assert "ok" in r.stdout
