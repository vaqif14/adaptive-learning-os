"""R10 privacy and security: private modes, no network clients or deletion in the gate, no secrets/PII, guarded routes,
a sandbox (never an unsandboxed degrade) and a scrubbed runner environment."""
from __future__ import annotations

import ast
import os
import re
import stat
import tempfile
from pathlib import Path

from .. import course_content as cc
from .base import RUNTIME_DIR, Ctx, Result, journey_obs, na, probe, verdict, walk_strings

FORBIDDEN_MODULES = frozenset({"socket", "urllib", "http", "ssl", "requests", "ftplib", "smtplib", "telnetlib", "xmlrpc",
                               "httpx", "aiohttp", "urllib3", "websocket", "websockets", "subprocess", "asyncio",
                               "socketserver", "imaplib", "poplib", "nntplib", "ctypes", "webbrowser", "multiprocessing",
                               "pty", "selectors", "select", "smtpd", "signal", "fcntl"})
# The only gated modules that may start processes: the opt-in runner dispatcher and the detached worker. They may import
# subprocess/signal/fcntl but only start argv lists with shell=False through Popen; network, deletion and dynamic code stay banned.
PROCESS_EXEMPT = frozenset({"studio_agents.py", "studio_worker.py"})
PROCESS_MODULES = frozenset({"subprocess", "signal", "fcntl"})
SUBPROCESS_FORBIDDEN_CALLS = frozenset({"run", "call", "check_call", "check_output", "getoutput", "getstatusoutput"})
# What the modules a gated module IMPORTS (transitively) must not reach: a network client. Parsing helpers
# (urllib.parse) and the sandbox runner (subprocess, outside the studio) are legitimate there.
NETWORK_PREFIXES = ("socket", "ssl", "http", "urllib.request", "urllib.error", "urllib3", "requests", "httpx", "aiohttp",
                    "ftplib", "smtplib", "telnetlib", "xmlrpc", "websocket", "imaplib", "poplib", "nntplib", "socketserver",
                    "asyncio", "webbrowser")
# The documented network-capable harness: the HTTP server under test and its loopback client are outside the gate.
GATE_EXEMPT = frozenset({"server.py", "rules_loopback.py"})
DELETION_ATTRS = frozenset({"unlink", "rmtree", "rmdir", "removedirs", "move"})
PROCESS_ATTRS = frozenset({"system", "popen", "execv", "execve", "execl", "execle", "execlp", "execlpe", "execvp", "execvpe",
                           "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe",
                           "posix_spawn", "posix_spawnp", "fork", "forkpty", "startfile"})
DYNAMIC_CALLS = frozenset({"exec", "eval", "compile", "__import__", "import_module", "breakpoint"})
AUDITED = ("assessment-vault", "studio", "course", "sources/objects", "ledger.jsonl", "ledger-integrity.json")
SECRET_PATTERNS = [
    ("api key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")), ("aws key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")), ("slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("bearer token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{24,}")),
    ("email address", re.compile(r"\b[\w.+-]+@[\w-]+\.[A-Za-z]{2,}\b")),
    ("home path", re.compile(r"(?:/Users|/home)/[A-Za-z0-9._-]+/")),
]
ROUTE_FLAGS = re.compile(r"author|install_|promote|/api/studio/(?:submit|order|result|lesson|items)", re.I)


# ------------------------------------------------------------------ file modes
def mode_problems(root: Path, names=AUDITED) -> list[str]:
    problems = []
    for name in names:
        target = root / name
        if not target.exists() and not target.is_symlink():
            continue
        stack = [target]
        while stack:
            path = stack.pop()
            if path.is_symlink():
                problems.append(f"{path.relative_to(root)}: a symlink")
                continue
            mode = stat.S_IMODE(path.lstat().st_mode)
            if path.is_dir():
                if mode & 0o077:
                    problems.append(f"{path.relative_to(root)}: directory mode {oct(mode)} (expected 0700)")
                if path.parent.name == "scratch-runs" and path.parent.parent.name == "archive":
                    continue        # a rotated scratch run is audited as one directory, not walked again on every run
                stack.extend(path.iterdir())
            elif mode & 0o077:
                problems.append(f"{path.relative_to(root)}: file mode {oct(mode)} (expected 0600)")
    return problems


