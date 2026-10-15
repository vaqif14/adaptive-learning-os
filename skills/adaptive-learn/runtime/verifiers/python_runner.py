from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import ast
import json
import os
import signal
import string
import subprocess
import sys
import tempfile

from .. import os_sandbox
from ..utils import run_capped
from typing import Iterable


DEFAULT_ALLOWED_IMPORTS = {
    "copy", "math", "statistics", "json", "re", "collections", "itertools", "functools",
}

FORBIDDEN_NAMES = {
    "open", "eval", "exec", "compile", "__import__", "input",
    "globals", "locals", "vars", "breakpoint", "getattr", "setattr", "delattr",
    "memoryview", "exit", "quit",
    "__builtins__", "__import__", "__loader__", "__spec__", "__globals__",
}

FORBIDDEN_MODULES = {
    "os", "sys", "subprocess", "socket", "pathlib", "shutil", "ctypes",
    "multiprocessing", "threading", "http", "urllib", "requests", "asyncio",
    "importlib", "builtins", "resource", "signal", "mmap", "fcntl",
}

# Introspection attributes that are not dunders but still lead from learner code to the harness frame, its globals and
# the real builtins (a generator or traceback frame walk: gi_frame -> f_back -> f_globals / f_builtins / f_locals).
FORBIDDEN_ATTRS = {
    "gi_frame", "gi_code", "gi_yieldfrom", "cr_frame", "cr_code", "cr_await", "cr_origin", "ag_frame", "ag_code", "ag_await",
    "f_back", "f_globals", "f_locals", "f_builtins", "f_code", "f_trace", "tb_frame", "tb_next", "format_map",
}
# Attributes of an imported module that are other modules (``re.enum.sys``) or private internals.
FORBIDDEN_MODULE_ATTRS = FORBIDDEN_MODULES | {"enum", "types", "modules", "builtins", "sys", "operator"}

_SENTINEL_RESULT = "result.json"


def _safe_template(node: ast.AST) -> bool:
    """A str.format receiver is allowed only as a literal template without attribute or index fields: ``'{0.__class__}'``
    walks attributes at run time and no AST check can see it."""
    if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
        return False
    pending, depth = [node.value], 0
    while pending and depth < 50:
        depth += 1
        try:
            fields = list(string.Formatter().parse(pending.pop()))
        except ValueError:
            return False
        for _lit, name, spec, _conv in fields:
            if name is not None and ("." in name or "[" in name):
                return False
            if spec and "{" in spec:
                pending.append(spec)
    return not pending


@dataclass
class PythonVerificationResult:
    syntax_valid: bool
    policy_valid: bool
    executed: bool
    passed: bool
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None
    timed_out: bool = False
    policy_errors: list[str] | None = None
    syntax_error: dict | None = None
    # v0.6.0 hardening fields
    correctness_checked: bool = False      # True only if a trusted test or expected_stdout was applied
    verdict_source: str = "none"           # trusted_tests | expected_stdout | none | error
    resource_limits: str = "best_effort"

    def to_dict(self) -> dict:
        return asdict(self)


