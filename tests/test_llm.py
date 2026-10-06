import json

from qra.drafting import DRAFT_SCHEMA, DraftOutput
from qra.llm import AnthropicJsonModel, build_request, request_hash


def test_request_hash_is_stable_and_sensitive_to_prompt_changes():
    a = build_request("m", "system", "user", DRAFT_SCHEMA, "medium")
    assert request_hash(a) == request_hash(build_request("m", "system", "user", DRAFT_SCHEMA, "medium"))
    assert request_hash(a) != request_hash(build_request("m", "system v2", "user", DRAFT_SCHEMA, "medium"))


def test_replay_uses_recorded_response_and_labels_it(tmp_path):
    model = AnthropicJsonModel(mode="replay", replay_dir=tmp_path, model="m")
    request = build_request("m", "sys", "usr", DRAFT_SCHEMA, "medium")
    digest = request_hash(request)
    output = {"status": "unresolved", "answer": "", "citations": [], "conflicts": [], "unresolved_reason": "Not documented."}
    (tmp_path / "draft_answer").mkdir()
    (tmp_path / "draft_answer" / f"{digest[:24]}.json").write_text(json.dumps({
        "recorded_at": "2026-10-04T00:00:00+00:00", "request": request,
        "response": {"id": "msg_x", "model": "m", "stop_reason": "end_turn", "usage": {},
                     "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": json.dumps(output)}]},
    }))
    result = model.complete_json(purpose="draft_answer", prompt_name="p", system="sys", user="usr", schema=DRAFT_SCHEMA,
                                 output_model=DraftOutput, effort="medium", context={})
    assert result.ok and result.provenance == "replay" and result.data["unresolved_reason"] == "Not documented."
    assert result.response_id == "msg_x" and result.recorded_at.startswith("2026-10-04")


def test_recorded_refusal_and_invalid_output_are_errors(tmp_path):
    model = AnthropicJsonModel(mode="replay", replay_dir=tmp_path, model="m")
    for user, response, expected in [
        ("a", {"stop_reason": "refusal", "stop_details": {"category": "cyber"}, "content": []}, "refusal"),
        ("b", {"stop_reason": "end_turn", "content": [{"type": "text", "text": "{\"status\": \"maybe\"}"}]}, "invalid_output"),
        ("c", {"stop_reason": "max_tokens", "content": [{"type": "text", "text": "{"}]}, "truncated"),
    ]:
        request = build_request("m", "sys", user, DRAFT_SCHEMA, "medium")
        (tmp_path / "draft_answer").mkdir(exist_ok=True)
        (tmp_path / "draft_answer" / f"{request_hash(request)[:24]}.json").write_text(json.dumps({"request": request, "response": response}))
        result = model.complete_json(purpose="draft_answer", prompt_name="p", system="sys", user=user, schema=DRAFT_SCHEMA,
                                     output_model=DraftOutput, effort="medium", context={})
        assert not result.ok and result.error_type == expected


def test_langchain_sends_exactly_the_request_we_record():
    from qra.drafting import VERIFY_SCHEMA
    from qra.llm import langchain_call, payload_langchain_would_send
    for schema in (DRAFT_SCHEMA, VERIFY_SCHEMA):
        request = build_request("claude-opus-5-5", "system text {not a variable}", "<documents>x</documents>", schema, "medium")
        chat, inputs = langchain_call(request, api_key="test-key-not-used")
        assert payload_langchain_would_send(chat, inputs) == request


def test_langchain_response_is_read_like_an_api_response():
    from langchain_core.messages import AIMessage
    from qra.llm import ModelResult, response_dict
    output = {"status": "unresolved", "answer": "", "citations": [], "conflicts": [], "unresolved_reason": "Not documented."}
    meta = {"id": "msg_1", "model_name": "claude-opus-5-5", "stop_reason": "end_turn", "usage": {"input_tokens": 1}}
    for content in (json.dumps(output), [{"type": "thinking", "thinking": ""}, {"type": "text", "text": json.dumps(output)}]):
        response = response_dict(AIMessage(content=content, response_metadata=meta))
        result = AnthropicJsonModel._read_response(
            ModelResult(ok=False, provenance="live", purpose="draft_answer", prompt_name="p", model_requested="m"), response, DraftOutput)
        assert result.ok and result.data["unresolved_reason"] == "Not documented." and result.response_id == "msg_1"
