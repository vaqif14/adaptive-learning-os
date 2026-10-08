from __future__ import annotations

from dataclasses import dataclass, asdict, field
from pathlib import Path
from shutil import which
import json
import os
import subprocess
import sys
import tempfile

from .utils import run_capped

# Language-agnostic execution backends.
#
# A learner may want Python, Go, Kotlin, Rust, JavaScript, SQL — anything. The
# platform NEVER says "that language doesn't work here". Each language is a
# LanguageSpec (how to run/compile it); each submission runs through a backend:
#
#   LocalToolchainBackend - uses the toolchain installed on this host (dev/local,
#                           single operator). Fast, no isolation beyond OS limits.
#   ContainerBackend      - the PRODUCTION executor: one disposable container per
#                           run, no network, read-only rootfs, non-root, capped.
#                           Covers languages whose toolchain isn't installed locally.
#
# If neither can run a language, the result says exactly what to install or enable
# — never "unsupported".

DEFAULT_TIMEOUT = 10.0


@dataclass
class LanguageSpec:
    id: str
    display: str
    filename: str                       # entry source file name in the work dir
    steps: list[list[str]]              # command templates; {file},{bin} substituted; run in order
    local_bin: str                      # binary used to detect local availability
    image: str                          # container image for the production backend
    bin_name: str = "prog"              # compiled-artifact name for {bin}

    def materialize(self, is_container: bool) -> list[list[str]]:
        out = []
        for step in self.steps:
            out.append([tok.replace("{file}", self.filename).replace("{bin}", ("./" + self.bin_name)) for tok in step])
        return out

    def to_dict(self):
        return {"id": self.id, "display": self.display, "image": self.image}


# Registry — extend by adding a spec. Single-command languages just name the runner;
# compiled ones list a compile step then a run step.
LANGUAGES: dict[str, LanguageSpec] = {
    "python":     LanguageSpec("python", "Python", "main.py", [["python3", "{file}"]], "python3", "python:3.12-slim"),
    "javascript": LanguageSpec("javascript", "JavaScript (Node)", "main.js", [["node", "{file}"]], "node", "node:20-slim"),
    "typescript": LanguageSpec("typescript", "TypeScript (Deno)", "main.ts", [["deno", "run", "-q", "{file}"]], "deno", "denoland/deno:latest"),
    "go":         LanguageSpec("go", "Go", "main.go", [["go", "run", "{file}"]], "go", "golang:1.22-alpine"),
    "java":       LanguageSpec("java", "Java", "Main.java", [["java", "{file}"]], "java", "eclipse-temurin:17"),
    "kotlin":     LanguageSpec("kotlin", "Kotlin", "main.kt", [["kotlinc", "{file}", "-include-runtime", "-d", "app.jar"], ["java", "-jar", "app.jar"]], "kotlinc", "zenika/kotlin:1.9-jdk17"),
    "rust":       LanguageSpec("rust", "Rust", "main.rs", [["rustc", "-O", "{file}", "-o", "{bin}"], ["{bin}"]], "rustc", "rust:1-slim"),
    "c":          LanguageSpec("c", "C", "main.c", [["cc", "{file}", "-o", "{bin}"], ["{bin}"]], "cc", "gcc:13"),
    "cpp":        LanguageSpec("cpp", "C++", "main.cpp", [["c++", "{file}", "-o", "{bin}"], ["{bin}"]], "c++", "gcc:13"),
    "ruby":       LanguageSpec("ruby", "Ruby", "main.rb", [["ruby", "{file}"]], "ruby", "ruby:3.3-slim"),
    "php":        LanguageSpec("php", "PHP", "main.php", [["php", "{file}"]], "php", "php:8.3-cli"),
    "sql":        LanguageSpec("sql", "SQL (SQLite)", "main.sql", [["sh", "-c", "sqlite3 :memory: < {file}"]], "sqlite3", "keinos/sqlite3:latest"),
}


def language(lang_id: str) -> LanguageSpec:
    if lang_id not in LANGUAGES:
        raise KeyError(f"unknown_language:{lang_id}")
    return LANGUAGES[lang_id]


