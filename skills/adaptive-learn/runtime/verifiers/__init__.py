from __future__ import annotations

from .python_runner import PythonVerifier, PythonVerificationResult


class VerifierRegistry:
    @staticmethod
    def verify_python(domain_context: dict, code: str, *, expected_stdout: str | None = None, trusted_test_code: str | None = None) -> dict:
        verifier = domain_context.get("verifier") or {}
        if verifier.get("id") != "python-low-risk":
            return {"available": False, "reason": "python_verifier_not_enabled_for_adapter"}
        cfg = verifier.get("config") or {}
        runner = PythonVerifier(
            allowed_imports=cfg.get("allowed_imports"),
            timeout_sec=float(cfg.get("timeout_sec", 2.0)),
            memory_mb=int(cfg.get("memory_mb", 256)),
            max_output_bytes=int(cfg.get("max_output_bytes", 64000)),
        )
        return {
            "available": True,
            "verifier_id": "python-low-risk",
            "result": runner.verify(
                code,
                expected_stdout=expected_stdout,
                trusted_test_code=trusted_test_code,
            ).to_dict(),
        }


__all__ = ["PythonVerifier", "PythonVerificationResult", "VerifierRegistry"]
