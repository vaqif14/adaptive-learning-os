"""Stage A static gates: no network clients and no deletion calls in any studio module.

Superseding means renaming into archive/; the studio never removes data and never talks to a network.
"""
import ast
import os
import re
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1] / "skills/adaptive-learn/runtime"
FORBIDDEN_MODULES = {"socket", "urllib", "http", "ssl", "requests", "ftplib", "smtplib", "telnetlib", "xmlrpc",
                     "httpx", "aiohttp", "urllib3", "websocket", "websockets"}
DELETION_ATTRS = {"unlink", "rmtree", "rmdir", "removedirs"}
DELETION_TEXT = re.compile(r"\b(os\.remove|os\.unlink|os\.rmdir|os\.removedirs|shutil\.rmtree)\b|\.unlink\s*\(|\brmtree\s*\(")


def gated_files():
    files = sorted(RUNTIME.glob("studio*.py"))
    rules = RUNTIME / "rules_check.py"
    if rules.exists():
        files.append(rules)
    files += sorted((RUNTIME / "rules_probes").glob("*.py"))          # stage C: the probes obey the same gates
    return files


def network_imports(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name.split(".")[0] in FORBIDDEN_MODULES]
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in FORBIDDEN_MODULES:
                found.append(node.module)
        elif isinstance(node, ast.Call) and getattr(node.func, "id", "") in {"__import__", "import_module"}:
            found.append("dynamic import")
        elif isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "import_module":
            found.append("dynamic import")
    return found


def deletion_calls(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = getattr(node.func.value, "id", None)
            if node.func.attr in DELETION_ATTRS or (node.func.attr == "remove" and owner == "os"):
                found.append(f"{owner or '<expr>'}.{node.func.attr}")
        elif isinstance(node, ast.ImportFrom) and node.module in {"os", "shutil"}:
            found += [f"{node.module}.{a.name}" for a in node.names if a.name in {"remove", "unlink", "rmtree", "rmdir", "removedirs"}]
    return found


PROCESS_MODULES_OWNERS = {"studio_agents.py", "studio_worker.py"}
PROCESS_NAMES = {"subprocess", "signal", "fcntl", "pty", "pexpect"}


def process_imports(source: str) -> set[str]:
    tree = ast.parse(source)
    names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level == 0}
    return names & PROCESS_NAMES