@dataclass
class ExecResult:
    ok: bool
    available: bool
    backend: str
    language: str
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None
    timed_out: bool = False
    detail: str | None = None

    def to_dict(self):
        return asdict(self)


def _resource_limits(memory_mb: int = 512):  # pragma: no cover - child only
    try:
        import resource
    except Exception:
        return
    for name, val in (("RLIMIT_CPU", (8, 8)), ("RLIMIT_FSIZE", (20_000_000, 20_000_000))):
        try:
            resource.setrlimit(getattr(resource, name), val)
        except (ValueError, OSError, AttributeError):
            pass
    mem = memory_mb * 1024 * 1024
    # RLIMIT_DATA (heap commit), NOT RLIMIT_AS: Go and V8 reserve large virtual
    # address ranges up front and die under an AS cap ("failed to reserve page
    # summary memory" / Isolate::Init), while committing little real memory.
    # ACCEPTED TRADE-OFF: DATA is a weaker memory control than AS (notably on
    # macOS). The compensating controls here are CPU/FSIZE/NOFILE/CORE caps,
    # the parent-side timeout with process-group kill, and — for any untrusted
    # or multi-tenant use — the HARD boundary is ContainerBackend's cgroup
    # limits (--memory/--pids-limit), not these best-effort rlimits.
    for name, val in (("RLIMIT_DATA", (mem, mem)),
                      ("RLIMIT_NOFILE", (256, 256)),
                      ("RLIMIT_CORE", (0, 0))):
        try:
            resource.setrlimit(getattr(resource, name), val)
        except (ValueError, OSError, AttributeError):
            pass


class LocalToolchainBackend:
    name = "local_toolchain"

    # 1024, not 512: node/V8 isolate init commits >512MB against RLIMIT_DATA on
    # linux/amd64 and dies (v8::Isolate::Initialize trap, or a silent kill when
    # near-limit GC thrash hits the CPU/wall caps). 512 was an intermittent CI
    # failure at the edge; 1GB leaves headroom while CPU/FSIZE/timeout still cap
    # runaway programs. The hard memory boundary remains ContainerPolicy.memory.
    def __init__(self, *, timeout: float = DEFAULT_TIMEOUT, memory_mb: int = 1024):
        self.timeout = float(timeout)
        self.memory_mb = int(memory_mb)

    def available_for(self, spec: LanguageSpec) -> bool:
        return which(spec.local_bin) is not None

    def run(self, spec: LanguageSpec, files: dict[str, str], *, stdin: str | None = None) -> ExecResult:
        if not self.available_for(spec):
            return ExecResult(False, False, self.name, spec.id,
                              detail=f"toolchain_not_installed_locally:{spec.local_bin}. "
                                     f"Run via the container backend (image {spec.image}) or install {spec.display}.")
        posix = os.name == "posix"
        with tempfile.TemporaryDirectory(prefix=f"alearn-{spec.id}-") as td:
            tdp = Path(td)
            for rel, content in files.items():
                (tdp / rel).parent.mkdir(parents=True, exist_ok=True)
                (tdp / rel).write_text(content, encoding="utf-8")
            last = None
            for cmd in spec.materialize(is_container=False):
                try:
                    last = run_capped(
                        cmd, cwd=td,
                        env={"PATH": os.environ.get("PATH", ""),
                             # keep real HOME so language user-site/toolchains resolve
                             # (Python user-site, etc.); isolate Go caches explicitly.
                             "HOME": os.environ.get("HOME", td),
                             "GOCACHE": td + "/.gocache", "GOPATH": td + "/.gopath",
                             "GOFLAGS": "-mod=mod"},
                        input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                        timeout=self.timeout,
                        preexec_fn=(lambda: _resource_limits(self.memory_mb)) if posix else None,
                        start_new_session=posix,
                    )
                except subprocess.TimeoutExpired as e:
                    return ExecResult(False, True, self.name, spec.id,
                                      stdout=e.stdout or "", stderr=e.stderr or "", timed_out=True, detail="timed_out")
                except FileNotFoundError as e:
                    return ExecResult(False, False, self.name, spec.id, detail=f"command_not_found:{e}")
                if last.returncode != 0:  # a failing compile/run step stops the chain
                    return ExecResult(False, True, self.name, spec.id,
                                      stdout=last.stdout, stderr=last.stderr, returncode=last.returncode)
            return ExecResult(True, True, self.name, spec.id,
                              stdout=last.stdout if last else "", stderr=last.stderr if last else "",
                              returncode=0)


