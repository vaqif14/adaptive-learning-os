from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import ast
import json
import os
import signal
import subprocess
import sys
import tempfile
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

_SENTINEL_RESULT = "result.json"


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

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            root = alias.name.split(".", 1)[0]
            if root in FORBIDDEN_MODULES or root not in self.allowed_imports:
                self.errors.append(f"import not allowed: {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        root = (node.module or "").split(".", 1)[0]
        if not root or root in FORBIDDEN_MODULES or root not in self.allowed_imports:
            self.errors.append(f"import not allowed: {node.module}")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name):
        if node.id in FORBIDDEN_NAMES:
            self.errors.append(f"name not allowed: {node.id}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        if node.attr.startswith("__") and node.attr.endswith("__"):
            self.errors.append(f"dunder attribute not allowed: {node.attr}")
        if node.attr in {"system", "popen", "fork", "spawn", "connect", "bind", "listen", "accept"}:
            self.errors.append(f"attribute not allowed: {node.attr}")
        self.generic_visit(node)


# Child-side harness. Runs under `python -I -S`. The learner code is executed by
# TRUSTED code here, so a `raise SystemExit(0)` in the learner's code can never
# skip the trusted assertions: it is caught, and the verdict is decided by the
# harness, not by the child's exit status. Builtins are an allowlist, so
# open/eval/__import__ are unreachable even if they slip past the AST pre-filter.
# OS resource limits + (for untrusted/high-risk) a container remain the real
# boundary; see the module docstring.
_HARNESS = r'''
import io, json, sys, builtins as _b

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

def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    root = name.split(".", 1)[0]
    if level != 0 or root not in allowed:
        raise ImportError("import not allowed: %s" % name)
    return _ORIG_IMPORT(name, globals, locals, fromlist, level)

_ORIG_IMPORT = _b.__import__
safe_builtins["__import__"] = _safe_import

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

open(result_path, "w", encoding="utf-8").write(json.dumps(res))
'''


class PythonVerifier:
    """Deterministic verifier for low-risk learning snippets.

    Hardening (v0.6.0): learner code runs under a trusted harness that owns the
    verdict (a learner `SystemExit`/early return cannot force a pass), builtins
    are an allowlist (open/eval/__import__ unreachable), output is capped at the
    source, and the child runs in its own process group (killed as a group on
    timeout). This is defense in depth, NOT a security boundary: untrusted or
    high-risk code MUST run in a container/VM with no network, a read-only FS and
    a non-root user. Resource limits are best-effort and degrade (never crash) on
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
                proc = subprocess.run(
                    [sys.executable, "-I", "-S", str(tdp / "harness.py"),
                     str(tdp / "config.json"), str(result_file)],
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
