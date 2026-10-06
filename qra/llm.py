"""Calling the model (through LangChain), and saving/replaying its responses.

Live calls go through LangChain: a ChatPromptTemplate piped into ChatAnthropic (`PROMPT | chat`).
We do not use `with_structured_output()`: by default it forces a tool call, which claude-opus-5-5
rejects. Instead we pass the API's own structured-output setting (`output_config` with our JSON
schema) to ChatAnthropic, and validate the JSON ourselves with pydantic.

Every live response is saved to replays/<purpose>/<request-hash>.json together with the exact
request. In replay mode we look the response up by the hash (fingerprint) of the request we are
about to send. So a replay is only used when the prompt, documents, question and settings are
exactly the same as when it was recorded. If there is no recording, the call fails visibly; we
never make up an answer. Before each live call we check that LangChain will send exactly the
request we fingerprint and save.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate
from pydantic import ValidationError

from . import config

# The values are inserted as plain text, so braces or tags inside documents are not interpreted.
PROMPT = ChatPromptTemplate.from_messages([("system", "{system}"), ("human", "{user}")])


@dataclass
class ModelResult:
    """The outcome of one model call, successful or not, and where the answer came from."""
    ok: bool
    provenance: str            # live | replay | simulated | test-fake
    purpose: str               # draft_answer | verify_answer
    prompt_name: str
    model_requested: str
    request_hash: str = ""
    data: dict | None = None   # the validated JSON answer (only when ok)
    raw_text: str | None = None
    error_type: str | None = None  # api_error | invalid_output | refusal | truncated | replay_miss
    error_message: str | None = None
    replay_path: str | None = None
    model_served: str | None = None
    response_id: str | None = None
    request_id: str | None = None
    stop_reason: str | None = None
    usage: dict | None = None
    recorded_at: str | None = None

    def as_record(self) -> dict:
        """The columns saved in the model_calls table."""
        record = asdict(self)
        record["parsed"] = record.pop("data")
        record["ok"] = int(self.ok)
        return record


def build_request(model: str, system: str, user: str, schema: dict, effort: str) -> dict:
    """The exact Messages API request we send (and save next to the response)."""
    return {
        "model": model,
        "max_tokens": config.MAX_TOKENS,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        # json_schema: the API only returns JSON that matches our schema.
        "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        "betas": [config.FALLBACK_BETA],
        "fallbacks": "default",
    }


def request_hash(request: dict) -> str:
    """A fingerprint of the request; identical requests give identical hashes."""
    canonical = json.dumps(request, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def parse_output(text: str | None, output_model) -> tuple[dict | None, str | None]:
    """Check the model's JSON against the expected shape. Returns (data, error message)."""
    if text is None:
        return None, "Response contained no text block."
    try:
        return output_model.model_validate_json(text).model_dump(), None
    except ValidationError as error:
        return None, f"Output failed schema validation: {error.errors()[:3]}"


def _relative(path: Path) -> str:
    """Show a path relative to the project folder when possible (shorter and machine-independent)."""
    return str(path.relative_to(config.ROOT)) if path.is_relative_to(config.ROOT) else str(path)


def langchain_call(request: dict, api_key: str | None = None) -> tuple[ChatAnthropic, dict]:
    """Build the LangChain chat model and prompt inputs that send exactly `request`.

    Returns (chat model, inputs for PROMPT). The live call is then `(PROMPT | chat).invoke(inputs)`.
    """
    chat = ChatAnthropic(
        model=request["model"],
        max_tokens=request["max_tokens"],
        betas=request["betas"],                          # beta features (here: refusal fallback)
        output_config=request["output_config"],          # effort + our JSON schema (structured output)
        model_kwargs={"fallbacks": request["fallbacks"]},
        max_retries=config.MAX_RETRIES,
        default_request_timeout=config.REQUEST_TIMEOUT_SECONDS,
        **({"api_key": api_key} if api_key else {}),     # default: ANTHROPIC_API_KEY from the environment
    )
    inputs = {"system": request["system"], "user": request["messages"][0]["content"]}
    return chat, inputs