@dataclass
class ContainerPolicy:
    network: str = "none"
    read_only_rootfs: bool = True
    user: str = "nobody"
    cpus: str = "1.0"
    memory: str = "512m"
    pids_limit: int = 256
    timeout_sec: int = 20
    workdir: str = "/work"

    def docker_args(self) -> list[str]:
        return ["run", "--rm", "--network", self.network, "--read-only",
                "--tmpfs", f"{self.workdir}:rw,exec,size=64m,mode=1777", "--user", self.user,
                "--cpus", self.cpus, "--memory", self.memory, "--pids-limit", str(self.pids_limit),
                "--workdir", self.workdir, "--cap-drop", "ALL", "--security-opt", "no-new-privileges"]

    def to_dict(self):
        return asdict(self)


class ContainerBackend:
    """Production executor: one throwaway container per run, any language."""
    name = "container"

    def __init__(self, policy: ContainerPolicy | None = None, docker_bin: str = "docker"):
        self.policy = policy or ContainerPolicy()
        self.docker_bin = docker_bin

    def available_for(self, spec: LanguageSpec) -> bool:
        return which(self.docker_bin) is not None

    def run(self, spec: LanguageSpec, files: dict[str, str], *, stdin: str | None = None) -> ExecResult:
        if which(self.docker_bin) is None:
            return ExecResult(False, False, self.name, spec.id,
                              detail="container_executor_unavailable: Docker not found. "
                                     "Install Docker or point ADAPTIVE_EXECUTOR at a remote runner.")
        with tempfile.TemporaryDirectory(prefix=f"alearn-c-{spec.id}-") as td:
            tdp = Path(td)
            for rel, content in files.items():
                (tdp / rel).parent.mkdir(parents=True, exist_ok=True)
                (tdp / rel).write_text(content, encoding="utf-8")
            # Source is bind-mounted READ-ONLY at /src; /work is a separate writable
            # tmpfs workdir (compilers/caches write there). We copy src -> work first,
            # so no mount collides with the workdir (fixes "Duplicate mount point").
            steps = " && ".join(" ".join(_shq(t) for t in cmd) for cmd in spec.materialize(is_container=True))
            chain = f"cp -rL /src/. {self.policy.workdir}/ && {steps}"
            args = [self.docker_bin, *self.policy.docker_args(),
                    "-v", f"{td}:/src:ro", spec.image, "sh", "-c", chain]
            try:
                proc = subprocess.run(args, input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                      timeout=self.policy.timeout_sec + 30)
            except subprocess.TimeoutExpired as e:
                return ExecResult(False, True, self.name, spec.id, stdout=e.stdout or "", stderr=e.stderr or "",
                                  timed_out=True, detail="timed_out")
            return ExecResult(proc.returncode == 0, True, self.name, spec.id,
                              stdout=proc.stdout, stderr=proc.stderr, returncode=proc.returncode)


