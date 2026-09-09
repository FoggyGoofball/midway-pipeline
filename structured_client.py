#!/usr/bin/env python3
"""
structured_client.py — Instructor-wrapped, tenacity-hardened structured LLM client.
====================================================================================

ARCHITECTURAL STANDARD #1 (Output Validation):
    * All structured calls go through the ``instructor`` wrapper (``response_model``),
      so the model's output is validated against the Pydantic contracts in
      ``structured_schemas.py`` BEFORE it is returned to the pipeline.
    * ``tenacity`` provides automated error recovery: on a Pydantic
      ``ValidationError`` it retries with exponential backoff (max 3 attempts) and
      feeds the EXACT error message back to the LLM for self-correction.

ARCHITECTURAL STANDARD #2 (Cognitive Load Separation):
    * ``two_turn_extract`` implements the decoupled two-turn prompt: turn 1 asks for
      raw, unstructured chain-of-thought; turn 2 feeds that freeform analysis into
      the instructor-wrapped schema for clean extraction.

Transport:
    * Ollama exposes an OpenAI-compatible endpoint at ``{OLLAMA_HOST}/v1``.
      instructor's ``Mode.JSON`` is used (JSON-mode + message-appended schema) rather
      than function-calling/TOOLS, which Ollama's compatibility layer does not
      reliably support.

Dependencies:
    pip install instructor openai tenacity pydantic
"""

from __future__ import annotations

import json
import sys
from typing import Any, Optional, TypeVar

from pydantic import BaseModel, ValidationError

from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# Imported lazily inside __init__ so the module remains importable even when
# instructor/openai are not yet installed (the legacy pipeline must keep running).
T = TypeVar("T", bound=BaseModel)

MAX_ATTEMPTS: int = 3
BACKOFF_MULTIPLIER: float = 2.0
BACKOFF_MIN_SEC: float = 2.0
BACKOFF_MAX_SEC: float = 30.0

# The exact error text is re-inserted as a user turn so the model can see why
# its previous attempt failed and self-correct on the next attempt.
_SELF_CORRECTION_TEMPLATE = (
    "Your previous output failed schema validation. "
    "Fix the following problems and return ONLY a valid JSON object that "
    "conforms to the requested schema.\n\n"
    "Validation errors:\n{errors}"
)


def _recover_validation_error(exc: BaseException, schema: type[T]) -> Optional[ValidationError]:
    """Best-effort recovery of the exact Pydantic ValidationError from an
    instructor ``InstructorRetryException``.

    instructor wraps the last raw completion in ``InstructorRetryException``.
    We pull the raw content back out and re-validate it ourselves so tenacity
    can retry on a genuine ``pydantic.ValidationError`` (not just instructor's
    opaque wrapper).
    """
    completion = getattr(exc, "last_completion", None)
    raw: Optional[str] = None

    if completion is not None:
        # instructor's last_completion is a pydantic model with .choices
        choices = getattr(completion, "choices", None)
        if choices:
            message = getattr(choices[0], "message", None)
            raw = getattr(message, "content", None)
        # Some instructor versions expose the dump directly
        if raw is None:
            try:
                dumped = completion.model_dump()
                raw = dumped.get("choices", [{}])[0].get("message", {}).get("content")
            except Exception:
                raw = None

    if raw:
        try:
            schema.model_validate_json(raw)
        except ValidationError as ve:
            return ve
        except Exception:
            pass
    return None


def _format_error(exc: BaseException) -> str:
    """Produce the exact, machine-readable error text fed back to the LLM."""
    if isinstance(exc, ValidationError):
        return exc.json(indent=2)
    return str(exc)