class _SafetyVisitor(ast.NodeVisitor):
    def __init__(self, allowed_imports: set[str]):
        self.allowed_imports = allowed_imports
        self.errors: list[str] = []
        self.module_names: set[str] = set()

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            root = alias.name.split(".", 1)[0]
            if root in FORBIDDEN_MODULES or root not in self.allowed_imports:
                self.errors.append(f"import not allowed: {alias.name}")
            self.module_names.add((alias.asname or alias.name).split(".", 1)[0])
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        root = (node.module or "").split(".", 1)[0]
        if not root or root in FORBIDDEN_MODULES or root not in self.allowed_imports:
            self.errors.append(f"import not allowed: {node.module}")
        for alias in node.names:
            if alias.name in FORBIDDEN_MODULE_ATTRS or alias.name.startswith("_"):
                self.errors.append(f"import not allowed: {node.module}.{alias.name}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "format" and _safe_template(func.value):
            for child in [*node.args, *(k.value for k in node.keywords)]:      # the literal template needs no further check
                self.visit(child)
            return
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name):
        if node.id in FORBIDDEN_NAMES:
            self.errors.append(f"name not allowed: {node.id}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        if node.attr.startswith("__") and node.attr.endswith("__"):
            self.errors.append(f"dunder attribute not allowed: {node.attr}")
        if node.attr in {"system", "popen", "fork", "spawn", "connect", "bind", "listen", "accept"} | FORBIDDEN_ATTRS:
            self.errors.append(f"attribute not allowed: {node.attr}")
        if node.attr == "format":
            self.errors.append("attribute not allowed: format (use a literal template without attribute or index fields, "
                               "or an f-string)")
        if isinstance(node.value, ast.Name) and node.value.id in self.module_names and (
                node.attr in FORBIDDEN_MODULE_ATTRS or node.attr.startswith("_")):
            self.errors.append(f"module attribute not allowed: {node.value.id}.{node.attr}")
        self.generic_visit(node)


# Child-side harness. Runs under `python -I -S`. The learner code is executed by
# TRUSTED code here, so a `raise SystemExit(0)` in the learner's code can never
# skip the trusted assertions: it is caught, and the verdict is decided by the
# harness, not by the child's exit status. Builtins are an allowlist, so
# open/eval/__import__ are unreachable even if they slip past the AST pre-filter.
# OS resource limits + (for untrusted/high-risk) a container remain the real
# boundary; see the module docstring.
_HARNESS = r'''
def _main():
    import io, json, os, sys, sysconfig, builtins as _b

    cfg = json.loads(open(sys.argv[1], "r", encoding="utf-8").read())
    code = cfg["code"]
    trusted = cfg.get("trusted")
    expected = cfg.get("expected_stdout")
    allowed = set(cfg.get("allowed_imports") or [])
    cap = int(cfg.get("max_output_bytes", 64000))
    result_path = sys.argv[2]

    _SAFE_NAMES = [
        "abs","all","any","ascii","bin","bool","bytearray","bytes","callable","chr",
        "complex","dict","divmod","enumerate","filter","float","format","frozenset",
        "hash","hex","int","isinstance","issubclass","iter","len","list","map","max",
        "min","next","oct","ord","pow","print","range","repr","reversed","round","set",
        "slice","sorted","str","sum","tuple","type","zip","True","False","None",
        "Exception","ValueError","TypeError","KeyError","IndexError","ZeroDivisionError",
        "StopIteration","ArithmeticError","AssertionError","RuntimeError","NotImplementedError",
        "SystemExit","RecursionError","MemoryError","GeneratorExit",
        "AttributeError","LookupError","OverflowError","FloatingPointError","abs",
    ]
    safe_builtins = {n: getattr(_b, n) for n in _SAFE_NAMES if hasattr(_b, n)}
    _ORIG_IMPORT = _b.__import__

    def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        root = name.split(".", 1)[0]
        if level != 0 or root not in allowed:
            raise ImportError("import not allowed: %s" % name)
        return _ORIG_IMPORT(name, globals, locals, fromlist, level)

    safe_builtins["__import__"] = _safe_import

    # Audit hook (defence in depth behind the AST policy, which is a pre-filter and not a boundary): while the learner
    # code and the trusted checks run, any network, process, file-write or out-of-stdlib file-read event is refused, so
    # even a walk to a real module object cannot reach the network, the account's files or a subprocess. The hook is
    # switched off (closure cell) only for the harness's own final write. Denials are PermissionError so importlib's
    # best-effort bytecode caching degrades quietly.
    _live = [True]
    _paths = sysconfig.get_paths()
    _roots = tuple(sorted({os.path.realpath(_paths[k]) + os.sep for k in ("stdlib", "platstdlib", "purelib", "platlib")
                           if _paths.get(k)}))
    _blocked = ("socket.", "subprocess.", "os.system", "os.exec", "os.fork", "os.forkpty", "os.spawn", "os.posix_spawn",
                "os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.chmod", "os.chown", "os.truncate", "os.symlink",
                "os.link", "os.putenv", "os.unsetenv", "os.kill", "os.killpg", "os.setuid", "os.chdir", "os.chroot",
                "shutil.", "ctypes.", "pty.", "webbrowser.", "urllib.", "http.", "ftplib.", "smtplib.", "telnetlib.",
                "mmap.", "signal.", "fcntl.", "resource.", "sys.addaudithook", "sys.settrace", "sys.setprofile",
                "pickle.find_class", "msvcrt.", "winreg.")

    def _inside(path):
        try:
            real = os.path.realpath(os.fsdecode(path))
        except (TypeError, ValueError):
            return False
        return real.startswith(_roots)

    def _hook(event, args):
        if not _live[0]:
            return
        if event.startswith(_blocked):
            raise PermissionError("blocked in the sandbox: " + event)
        if event == "open":
            path, mode, flags = (list(args) + [None, None])[:3]
            if isinstance(path, int):
                return
            write = bool(mode and set(str(mode)) & set("wax+")) or bool(
                isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC))
            if write or not _inside(path):
                raise PermissionError("blocked in the sandbox: open")
        elif event in ("os.listdir", "os.scandir", "os.walk", "os.readlink", "glob.glob"):
            path = args[0] if args else None
            if path is None or isinstance(path, int) or not _inside(path):
                raise PermissionError("blocked in the sandbox: " + event)

    class _Capped(io.StringIO):
        def __init__(self, cap):
            super().__init__(); self._cap = cap; self._n = 0
        def write(self, s):
            if self._n >= self._cap:
                return 0
            s = s[: self._cap - self._n]
            self._n += len(s)
            return super().write(s)

    sandbox = {"__builtins__": safe_builtins, "__name__": "__submission__"}
    cap_out = _Capped(cap)
    res = {"executed": False, "passed": False, "correctness_checked": False,
           "verdict_source": "none", "stdout": "", "error": None, "exit_code": None,
           "tests_failed": False}

    _real_stdout = sys.stdout
    sys.stdout = cap_out
    sys.addaudithook(_hook)
    try:
        try:
            compiled = compile(code, "<submission>", "exec")
            exec(compiled, sandbox)
            res["executed"] = True
        except SystemExit as e:                     # caught: cannot short-circuit tests
            res["executed"] = True
            res["exit_code"] = e.code if isinstance(e.code, int) else None
        except BaseException as e:
            res["executed"] = True
            res["error"] = "%s: %s" % (type(e).__name__, e)
    finally:
        sys.stdout = _real_stdout

    res["stdout"] = cap_out.getvalue()

    if res["error"] is None:
        if trusted:
            res["correctness_checked"] = True
            res["verdict_source"] = "trusted_tests"
            try:
                t_ns = dict(sandbox)
                t_ns["__builtins__"] = safe_builtins
                exec(compile(trusted, "<trusted>", "exec"), t_ns)
                res["passed"] = True
            except BaseException as e:
                res["tests_failed"] = True
                res["error"] = "trusted_test_failed: %s: %s" % (type(e).__name__, e)
        elif expected is not None:
            res["correctness_checked"] = True
            res["verdict_source"] = "expected_stdout"
            res["passed"] = res["stdout"].strip() == str(expected).strip()
        else:
            res["passed"] = False  # ran, but nothing to verify correctness against

    _live[0] = False
    open(result_path, "w", encoding="utf-8").write(json.dumps(res))


_main()
'''


class PythonVerifier:
    """Deterministic verifier for low-risk learning snippets.

    Hardening (v0.6.0): learner code runs under a trusted harness that owns the
    verdict (a learner `SystemExit`/early return cannot force a pass), builtins
    are an allowlist (open/eval/__import__ unreachable), output is capped at the
    source, and the child runs in its own process group (killed as a group on
    timeout). These in-process layers are defense in depth, NOT a security
    boundary; the boundary is the OS sandbox (``os_sandbox``: Seatbelt on macOS,
    bubblewrap on Linux) wrapped around the child whenever the platform has one.
    Without it (``os_sandbox.mode() is None``) untrusted code MUST run in a
    container/VM with no network, a read-only FS and a non-root user. Resource limits are best-effort and degrade (never crash) on
    platforms lacking a given rlimit (e.g. RLIMIT_AS on macOS).
    """

    def __init__(
        self,
        *,
        allowed_imports: Iterable[str] | None = None,
        timeout_sec: float = 2.0,
        memory_mb: int = 256,
        max_output_bytes: int = 64_000,
    ):
        self.allowed_imports = set(allowed_imports or DEFAULT_ALLOWED_IMPORTS)
        self.timeout_sec = float(timeout_sec)
        self.memory_mb = int(memory_mb)
        self.max_output_bytes = int(max_output_bytes)

    def syntax_and_policy(self, code: str):
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return None, {"message": e.msg, "line": e.lineno, "offset": e.offset, "text": e.text}, []
        visitor = _SafetyVisitor(self.allowed_imports)
        visitor.visit(tree)
        return tree, None, sorted(set(visitor.errors))

    def _resource_limits(self):  # pragma: no cover - runs only in the child
        # Each limit guarded independently so a missing one degrades instead of
        # killing the spawn (this is the macOS RLIMIT_AS crash fix).
        try:
            import resource
        except Exception:
            return
        mem = self.memory_mb * 1024 * 1024
        for name, val in (
            ("RLIMIT_CPU", (2, 2)),
            ("RLIMIT_FSIZE", (1_000_000, 1_000_000)),
            ("RLIMIT_NOFILE", (32, 32)),
        ):
            try:
                resource.setrlimit(getattr(resource, name), val)
            except (ValueError, OSError, AttributeError):
                pass
        # Address-space cap: RLIMIT_AS is unsupported on macOS; prefer RLIMIT_DATA there.
        mem_limit_names = ("RLIMIT_DATA",) if sys.platform == "darwin" else ("RLIMIT_AS", "RLIMIT_DATA")
        for name in mem_limit_names:
            try:
                resource.setrlimit(getattr(resource, name), (mem, mem))
                break
            except (ValueError, OSError, AttributeError):
                continue

    def verify(
        self,
        code: str,
        *,
        expected_stdout: str | None = None,
        trusted_test_code: str | None = None,
    ) -> PythonVerificationResult:
        _, syntax_error, policy_errors = self.syntax_and_policy(code)
        if syntax_error:
            return PythonVerificationResult(
                syntax_valid=False, policy_valid=False, executed=False, passed=False,
                syntax_error=syntax_error, policy_errors=[], verdict_source="error",
            )
        if policy_errors:
            return PythonVerificationResult(
                syntax_valid=True, policy_valid=False, executed=False, passed=False,
                policy_errors=policy_errors, verdict_source="error",
            )

        with tempfile.TemporaryDirectory(prefix="alearn-py-") as td:
            tdp = Path(td)
            (tdp / "harness.py").write_text(_HARNESS, encoding="utf-8")
            (tdp / "config.json").write_text(json.dumps({
                "code": code,
                "trusted": trusted_test_code,
                "expected_stdout": expected_stdout,
                "allowed_imports": sorted(self.allowed_imports),
                "max_output_bytes": self.max_output_bytes,
            }), encoding="utf-8")
            result_file = tdp / _SENTINEL_RESULT
            posix = os.name == "posix"
            try:
                # The OS boundary (Seatbelt / bubblewrap) when this platform has one: no network, no exec outside the
                # interpreter, writes only in this temp dir, no reads of homes, temp roots or the state dir.
                proc = run_capped(
                    os_sandbox.wrap([sys.executable, "-I", "-S", str(tdp / "harness.py"),
                                     str(tdp / "config.json"), str(result_file)], td),
                    cwd=td,
                    env={"PYTHONHASHSEED": "0", "PATH": os.environ.get("PATH", "")},
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=False,
                    timeout=self.timeout_sec,
                    preexec_fn=self._resource_limits if posix else None,
                    start_new_session=posix,
                )
            except subprocess.TimeoutExpired as e:
                return PythonVerificationResult(
                    syntax_valid=True, policy_valid=True, executed=True, passed=False,
                    stdout=self._decode(e.stdout or b""), stderr=self._decode(e.stderr or b""),
                    timed_out=True, policy_errors=[], verdict_source="none",
                )

            stderr = self._decode(proc.stderr)
            if not result_file.exists():
                # Harness never produced a verdict (crash / killed) -> fail closed.
                return PythonVerificationResult(
                    syntax_valid=True, policy_valid=True, executed=False, passed=False,
                    stdout="", stderr=stderr, returncode=proc.returncode,
                    policy_errors=[], verdict_source="error",
                )
            res = json.loads(result_file.read_text(encoding="utf-8"))

        return PythonVerificationResult(
            syntax_valid=True,
            policy_valid=True,
            executed=bool(res.get("executed")),
            passed=bool(res.get("passed")),
            stdout=res.get("stdout", ""),
            stderr=(res.get("error") or "") + (("\n" + stderr) if stderr else ""),
            returncode=res.get("exit_code"),
            timed_out=False,
            policy_errors=[],
            correctness_checked=bool(res.get("correctness_checked")),
            verdict_source=res.get("verdict_source", "none"),
        )

    def _decode(self, data: bytes) -> str:
        return data[: self.max_output_bytes].decode("utf-8", errors="replace")