def _shq(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'" if any(c in s for c in " <>|&;$`\"'") else s


def select_backend(prefer: str | None = None, **kw):
    """Production prefers 'container'; local/dev uses host toolchains. Returns a
    backend whose .run(spec, files) is called by the caller."""
    prefer = prefer or os.environ.get("ADAPTIVE_EXECUTOR", "auto")
    if prefer == "container":
        return ContainerBackend()
    if prefer == "local":
        return LocalToolchainBackend(**kw)
    return _AutoBackend(**kw)   # per-language: local if toolchain present, else container


class _AutoBackend:
    name = "auto"

    def __init__(self, **kw):
        self.local = LocalToolchainBackend(**kw)
        self.container = ContainerBackend()

    def available_for(self, spec):
        return self.local.available_for(spec) or self.container.available_for(spec)

    def run(self, spec, files, *, stdin=None):
        if self.local.available_for(spec):
            return self.local.run(spec, files, stdin=stdin)
        return self.container.run(spec, files, stdin=stdin)


# --- generic exercise check (any language) -----------------------------------

@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""

    def to_dict(self):
        return asdict(self)


def run_exercise(lang_id: str, source: str, *, extra_files: dict | None = None,
                 stdin: str | None = None, expect_stdout: str | None = None,
                 backend=None) -> dict:
    """Compile+run a submission in any language; optionally assert stdout.

    Returns {available, backend, language, ran, passed, stdout, stderr, detail}.
    'available' False means no backend can run this language (with the fix in
    'detail') — the platform never claims a language is unsupported.
    """
    spec = language(lang_id)
    backend = backend or select_backend()
    files = {spec.filename: source}
    if extra_files:
        files.update(extra_files)
    res = backend.run(spec, files, stdin=stdin)
    out = {"available": res.available, "backend": res.backend, "language": spec.id,
           "ran": res.ok, "stdout": res.stdout, "stderr": res.stderr[-1200:],
           "timed_out": res.timed_out, "detail": res.detail}
    if res.available and expect_stdout is not None:
        out["passed"] = res.ok and res.stdout.strip() == expect_stdout.strip()
        out["expected"] = expect_stdout.strip()
    else:
        out["passed"] = res.ok
    return out


# --- Python/FastAPI web adapter (real framework, in-process TestClient) -------

_RESULT_MARK = "__ALEARN_RESULT__"


def verify_fastapi_app(app_code: str, checks: list[dict], *, backend=None) -> dict:
    backend = backend or select_backend()
    spec = language("python")
    harness = _FASTAPI_HARNESS.replace("# __APP_CODE__", app_code).replace("__CHECKS_JSON__", json.dumps(checks))
    res = backend.run(spec, {spec.filename: harness})
    if not res.available:
        return {"available": False, "backend": res.backend, "detail": res.detail, "results": []}
    parsed = None
    for line in res.stdout.splitlines():
        if line.startswith(_RESULT_MARK):
            parsed = json.loads(line[len(_RESULT_MARK):]); break
    if parsed is None:
        stderr = (res.stderr or "").strip()
        missing = "ModuleNotFoundError" in stderr and "fastapi" in stderr
        return {"available": not missing, "backend": res.backend,
                "detail": ("fastapi_not_installed_in_backend: add fastapi+httpx to the executor image"
                           if missing else (res.detail or "no_result_from_harness")),
                "stderr": stderr[-800:], "results": []}
    return {"available": True, "backend": res.backend,
            "all_passed": bool(parsed) and all(c["passed"] for c in parsed), "results": parsed}


_FASTAPI_HARNESS = r'''
import json, sys
_RESULT_MARK = "__ALEARN_RESULT__"
_results = []
try:
    from fastapi.testclient import TestClient
except Exception as e:
    print("IMPORT_ERROR:" + repr(e), file=sys.stderr); raise
# __APP_CODE__
try:
    client = TestClient(app)  # noqa: F821
except NameError:
    print("NO_APP: define `app = FastAPI()`", file=sys.stderr); sys.exit(1)
_CHECKS = json.loads(r"""__CHECKS_JSON__""")
for chk in _CHECKS:
    name = chk.get("name", chk.get("path", "check"))
    try:
        m = chk.get("method", "GET").lower()
        resp = getattr(client, m)(chk["path"], json=chk.get("json")) if chk.get("json") is not None else getattr(client, m)(chk["path"])
        ok = True; why = []
        if "expect_status" in chk and resp.status_code != chk["expect_status"]:
            ok = False; why.append("status %s != %s" % (resp.status_code, chk["expect_status"]))
        if "expect_json" in chk and resp.json() != chk["expect_json"]:
            ok = False; why.append("json %r != %r" % (resp.json(), chk["expect_json"]))
        _results.append({"name": name, "passed": ok, "detail": "; ".join(why)})
    except Exception as e:
        _results.append({"name": name, "passed": False, "detail": "%s: %s" % (type(e).__name__, e)})
print(_RESULT_MARK + json.dumps(_results))
'''