def _modes(ctx: Ctx) -> Result:
    root = ctx.session_dir
    problems = mode_problems(root)
    return verdict(problems, "vault, studio (staging, receipts, handover, scratch), course lessons and source objects are 0700/0600")


def _control_modes():
    holder = tempfile.TemporaryDirectory(prefix="rules-control-")
    root = Path(holder.name)
    vault = root / "assessment-vault" / "n"
    vault.mkdir(parents=True)
    os.chmod(vault.parent, 0o700)
    os.chmod(vault, 0o700)
    item = vault / "item.json"
    item.write_text("{}", encoding="utf-8")
    os.chmod(item, 0o644)                                    # the world-readable vault file
    ctx = Ctx.synthetic(session_dir=root)
    ctx._tmp = holder
    return ctx


@probe("r10.file_modes", "R10", control=_control_modes, title="vault, studio, scratch and source objects are 0600/0700")
def file_modes(ctx: Ctx) -> Result:
    return _modes(ctx)


# --------------------------------------------------------------- static scan
def _local_imports(path: Path, tree: ast.AST) -> set[Path]:
    """Runtime files a module imports with a relative import (top level or lazy), as paths."""
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.level:
            continue
        base = path.parent
        for _ in range(node.level - 1):
            base = base.parent
        stem = base.joinpath(*(node.module.split(".") if node.module else []))
        names = [None] + [a.name for a in node.names]
        for name in names:
            target = stem / name if name else stem
            for cand in (target.with_suffix(".py"), target / "__init__.py"):
                if cand.is_file() and RUNTIME_DIR in cand.resolve().parents:
                    found.add(cand.resolve())
    return found


def import_closure(seeds: list[Path]) -> dict[Path, ast.AST]:
    """Every runtime module reachable from ``seeds`` through relative imports (the exempt harness is not entered)."""
    seen: dict[Path, ast.AST] = {}
    stack = [p.resolve() for p in seeds]
    while stack:
        path = stack.pop()
        if path in seen or path.name in GATE_EXEMPT:
            continue
        seen[path] = ast.parse(path.read_text(encoding="utf-8"))
        stack.extend(_local_imports(path, seen[path]))
    return seen


def gated_sources() -> dict[str, str]:
    """name -> source of the modules under the full ban (studio*.py, rules_check.py, rules_probes) plus, under 'dep:',
    every module they import transitively (scanned for network clients only)."""
    paths = sorted(RUNTIME_DIR.glob("studio*.py")) + [RUNTIME_DIR / "rules_check.py"] + sorted((RUNTIME_DIR / "rules_probes").glob("*.py"))
    out = {p.relative_to(RUNTIME_DIR).as_posix(): p.read_text(encoding="utf-8") for p in paths if p.is_file()}
    for path in import_closure([p for p in paths if p.is_file()]):
        rel = path.relative_to(RUNTIME_DIR).as_posix()
        if rel not in out:
            out["dep:" + rel] = path.read_text(encoding="utf-8")
    return out


def _is_network(module: str) -> bool:
    return any(module == p or module.startswith(p + ".") for p in NETWORK_PREFIXES)