def payload_langchain_would_send(chat: ChatAnthropic, inputs: dict) -> dict:
    """The exact API request LangChain builds for these inputs (nothing is sent)."""
    return chat._get_request_payload(PROMPT.invoke(inputs).to_messages())


def response_dict(message) -> dict:
    """Turn LangChain's AIMessage back into the API's response shape (the shape stored in replays/)."""
    meta = message.response_metadata
    content = message.content
    if isinstance(content, str):  # LangChain flattens a single text block to a plain string
        content = [{"type": "text", "text": content}]
    return {"id": meta.get("id"), "model": meta.get("model_name") or meta.get("model"),
            "stop_reason": meta.get("stop_reason"), "stop_details": meta.get("stop_details"),
            "usage": meta.get("usage"), "content": content}


class AnthropicJsonModel:
    """Claude through LangChain (ChatAnthropic), in replay, live or auto mode (see config.py)."""

    def __init__(self, mode: str = config.DEFAULT_MODE, replay_dir: Path = config.REPLAY_DIR, model: str = config.MODEL):
        """Choose the mode (replay, live or auto), where recordings are saved, and which model to use."""
        if mode not in config.MODES:
            raise ValueError(f"Unknown model mode {mode!r}; expected one of {config.MODES}")
        self.mode = mode
        self.replay_dir = Path(replay_dir)
        self.model = model

    def complete_json(self, *, purpose, prompt_name, system, user, schema, output_model, effort, context) -> ModelResult:
        """Get a JSON answer from the model for one prompt.

        In replay/auto mode a saved response is used if one exists for this exact request; otherwise
        (live/auto) the request is sent through LangChain and the response is saved. Always returns a
        ModelResult: check `.ok` before using `.data`.
        """
        request = build_request(self.model, system, user, schema, effort)
        digest = request_hash(request)
        path = self.replay_dir / purpose / f"{digest[:24]}.json"
        result = ModelResult(ok=False, provenance=self.mode, purpose=purpose, prompt_name=prompt_name,
                             model_requested=self.model, request_hash=digest)

        if self.mode in ("replay", "auto") and path.exists():
            # 1. A saved real response exists: use it.
            saved = json.loads(path.read_text(encoding="utf-8"))
            result.provenance = "replay"
            result.recorded_at = saved.get("recorded_at")
            result.request_id = saved.get("request_id")
            result.replay_path = _relative(path)
            response = saved["response"]
        elif self.mode == "replay":
            # 2. Replay mode but nothing saved: fail visibly.
            result.error_type = "replay_miss"
            result.error_message = (f"No saved response for request {digest[:24]} ({purpose}). "
                                    "Run with --mode auto or live to record one.")
            return result
        else:
            # 3. Call the API and save the response for later replay.
            result.provenance = "live"
            response = self._call_api(request, result)
            if response is None:
                return result  # result already holds the error
            result.recorded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self._save_recording(path, request, response, result, context)

        return self._read_response(result, response, output_model)

    def _call_api(self, request: dict, result: ModelResult) -> dict | None:
        """Send the request through LangChain. On failure, fill in result.error_* and return None."""
        import anthropic  # LangChain uses the Anthropic SDK underneath, so its error types reach us

        try:
            chat, inputs = langchain_call(request)
            # Safety check: the request we fingerprint and save must be exactly what LangChain sends.
            if payload_langchain_would_send(chat, inputs) != request:
                result.error_type = "api_error"
                result.error_message = "LangChain would send a different request than the one we record; call not made."
                return None
            message = (PROMPT | chat).invoke(inputs)  # LangChain chain: prompt template -> Claude
        except anthropic.AuthenticationError as error:
            result.error_type, result.error_message = "api_error", f"Authentication failed (set ANTHROPIC_API_KEY): {error.message}"
            return None
        except anthropic.APIStatusError as error:  # 400, 429, 500 ... (the SDK has already retried 429/5xx)
            result.error_type, result.error_message = "api_error", f"API error {error.status_code}: {error.message}"
            return None
        except anthropic.APIConnectionError as error:  # network problem or timeout
            result.error_type, result.error_message = "api_error", f"Model API unreachable: {error.__class__.__name__}"
            return None
        except Exception as error:  # e.g. no credentials configured at all
            result.error_type, result.error_message = "api_error", f"{error.__class__.__name__}: {error}"
            return None
        return response_dict(message)

    def _save_recording(self, path: Path, request: dict, response: dict, result: ModelResult, context: dict) -> None:
        """Save the request and the unedited response as a JSON file, so the call can be replayed later."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "provenance": "recorded-live-response",
            "note": "Real response from the Anthropic Messages API, saved for offline replay. Not edited.",
            "recorded_at": result.recorded_at,
            "sdk": f"langchain-anthropic {version('langchain-anthropic')} (anthropic-python {version('anthropic')})",
            "request_hash": result.request_hash,
            "request_id": result.request_id,
            "context": context,
            "request": request,
            "response": response,
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        result.replay_path = _relative(path)

    @staticmethod
    def _read_response(result: ModelResult, response: dict, output_model) -> ModelResult:
        """Turn the API response (live or replayed) into a checked result."""
        result.model_served = response.get("model")
        result.response_id = response.get("id")
        result.stop_reason = response.get("stop_reason")
        result.usage = response.get("usage")

        if result.stop_reason == "refusal":
            category = (response.get("stop_details") or {}).get("category")
            result.error_type, result.error_message = "refusal", f"Model declined (category: {category})."
            return result
        if result.stop_reason == "max_tokens":
            result.error_type, result.error_message = "truncated", "Response hit max_tokens before the JSON was complete."
            return result

        # The answer is the first text block (thinking blocks come before it).
        result.raw_text = next((b.get("text") for b in response.get("content", []) if b.get("type") == "text"), None)
        result.data, error = parse_output(result.raw_text, output_model)
        if error:
            result.error_type, result.error_message = "invalid_output", error
            return result
        result.ok = True
        return result


class SimulatedFailures:
    """Test helper: wraps a real model client and makes chosen questions fail on purpose.

    Simulated results are labelled provenance="simulated" and are never saved to replays/.
    """

    KINDS = ("invalid_output", "api_error", "fabricated_citation")

    def __init__(self, inner, failures: dict[str, str]):
        """Wrap a real model client; `failures` maps a question id to the kind of failure to simulate."""
        unknown = set(failures.values()) - set(self.KINDS)
        if unknown:
            raise ValueError(f"Unknown simulated failure kinds: {sorted(unknown)}")
        self.inner = inner
        self.failures = failures  # question id -> kind of failure

    def complete_json(self, *, purpose, prompt_name, system, user, schema, output_model, effort, context) -> ModelResult:
        """Return a labelled simulated failure for the chosen questions; pass everything else to the real client."""
        kind = self.failures.get(context.get("question_id", ""))
        if purpose != "draft_answer" or kind is None:
            return self.inner.complete_json(purpose=purpose, prompt_name=prompt_name, system=system, user=user,
                                            schema=schema, output_model=output_model, effort=effort, context=context)

        result = ModelResult(ok=False, provenance="simulated", purpose=purpose, prompt_name=prompt_name,
                             model_requested="simulated-failure", request_hash="simulated")
        if kind == "api_error":
            result.error_type, result.error_message = "api_error", "SIMULATED: model API unavailable (connection refused)."
        elif kind == "invalid_output":
            result.raw_text = '{"status": "answered", "answer": "Subscriptions are billed'  # cut off: not valid JSON
            _, error = parse_output(result.raw_text, output_model)
            result.error_type, result.error_message = "invalid_output", f"SIMULATED: {error}"
        else:  # fabricated_citation: well-formed JSON that cites a passage that does not exist
            result.raw_text = json.dumps({
                "status": "answered",
                "answer": "Subscriptions are billed weekly.",
                "citations": [{"passage_id": "EXPORT-v9:p1", "excerpt": "billed weekly"}],
                "conflicts": [],
                "unresolved_reason": "",
            })
            result.data, _ = parse_output(result.raw_text, output_model)
            result.ok = True
        return result
