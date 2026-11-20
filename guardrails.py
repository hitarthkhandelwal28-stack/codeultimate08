"""Configurable safety guardrails for CodeAI Ultimate.

This module lets maintainers tune operational limits without providing a
switch that disables core safety checks. It is intentionally dependency-free.

Usage:
    from guardrails import check_user_input, check_model_output

    result = check_user_input(user_text)
    if not result.allowed:
        return {"error": result.reason}, 400
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class GuardrailConfig:
    # These limits are configurable per deployment.
    max_input_chars: int = 12_000
    max_output_chars: int = 60_000
    block_prompt_injection: bool = True
    redact_secrets_in_output: bool = True

    @classmethod
    def from_env(cls) -> "GuardrailConfig":
        """Load safe operational settings; invalid values fall back to defaults."""
        def bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
            try:
                value = int(os.getenv(name, str(default)))
                return max(minimum, min(value, maximum))
            except (TypeError, ValueError):
                return default

        return cls(
            max_input_chars=bounded_int("AI_GUARDRAIL_MAX_INPUT_CHARS", 12_000, 500, 50_000),
            max_output_chars=bounded_int("AI_GUARDRAIL_MAX_OUTPUT_CHARS", 60_000, 1_000, 200_000),
            block_prompt_injection=os.getenv("AI_GUARDRAIL_BLOCK_INJECTION", "true").lower()
                not in {"0", "false", "no"},
            redact_secrets_in_output=os.getenv("AI_GUARDRAIL_REDACT_SECRETS", "true").lower()
                not in {"0", "false", "no"},
        )


@dataclass(frozen=True)
class GuardrailResult:
    allowed: bool
    text: str
    reason: Optional[str] = None
    flagged: bool = False


# Heuristic only: do not treat this as a complete prompt-injection detector.
_INJECTION_PATTERNS = (
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions", re.I),
    re.compile(r"reveal\s+(the\s+)?(system|developer)\s+prompt", re.I),
    re.compile(r"you\s+are\s+now\s+in\s+developer\s+mode", re.I),
)

_SECRET_PATTERNS = (
    # Common API token formats and credential assignments.
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*['\"]?[^\s'\";,]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
)


def check_user_input(text: str, config: Optional[GuardrailConfig] = None) -> GuardrailResult:
    """Validate user input length and flag common instruction-override attempts."""
    cfg = config or GuardrailConfig.from_env()
    if not isinstance(text, str):
        return GuardrailResult(False, "", "Input must be text.")
    if not text.strip():
        return GuardrailResult(False, text, "Please enter a message.")
    if len(text) > cfg.max_input_chars:
        return GuardrailResult(
            False, text[:cfg.max_input_chars],
            f"Input is too long (limit: {cfg.max_input_chars} characters)."
        )

    flagged = any(pattern.search(text) for pattern in _INJECTION_PATTERNS)
    if flagged and cfg.block_prompt_injection:
        return GuardrailResult(
            False, text,
            "This message appears to contain an instruction-override attempt. "
            "Remove that instruction and submit the legitimate coding request.",
            flagged=True,
        )
    return GuardrailResult(True, text, flagged=flagged)


def check_model_output(text: str, config: Optional[GuardrailConfig] = None) -> GuardrailResult:
    """Enforce output size and optionally redact obvious credential-like strings."""
    cfg = config or GuardrailConfig.from_env()
    if not isinstance(text, str):
        return GuardrailResult(False, "", "Model output must be text.")
    if len(text) > cfg.max_output_chars:
        return GuardrailResult(
            False, text[:cfg.max_output_chars],
            f"Model output exceeded the {cfg.max_output_chars}-character limit."
        )

    safe_text = text
    if cfg.redact_secrets_in_output:
        for pattern in _SECRET_PATTERNS:
            safe_text = pattern.sub("[REDACTED_SECRET]", safe_text)

    return GuardrailResult(True, safe_text, flagged=(safe_text != text))


def guardrail_status(config: Optional[GuardrailConfig] = None) -> dict:
    """Return non-secret configuration for an admin diagnostics page."""
    cfg = config or GuardrailConfig.from_env()
    return {
        "enabled": True,
        "max_input_chars": cfg.max_input_chars,
        "max_output_chars": cfg.max_output_chars,
        "prompt_injection_check": cfg.block_prompt_injection,
        "output_secret_redaction": cfg.redact_secrets_in_output,
        "core_protections_can_be_disabled": False,
    }