def process_violations(source: str) -> list[str]:
    """Process starts that are not ``subprocess.Popen(<argv list or variable>, ..., shell=False)``."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        owner = getattr(node.func.value, "id", None)
        if owner == "subprocess":
            if node.func.attr != "Popen":
                found.append(f"subprocess.{node.func.attr}")
                continue
            first = node.args[0] if node.args else None
            if not isinstance(first, (ast.List, ast.Name)):
                found.append("Popen argv is not a list or a variable")
            shell = next((k for k in node.keywords if k.arg == "shell"), None)
            if shell is None or not (isinstance(shell.value, ast.Constant) and shell.value.value is False):
                found.append("Popen without shell=False")
        elif owner == "os" and node.func.attr in {"system", "popen"} | {a for a in dir(os) if a.startswith(("exec", "spawn"))}:
            found.append(f"os.{node.func.attr}")
    return found


class StudioStaticGateTests(unittest.TestCase):
    def test_all_stage_a_modules_are_covered_by_the_scan(self):
        names = {p.name for p in gated_files()}
        self.assertTrue({"studio.py", "studio_store.py", "studio_plan.py", "studio_jobs.py", "studio_orders.py",
                         "studio_staging.py"} <= names)

    def test_stage_c_modules_and_every_probe_module_are_covered_by_the_scan(self):
        names = {p.relative_to(RUNTIME).as_posix() for p in gated_files()}
        self.assertTrue({"studio_review.py", "studio_receipt.py", "studio_handover.py", "studio_remediate.py", "rules_check.py"} <= names)
        self.assertTrue({f"rules_probes/r{n:02d}.py" for n in range(1, 11)} | {"rules_probes/base.py", "rules_probes/journey.py"} <= names)

    def test_the_only_http_speaking_gate_module_is_the_loopback_harness(self):
        """rules_loopback.py is outside the gated set on purpose: it only talks to the server it starts on 127.0.0.1."""
        source = (RUNTIME / "rules_loopback.py").read_text(encoding="utf-8")
        self.assertIn("LOOPBACK = \"127.0.0.1\"", source)
        self.assertNotIn("0.0.0.0", source)
        for path in gated_files():
            self.assertNotIn("rules_loopback", network_imports(path.read_text(encoding="utf-8")))

    def test_no_network_client_imports_in_studio_modules(self):
        for path in gated_files():
            self.assertEqual(network_imports(path.read_text(encoding="utf-8")), [], path.name)

    def test_no_deletion_calls_in_studio_modules(self):
        for path in gated_files():
            source = path.read_text(encoding="utf-8")
            self.assertEqual(deletion_calls(source), [], path.name)
            self.assertIsNone(DELETION_TEXT.search(source), f"{path.name}: {DELETION_TEXT.search(source)}")

    def test_only_the_runner_and_worker_modules_import_process_machinery(self):
        """Stage D: subprocess/signal/fcntl live in studio_agents.py and studio_worker.py and nowhere else in the gated set."""
        owners = set()
        for path in gated_files():
            names = process_imports(path.read_text(encoding="utf-8"))
            if names:
                owners.add(path.name)
            self.assertFalse(names & {"pty", "pexpect"}, path.name)
        self.assertEqual(owners, PROCESS_MODULES_OWNERS)

    def test_processes_start_only_from_argv_lists_with_shell_false(self):
        for name in sorted(PROCESS_MODULES_OWNERS):
            source = (RUNTIME / name).read_text(encoding="utf-8")
            self.assertEqual(process_violations(source), [], name)
            self.assertIn("subprocess.Popen(", source)
            for banned in ("os.system", "os.popen", "os.exec", "os.spawn", "os.fork"):
                self.assertNotIn(banned, source, name)

    def test_stage_d_modules_are_covered_by_the_scan(self):
        names = {p.name for p in gated_files()}
        self.assertTrue(PROCESS_MODULES_OWNERS <= names)

    def test_stage_e_owner_views_are_scanned_and_the_server_spawns_only_through_the_worker_module(self):
        """Stage E: studio_views.py joins the full ban; server.py (owner routes) imports no process machinery, deletes nothing,
        and starts a process only through studio_worker.start_detached (argv list, shell=False, in the worker module)."""
        self.assertIn("studio_views.py", {p.name for p in gated_files()})
        self.assertNotIn("studio_views.py", PROCESS_MODULES_OWNERS)
        server = (RUNTIME / "server.py").read_text(encoding="utf-8")
        self.assertEqual(process_imports(server), set())
        self.assertEqual(deletion_calls(server), [])
        self.assertIsNone(DELETION_TEXT.search(server))
        self.assertEqual(process_violations(server), [])
        self.assertEqual(server.count("start_detached("), 1)
        self.assertEqual(PROCESS_MODULES_OWNERS, {"studio_agents.py", "studio_worker.py"})        # exactly these two, still

    def test_the_owner_post_routes_are_exactly_the_documented_control_set_and_accept_no_content_fields(self):
        tree = ast.parse((RUNTIME / "server.py").read_text(encoding="utf-8"))
        fields = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "_STUDIO_POST_FIELDS" for t in node.targets):
                fields = {k.value: ({e.value for e in v.elts} if isinstance(v, ast.Set) else set())
                          for k, v in zip(node.value.keys, node.value.values)}
        self.assertEqual(fields, {"start": {"runner"}, "pause": set(), "resume": set(), "cancel": set(),
                                  "accept": {"digest", "statement"}, "reject": {"node", "note"}})

    def test_the_process_scanner_catches_violations(self):
        for code in ("import subprocess\nsubprocess.Popen('ls -l', shell=True)", "import subprocess\nsubprocess.run(['ls'])",
                     "import subprocess\nsubprocess.Popen(['ls'])", "import subprocess\nsubprocess.Popen(f'ls {x}', shell=False)",
                     "import subprocess\nsubprocess.check_output(['ls'])", "import os\nos.system('ls')",
                     "import os\nos.execv('/bin/ls', ['ls'])"):
            self.assertTrue(process_violations(code), code)
        self.assertEqual(process_violations("import subprocess\nsubprocess.Popen(argv, shell=False)"), [])
        self.assertEqual(process_violations("import subprocess\nsubprocess.Popen(['ls'], shell=False, cwd='.')"), [])
        self.assertEqual(process_imports("import json\nimport os"), set())
        self.assertEqual(process_imports("import fcntl\nfrom signal import SIGTERM"), {"fcntl", "signal"})

    def test_the_scanners_catch_violations(self):
        """Negative controls: the gate must fail on deliberately bad code."""
        bad_net = ["import socket", "import urllib.request", "from urllib import request", "import http.client",
                   "from http import client", "import ssl", "import requests", "from requests import get",
                   "import importlib\nimportlib.import_module('socket')"]
        for code in bad_net:
            self.assertTrue(network_imports(code), code)
        bad_del = ["import os\nos.remove('x')", "import os\nos.unlink('x')", "from pathlib import Path\nPath('x').unlink()",
                   "import shutil\nshutil.rmtree('x')", "from shutil import rmtree\nrmtree('x')", "import os\nos.rmdir('x')"]
        for code in bad_del:
            hit = deletion_calls(code) or DELETION_TEXT.search(code)
            self.assertTrue(hit, code)
        self.assertEqual(network_imports("import json\nimport hashlib"), [])
        self.assertEqual(deletion_calls("items = [1]\nitems.remove(1)"), [])      # list.remove is not a deletion of data

    def test_superseding_is_a_rename_into_archive(self):
        text = (RUNTIME / "studio_store.py").read_text(encoding="utf-8")
        self.assertIn("os.rename(path, target)", text)
        self.assertIn("def archive_rename", text)


if __name__ == "__main__":
    unittest.main()
