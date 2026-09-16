"""Backend tests.

The Ollama tests run against a stub HTTP server speaking Ollama's real
NDJSON streaming protocol, so the parser is exercised for real without
needing a model on disk.
"""
import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.brain.backends import OllamaBrain, TextDelta, ToolCall, Turn, build_brain

CHUNKS = [
    {"message": {"role": "assistant", "content": "Hi Mike, "}, "done": False},
    {"message": {"role": "assistant", "content": "quick question."}, "done": False},
    {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "log_discovery",
                              "arguments": {"field": "software_used", "value": "Tekla"}}}
            ],
        },
        "done": False,
    },
    {"message": {"role": "assistant", "content": ""}, "done": True},
]


class _Handler(BaseHTTPRequestHandler):
    body_chunks = CHUNKS

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.received = json.loads(self.rfile.read(length))
        _Handler.last_request = self.received
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        for chunk in self.body_chunks:
            self.wfile.write((json.dumps(chunk) + "\n").encode())
            self.wfile.flush()

    def log_message(self, *args):
        pass


@pytest.fixture
def stub_ollama():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def collect(brain, turns, tools):
    async def run():
        return [event async for event in brain.stream("system prompt", turns, tools)]
    return asyncio.run(run())


TOOLS = [{
    "name": "log_discovery",
    "description": "Record a fact.",
    "input_schema": {"type": "object", "properties": {"field": {"type": "string"}}},
}]


def test_streams_text_deltas_in_order(stub_ollama):
    events = collect(OllamaBrain("m", stub_ollama), [Turn("user", "hello")], TOOLS)
    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert text == "Hi Mike, quick question."


def test_parses_tool_calls_out_of_the_stream(stub_ollama):
    events = collect(OllamaBrain("m", stub_ollama), [Turn("user", "hello")], TOOLS)
    calls = [e for e in events if isinstance(e, ToolCall)]
    assert len(calls) == 1
    assert calls[0].name == "log_discovery"
    assert calls[0].input == {"field": "software_used", "value": "Tekla"}


def test_tool_arguments_arriving_as_a_json_string_are_parsed(stub_ollama, monkeypatch):
    # Some Ollama builds serialise arguments as a string rather than an object.
    monkeypatch.setattr(_Handler, "body_chunks", [
        {"message": {"tool_calls": [
            {"function": {"name": "end_call",
                          "arguments": '{"outcome": "not_interested", "summary": "no"}'}}
        ]}, "done": True},
    ])
    events = collect(OllamaBrain("m", stub_ollama), [Turn("user", "x")], TOOLS)
    calls = [e for e in events if isinstance(e, ToolCall)]
    assert calls[0].input["outcome"] == "not_interested"


def test_unparseable_tool_arguments_do_not_kill_the_call(stub_ollama, monkeypatch):
    monkeypatch.setattr(_Handler, "body_chunks", [
        {"message": {"content": "still talking",
                     "tool_calls": [{"function": {"name": "end_call", "arguments": "{oops"}}]},
         "done": True},
    ])
    events = collect(OllamaBrain("m", stub_ollama), [Turn("user", "x")], TOOLS)
    assert [e for e in events if isinstance(e, ToolCall)] == []
    assert any(isinstance(e, TextDelta) for e in events)


def test_stops_at_the_done_flag(stub_ollama, monkeypatch):
    monkeypatch.setattr(_Handler, "body_chunks", [
        {"message": {"content": "one"}, "done": True},
        {"message": {"content": "NEVER"}, "done": False},
    ])
    events = collect(OllamaBrain("m", stub_ollama), [Turn("user", "x")], TOOLS)
    assert "NEVER" not in "".join(e.text for e in events if isinstance(e, TextDelta))


def test_system_prompt_and_tools_are_sent_in_ollama_shape(stub_ollama):
    collect(OllamaBrain("m", stub_ollama), [Turn("user", "hello")], TOOLS)
    sent = _Handler.last_request
    assert sent["messages"][0] == {"role": "system", "content": "system prompt"}
    assert sent["tools"][0]["type"] == "function"
    assert sent["tools"][0]["function"]["name"] == "log_discovery"
    # A long turn on a phone call is a hang-up; the ceiling must be sent.
    assert sent["options"]["num_predict"] <= 300


def test_tool_results_use_ollamas_tool_role():
    messages = OllamaBrain._to_messages([
        Turn("user", "hi"),
        Turn("assistant", "hello", tool_calls=[{"id": "1", "name": "log_discovery", "input": {}}]),
        Turn("tool", tool_call_id="1", tool_name="log_discovery", tool_result="logged"),
    ])
    assert messages[-1] == {"role": "tool", "content": "logged", "name": "log_discovery"}
    assert messages[1]["tool_calls"][0]["function"]["name"] == "log_discovery"


def test_anthropic_transcript_uses_tool_result_blocks():
    from app.brain.backends import AnthropicBrain
    messages = AnthropicBrain._to_messages([
        Turn("user", "hi"),
        Turn("assistant", "hello", tool_calls=[{"id": "tu_1", "name": "log_discovery", "input": {}}]),
        Turn("tool", tool_call_id="tu_1", tool_name="log_discovery", tool_result="logged"),
    ])
    assert messages[-1]["content"][0]["type"] == "tool_result"
    assert messages[-1]["content"][0]["tool_use_id"] == "tu_1"


def test_missing_anthropic_key_names_the_local_alternative():
    class S:
        brain_provider = "anthropic"
        anthropic_api_key = ""
        model = "claude-opus-5"
    with pytest.raises(RuntimeError, match="BRAIN_PROVIDER=ollama"):
        build_brain(S())


def test_unknown_provider_is_rejected():
    class S:
        brain_provider = "gpt4all"
    with pytest.raises(RuntimeError, match="Unknown BRAIN_PROVIDER"):
        build_brain(S())
