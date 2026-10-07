# Execution Backends — real code, any language

The browser Academy UI runs a limited in-page sandbox because a static page
cannot host language runtimes. **That is a demo constraint, not the product.**
Real execution happens in a backend, and the platform never tells a learner a
language "doesn't work here".

## Backends (`runtime/execution.py`)

| Backend | Use | Isolation |
|---------|-----|-----------|
| `LocalToolchainBackend` | dev / single operator | OS resource limits, process group, temp cwd, timeout |
| `ContainerBackend` | **production, untrusted/multi-tenant** | one throwaway container per run: `--network none`, `--read-only`, non-root, `--cap-drop ALL`, cpu/memory/pids caps, timeout |
| `_AutoBackend` (default) | per language: local toolchain if installed, else container | — |

Select with `ADAPTIVE_EXECUTOR=auto|local|container` or `select_backend(prefer=...)`.

## Languages

Each language is a `LanguageSpec` (source filename, compile/run steps, local
binary to detect, production container image). Shipped: python, javascript (node),
typescript (deno), go, java, kotlin, rust, c, c++, ruby, php, sql (sqlite). Add a
language by adding one spec — the registry drives both backends.

If no backend can run a language, the result is `available: false` with a `detail`
that names the fix (install the toolchain, or enable the container image) — never
"unsupported".

## Checks

- Generic: `run_exercise(lang, source, expect_stdout=..., stdin=...)` compiles+runs
  and compares stdout / exit status for any language.
- Web frameworks: `verify_fastapi_app(app_code, checks)` runs a real FastAPI app
  through `fastapi.testclient.TestClient` (httpx, in-process — real routing,
  validation and serialization, no sockets). The same pattern extends to other
  frameworks via their test clients.

## CLI

```bash
alearn languages                        # list languages + prod images
alearn run-exercise --lang go --code-file sol.go --expect-stdout 42
alearn verify-fastapi --code-file app.py --checks-file checks.json
```

## Curriculum source

Technical roadmaps (e.g. roadmap.sh) are an authoritative curriculum input for the
Curriculum Authority Gate: the ordered topics become the course's module/lesson
backbone, which the LLM adapts rather than invents.