class StructuredClient:
    """Instructor-wrapped client with tenacity-driven self-correction.

    Example:
        client = StructuredClient(model="qwen2.5-coder:7b")
        design = client.extract(
            schema=AttractionDesignOutput,
            system=ARCHITECT_SYSTEM,
            user="...",
        )
    """

    def __init__(
        self,
        model: str,
        base_url: str = "http://192.168.0.16:11434",
        temperature: float = 0.0,
        max_attempts: int = MAX_ATTEMPTS,
        mode: str = "JSON",
        num_ctx: Optional[int] = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.max_attempts = max_attempts
        self._mode = mode
        self._num_ctx = num_ctx

        try:
            from openai import OpenAI
            import instructor
        except ImportError as exc:  # pragma: no cover - dependency gate
            raise ImportError(
                "structured_client requires `openai` and `instructor`. "
                "Install with: pip install openai instructor tenacity"
            ) from exc

        self._raw = OpenAI(
            base_url=f"{self.base_url}/v1",
            api_key="ollama",
            # The openai SDK default timeout is 600s, but a Steam Deck cold-load
            # of the 9B model (evict + load, ~140-590s) plus generation can
            # exceed that, producing a false "Request timed out."  Allow 60 min
            # per request; streaming output keeps the operator informed of
            # progress, and the caller still falls back to the legacy parser on
            # a genuine failure.  Disable SDK-level retries — tenacity owns the
            # retry schedule above.
            timeout=3600.0,
            max_retries=0,
        )
        self._client = instructor.from_openai(
            self._raw,
            mode=getattr(instructor.Mode, self._mode.upper(), instructor.Mode.JSON),
        )
        # Error feedback accumulated across tenacity retries of a single logical call.
        self._pending_feedback: Optional[str] = None

    # -- Raw completion (used for turn 1 of the two-turn protocol) -----------

    def raw_chat(
        self,
        system: str,
        user: str,
        temperature: Optional[float] = None,
        max_tokens: int = 4096,
        stream_to_console: bool = True,
    ) -> str:
        """Freeform, unstructured completion — the chain-of-thought turn.

        When ``stream_to_console`` is True (default), chunks are written to
        stdout as they arrive so the operator can watch the model's thinking
        live instead of staring at a silent process.
        """
        resp = self._raw.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=self.temperature if temperature is None else temperature,
            max_tokens=max_tokens,
            stream=stream_to_console,
            extra_body={"num_ctx": self._num_ctx} if self._num_ctx else None,
        )
        if not stream_to_console:
            return (resp.choices[0].message.content or "").strip()

        chunks: list[str] = []
        for chunk in resp:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                chunks.append(delta.content)
                sys.stdout.write(delta.content)
                sys.stdout.flush()
        sys.stdout.write("\n")
        sys.stdout.flush()
        return "".join(chunks).strip()

    # -- Structured extraction ----------------------------------------------

    def _attempt(self, schema: type[T], messages: list[dict[str, str]], max_tokens: int = 2048) -> T:
        """One instructor attempt.  Returns a validated model or raises.

        ``max_retries=0`` confines instructor to a SINGLE LLM call so tenacity
        owns the total attempt count and the exponential backoff schedule.
        ``max_tokens`` caps the response so a rambling model cannot generate
        unbounded tokens and trip the 60-minute socket timeout.
        """
        try:
            return self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_model=schema,
                max_retries=0,
                max_tokens=max_tokens,
                temperature=self.temperature,
                extra_body={"num_ctx": self._num_ctx} if self._num_ctx else None,
            )
        except Exception as exc:  # instructor raises InstructorRetryException on validation failure
            validation_error = _recover_validation_error(exc, schema)
            if validation_error is not None:
                # Record the exact error text for the next attempt's self-correction.
                self._pending_feedback = _format_error(validation_error)
                print(
                    "  [StructuredClient] ⚠ Validation failed — retrying with self-correction:\n"
                    f"{validation_error}",
                    flush=True,
                )
                raise validation_error from exc
            # Not a validation failure (transport / HTTP / etc.) — re-raise as-is.
            self._pending_feedback = _format_error(exc)
            raise

    def extract(
        self,
        schema: type[T],
        system: str,
        user: str,
        validation_context: Optional[str] = None,
        max_tokens: int = 2048,
    ) -> T:
        """Extract a validated ``schema`` instance, retrying on ValidationError.

        Retry policy (tenacity):
            * stop_after_attempt(3)
            * wait_exponential(multiplier=2, min=2s, max=30s)
            * retry only on pydantic.ValidationError
        Each retry injects the EXACT previous error into a new user message.
        """
        base_messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        if validation_context:
            base_messages.insert(
                1, {"role": "user", "content": f"Schema context:\n{validation_context}"}
            )

        self._pending_feedback = None
        return self._extract_with_retry(schema, base_messages, max_tokens=max_tokens)

    @retry(
        stop=stop_after_attempt(MAX_ATTEMPTS),
        wait=wait_exponential(
            multiplier=BACKOFF_MULTIPLIER, min=BACKOFF_MIN_SEC, max=BACKOFF_MAX_SEC
        ),
        retry=retry_if_exception_type(ValidationError),
        reraise=True,
        before_sleep=before_sleep_log(None, log_level=20),  # INFO-level "Retrying ... in Xs"
    )
    def _extract_with_retry(
        self, schema: type[T], base_messages: list[dict[str, str]], max_tokens: int = 2048
    ) -> T:
        """tenacity-decorated worker.  Appends pending feedback on each retry."""
        messages = list(base_messages)
        if self._pending_feedback:
            messages.append(
                {
                    "role": "user",
                    "content": _SELF_CORRECTION_TEMPLATE.format(errors=self._pending_feedback),
                }
            )
        return self._attempt(schema, messages, max_tokens=max_tokens)

    # -- Standard 2: two-turn cognitive-load separation ---------------------

    def two_turn_extract(
        self,
        schema: type[T],
        cot_system: str,
        user: str,
        extract_system: Optional[str] = None,
        cot_temperature: float = 0.7,
        validation_context: Optional[str] = None,
    ) -> tuple[T, str]:
        """Two-turn protocol.

        Turn 1 — ``cot_system`` + ``user`` produce RAW, unstructured analysis
                 (no formatting pressure, no schema in context).
        Turn 2 — the freeform analysis is appended to ``extract_system`` and the
                 instructor-wrapped Pydantic schema extracts clean structured data.

        Returns ``(validated_model, raw_chain_of_thought)``.
        """
        print("  [StructuredClient] Turn 1/2 — generating analysis (streaming below)...", flush=True)
        raw_cot = self.raw_chat(cot_system, user, temperature=cot_temperature)
        print(
            f"  [StructuredClient] Turn 1/2 complete ({len(raw_cot)} chars of analysis).",
            flush=True,
        )

        extraction_system = extract_system or cot_system
        extraction_user = (
            f"## Original Request\n{user}\n\n"
            f"## Raw Analysis (chain-of-thought)\n{raw_cot}\n\n"
            "Extract the structured result now, following the schema exactly."
        )
        print("  [StructuredClient] Turn 2/2 — extracting structured result...", flush=True)
        model = self.extract(
            schema,
            system=extraction_system,
            user=extraction_user,
            validation_context=validation_context,
        )
        print("  [StructuredClient] Turn 2/2 complete.", flush=True)
        return model, raw_cot


# ---------------------------------------------------------------------------
#  Convenience: schema-registry driven extraction.
# ---------------------------------------------------------------------------

def extract_named(
    client: StructuredClient,
    schema_name: str,
    system: str,
    user: str,
    **kwargs: Any,
) -> BaseModel:
    """Resolve a schema from ``structured_schemas.SCHEMA_REGISTRY`` and extract."""
    from structured_schemas import get_schema

    return client.extract(get_schema(schema_name), system=system, user=user, **kwargs)


if __name__ == "__main__":
    import sys

    from structured_schemas import AttractionDesignOutput

    cli = StructuredClient(model="qwen2.5-coder:7b", temperature=0.0)
    prompt = sys.argv[1] if len(sys.argv) > 1 else "A skeeball ramp with a jackpot lane"
    design, cot = cli.two_turn_extract(
        schema=AttractionDesignOutput,
        cot_system="You are a game-attraction architect. Analyze the request freely.",
        user=prompt,
        extract_system="Extract the attraction design into the given JSON schema.",
    )
    print(json.dumps(design.model_dump(), indent=2))