def _literal(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def scan_source(name: str, source: str) -> list[str]:
    """Findings for one module. A ``dep:`` module (reached through the import graph, not itself gated) is only checked
    for network client imports."""
    dep = name.startswith("dep:")
    forbidden = FORBIDDEN_MODULES - (PROCESS_MODULES if name in PROCESS_EXEMPT else frozenset())
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for a in node.names:
                bad = _is_network(a.name) if dep else a.name.split(".")[0] in forbidden
                if bad:
                    found.append(f"{name}: imports {a.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 0 and (_is_network(module) if dep else module.split(".")[0] in forbidden):
                found.append(f"{name}: imports from {module}")
            elif dep and node.level == 0 and any(_is_network(f"{module}.{a.name}") for a in node.names):
                found.append(f"{name}: imports a network client from {module}")
            if not dep and module in {"os", "shutil"}:
                found += [f"{name}: imports {module}.{a.name} (deletion or process)" for a in node.names
                          if a.name in DELETION_ATTRS | PROCESS_ATTRS | {"remove"}]
        elif isinstance(node, ast.Call) and not dep:
            func = node.func
            fname = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            # re.compile and friends are attribute calls: only the bare builtins and import_module count
            if (isinstance(func, ast.Name) and fname in DYNAMIC_CALLS) or fname == "import_module":
                found.append(f"{name}: dynamic code or import ({fname})")
            if isinstance(func, ast.Name) and func.id == "getattr" and len(node.args) >= 2:
                attr = _literal(node.args[1])
                if attr is None:
                    found.append(f"{name}: getattr with a computed attribute name")
                elif attr in DELETION_ATTRS | PROCESS_ATTRS | {"remove"} | DYNAMIC_CALLS:
                    found.append(f"{name}: getattr reaches {attr}")
            if isinstance(func, ast.Attribute) and (
                    func.attr in DELETION_ATTRS or func.attr in PROCESS_ATTRS
                    or (func.attr == "remove" and getattr(func.value, "id", None) == "os")):
                found.append(f"{name}: deletion or process call .{func.attr}()")
            if name in PROCESS_EXEMPT:
                if isinstance(func, ast.Attribute) and getattr(func.value, "id", None) == "subprocess" \
                        and func.attr in SUBPROCESS_FORBIDDEN_CALLS:
                    found.append(f"{name}: subprocess.{func.attr}() (only Popen with an argv list is allowed)")
                for kw in node.keywords:
                    if kw.arg == "shell" and not (isinstance(kw.value, ast.Constant) and kw.value.value is False):
                        found.append(f"{name}: shell= is not False")
    return found


def _static(ctx: Ctx) -> Result:
    sources = ctx.hook("gated_sources", gated_sources)()
    problems = [msg for name, src in sources.items() for msg in scan_source(name, src)]
    return verdict(problems, f"{len(sources)} module(s) scanned (studio, rules_check, rules_probes and everything they import): no network "
                             "client, process spawn, dynamic code or deletion call (server.py and the loopback harness are exempt)", modules=sorted(sources))


def _control_static():
    return Ctx.synthetic(hooks={"gated_sources": lambda: {
        "studio_bad.py": "import socket\nimport os\nos" + ".remove('x')\n",
        "studio_proc.py": "import subprocess\n",
        "studio_agents.py": "import subprocess\nsubprocess.Popen('ls -l', shell=True)\nsubprocess.run(['ls'])\n",
        "studio_dyn.py": "exec('x')\ngetattr(os, 'unl' + 'ink')('p')\n",
        "dep:helper.py": "import http.client\n"}})


@probe("r10.no_network_or_deletion", "R10", control=_control_static,
       title="studio_*.py, rules_check.py and rules_probes contain no network import or deletion call")
def no_network_or_deletion(ctx: Ctx) -> Result:
    return _static(ctx)


# ------------------------------------------------------------------- secrets
def secret_corpus(ctx: Ctx) -> list[tuple[str, str]]:
    rows = [(f"staged:{uid}.{path}", text) for uid, payload in ctx.staged_units().items() for path, text in walk_strings(payload)]
    handover = ctx.handover()
    if handover:
        rows += [("handover.md", handover["md"]), ("handover.json", str(handover["json"]))]
    root = ctx.root
    if root is not None:
        for name in ("events.jsonl", "notifications.jsonl"):
            path = root / name
            if path.is_file():
                rows.append((name, path.read_text(encoding="utf-8", errors="replace")))
    return rows


def _secrets(ctx: Ctx) -> Result:
    rows = ctx.hook("secret_corpus", lambda: secret_corpus(ctx))()
    problems = []
    for label, text in rows:
        for name, pattern in SECRET_PATTERNS:
            if pattern.search(text):
                problems.append(f"{label}: matches the {name} pattern")
    return verdict(problems, f"{len(rows)} staged/handover/log text(s) scanned: no secret, PII or home path")


def _control_secrets():
    return Ctx.synthetic(hooks={"secret_corpus": lambda: [("staged:lesson:n.explanation", "use key sk-abcdefghijklmnopqrstuvwx1234 now")]})


@probe("r10.no_secrets_or_pii", "R10", control=_control_secrets, title="no secret, PII or home path in staging, handover or logs")
def no_secrets_or_pii(ctx: Ctx) -> Result:
    return _secrets(ctx)


# ------------------------------------------------------------- routes (static)
def route_paths(server_source: str) -> list[str]:
    return sorted(set(re.findall(r'"(/api/[A-Za-z0-9/_-]+)"', server_source)))


def _routes(ctx: Ctx) -> Result:
    source = ctx.hook("server_source", lambda: (RUNTIME_DIR / "server.py").read_text(encoding="utf-8"))()
    paths = route_paths(source)
    bad = [p for p in paths if ROUTE_FLAGS.search(p)]
    return verdict([f"route {p} looks like it accepts authored content" for p in bad],
                   f"{len(paths)} HTTP route(s) listed; none accepts authored content (studio routes: "
                   f"{len([p for p in paths if p.startswith('/api/studio')])} defined)", routes=paths)


def _control_routes():
    return Ctx.synthetic(hooks={"server_source": lambda: 'routes = {"/api/course/author-lesson": 1, "/api/course/state": 2}'})


@probe("r10.no_authored_content_routes", "R10", control=_control_routes, title="no HTTP route accepts authored content")
def no_authored_content_routes(ctx: Ctx) -> Result:
    return _routes(ctx)


# ------------------------------------------------------------------ sandbox
def _sandbox(ctx: Ctx) -> Result:
    needs = any(cc.item_kind(((s.get("lesson") or {}).get("practice") or {})) == "python" and (s.get("lesson") or {}).get("practice")
                or any(cc.item_kind(i) == "python" for i in s.get("items") or [] if isinstance(i, dict))
                for s in ctx.subjects())
    if not needs:
        return na("the build has no code to run: the sandbox is not required")
    verify = ctx.hook("sandbox_verify", lambda: cc._VERIFIER.verify("print(1)"))
    res = verify()
    problems = []
    if not (getattr(res, "executed", False) and getattr(res, "policy_valid", False)) or getattr(res, "timed_out", False):
        problems.append("the python sandbox did not execute a trivial program; code checks cannot run (no unsandboxed fallback)")
    elif str(getattr(res, "stdout", "")).strip() != "1":
        problems.append("the python sandbox returned unexpected output for a trivial program")
    return verdict(problems, "the python sandbox executes code; there is no unsandboxed degrade")


def _control_sandbox():
    lesson = {"practice": {"checks": [], "reference_solution": "x"}}
    dead = type("R", (), {"executed": False, "policy_valid": True, "timed_out": False, "stdout": ""})()
    return Ctx.synthetic(subjects=[{"node": "n", "origin": "staged", "lesson": lesson, "items": None}],
                         hooks={"sandbox_verify": lambda: dead})


@probe("r10.sandbox_available", "R10", cost="sandbox", control=_control_sandbox, title="the sandbox is available when code must run")
def sandbox_available(ctx: Ctx) -> Result:
    return _sandbox(ctx)


def _runner_authored(job) -> bool:
    return any(str(e.get("taken_by") or "").startswith("runner:") or e.get("runner")
               for e in ((job or {}).get("orders") or {}).values() if isinstance(e, dict))


def _os_sandbox(ctx: Ctx) -> Result:
    needs = any(cc.item_kind(((s.get("lesson") or {}).get("practice") or {})) == "python" and (s.get("lesson") or {}).get("practice")
                or any(cc.item_kind(i) == "python" for i in s.get("items") or [] if isinstance(i, dict))
                or ((s.get("lesson") or {}).get("walkthrough") or {}).get("language") == "python"
                for s in ctx.subjects())
    if not needs:
        return na("the build has no code to run: no containment is required")
    from .. import os_sandbox
    proof = ctx.hook("os_containment", os_sandbox.containment_check)()
    if not proof.get("mode"):
        if _runner_authored(ctx.job):
            return verdict(["runner-authored code was executed without an OS sandbox (Seatbelt / bubblewrap) on this platform"], "")
        return na("no OS sandbox on this platform: host-authored and learner code ran under the in-process defences only "
                  "(not a boundary; disclosed in the handover)")
    problems = []
    if not proof.get("control_ok"):
        problems.append("the containment control could not read its own canary unwrapped: the check proves nothing here")
    if not proof.get("contained"):
        leaked = sorted(k for k, v in (proof.get("wrapped") or {}).items() if not str(v).startswith("denied") and k != "write_run_dir")
        problems.append(f"the OS sandbox ({proof.get('mode')}) did not contain the probe: {leaked or 'no report'}")
    return verdict(problems, f"verifier children run behind the OS sandbox ({proof.get('mode')}): canary read, outside "
                             "write, local network and shell exec are denied (negative control reads the canary)")


def _control_os_sandbox():
    lesson = {"practice": {"checks": [], "reference_solution": "x"}}
    leaky = {"mode": "seatbelt", "contained": False, "control_ok": True, "wrapped": {"read_canary": "allowed"}}
    return Ctx.synthetic(subjects=[{"node": "n", "origin": "staged", "lesson": lesson, "items": None}],
                         hooks={"os_containment": lambda: leaky})


@probe("r10.os_sandbox_contains", "R10", cost="sandbox", control=_control_os_sandbox,
       title="code the gate runs is contained by an OS sandbox (canary, network, write, exec)")
def os_sandbox_contains(ctx: Ctx) -> Result:
    return _os_sandbox(ctx)


# --------------------------------------------------------------- runner env
SENSITIVE_ENV = re.compile(r"ALEARN_|TOKEN|SECRET|COOKIE|PASSWORD|API_KEY|CREDENTIAL|SESSION", re.I)


def env_problems(env: dict) -> list[str]:
    return [f"runner environment carries {k}" for k in sorted(env) if SENSITIVE_ENV.search(k)]


def _runner_env(ctx: Ctx) -> Result:
    hooked = ctx.hook("runner_env", None)
    sample = hooked
    if sample is None:
        agents = RUNTIME_DIR / "studio_agents.py"
        if not agents.is_file():
            return na("this release has no subprocess runner: the environment scrub applies when runners exist")
        from .. import studio_agents
        # the REAL builder, fed a hostile environment: whatever the process holds, only the allowlist may come out
        hostile = {"PATH": "/evil", "HOME": "/home/x", "ALEARN_STATE_DIR": "/s", "ADAPTIVE_API_TOKEN": "t",
                   "ANTHROPIC_BASE_URL": "http://x", "SSH_AUTH_SOCK": "/a", "AWS_SECRET_ACCESS_KEY": "k",
                   "GITHUB_TOKEN": "g", "PYTHONPATH": "/p", "NODE_OPTIONS": "--x", "SESSION_COOKIE": "c"}
        sample = lambda: studio_agents.scrubbed_env("/opt/tool/bin/cli", ("ANTHROPIC_API_KEY",), hostile)   # noqa: E731
    env = sample()
    problems = env_problems(env)
    if hooked is None:
        allowed = set(("PATH", "TERM", "NO_COLOR", "ANTHROPIC_API_KEY") + ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR"))
        problems += [f"runner environment carries {k} (not on the allowlist)" for k in sorted(env) if k not in allowed]
    return verdict(problems, "the runner environment is built from an allowlist (no ALEARN_STATE_DIR, tokens or cookies)")


def _control_runner_env():
    return Ctx.synthetic(hooks={"runner_env": lambda: {"PATH": "/usr/bin", "ALEARN_STATE_DIR": "/x", "API_TOKEN": "t"}})


@probe("r10.runner_env_scrubbed", "R10", control=_control_runner_env, title="the runner environment carries no state dir or secret")
def runner_env_scrubbed(ctx: Ctx) -> Result:
    return _runner_env(ctx)


# ------------------------------------------------------ loopback guard (scratch)
GUARD_EXPECT = {"baseline": (200,), "foreign_host": (401,), "foreign_origin": (401,), "other_port_origin": (401,),
                "non_json_post": (415,), "token_required_off_loopback": (None,),
                # the owner server (stage E): own token, never anonymous, foreign Host/Origin refused, JSON only, no learner routes
                "owner_baseline": (200,), "owner_no_token": (401,), "owner_wrong_token": (401,), "owner_foreign_host": (403,),
                "owner_foreign_origin": (403,), "owner_non_json_post": (415,), "owner_no_learner_routes": (404,),
                "owner_anon_refused": (None,), "owner_token_required": (None,), "owner_token_differs_from_learner": (None,)}


def _guard(ctx: Ctx) -> Result:
    rows = journey_obs(ctx, "http_guard")
    problems = []
    for name, expect in GUARD_EXPECT.items():
        if name not in rows:
            problems.append(f"guard case '{name}' did not run")
        elif expect != (None,) and rows[name] not in expect:
            problems.append(f"guard case '{name}' answered {rows[name]} (expected {expect[0]})")
        elif expect == (None,) and rows[name] is not True:
            problems.append("a server bound off loopback without a token was accepted" if name == "token_required_off_loopback"
                            else f"owner server guard '{name}' did not refuse")
    # the LEARNER server never mounts a studio route: 404 whatever the Host (a 401/200 would mean the route exists there)
    for route, status in (rows.get("studio_routes") or {}).items():
        if status != 404:
            problems.append(f"studio route {route} answered {status} on the learner server (it must not be mounted: 404)")
    return verdict(problems, "foreign Host/Origin and non-JSON posts are refused; binding off loopback needs a token; "
                             f"{len(rows.get('studio_routes') or {})} studio route(s) answer 404 on the learner server; the owner server needs its own token")


def _control_guard():
    rows = {"baseline": 200, "foreign_host": 200, "foreign_origin": 200, "other_port_origin": 200, "non_json_post": 200,
            "token_required_off_loopback": False, "owner_baseline": 200, "owner_no_token": 200, "owner_wrong_token": 200,
            "owner_foreign_host": 200, "owner_foreign_origin": 200, "owner_non_json_post": 200, "owner_no_learner_routes": 200,
            "owner_anon_refused": False, "owner_token_required": False, "owner_token_differs_from_learner": False,
            "studio_routes": {"/api/studio": 200}}
    return Ctx.synthetic(scratch=type("S", (), {"obs": {"http_guard": rows}})())


@probe("r10.loopback_guard", "R10", kind="dynamic", cost="scratch", control=_control_guard,
       title="Host/Origin, JSON content type and token guards hold on every route")
def loopback_guard(ctx: Ctx) -> Result:
    return _guard(ctx)


PROBES = [file_modes, no_network_or_deletion, no_secrets_or_pii, no_authored_content_routes, sandbox_available,
          os_sandbox_contains, runner_env_scrubbed, loopback_guard]
