"""Pluggable conversation backends.

The agent's brain is either a hosted model (Anthropic) or a model running on
your own machine (Ollama). Both speak the same small interface below, so
nothing downstream - the call session, the tools, the CRM - knows or cares
which one is answering.

The transcript is kept in a neutral shape and converted per backend, because
the two wire formats disagree about almost everything.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol

import httpx

log = logging.getLogger(__name__)


# -- neutral transcript ---------------------------------------------------
@dataclass
class Turn:
    """One entry in the conversation, independent of any provider's schema."""

    role: str                                   # user | assistant | tool
    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_call_id: str = ""
    tool_name: str = ""
    tool_result: str = ""


# -- streamed events ------------------------------------------------------
@dataclass
class TextDelta:
    text: str


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


class Brain(Protocol):
    """What the call session needs from a model, and nothing more."""

    name: str

    def stream(
        self,
        system: str,
        turns: list[Turn],
        tools: list[dict[str, Any]],
    ) -> AsyncIterator[TextDelta | ToolCall]:
        ...


# -- Anthropic ------------------------------------------------------------
class AnthropicBrain:
    """Hosted Claude. Best quality; needs an API key and a network round trip."""

    name = "anthropic"

    def __init__(self, api_key: str, model: str) -> None:
        from anthropic import AsyncAnthropic

        self.model = model
        self.client = AsyncAnthropic(api_key=api_key)

    @staticmethod
    def _to_messages(turns: list[Turn]) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for turn in turns:
            if turn.role == "user":
                messages.append({"role": "user", "content": turn.text})
            elif turn.role == "assistant":
                blocks: list[dict[str, Any]] = []
                if turn.text:
                    blocks.append({"type": "text", "text": turn.text})
                for call in turn.tool_calls:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": call["id"],
                            "name": call["name"],
                            "input": call["input"],
                        }
                    )
                messages.append({"role": "assistant", "content": blocks})
            elif turn.role == "tool":
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": turn.tool_call_id,
                                "content": turn.tool_result,
                            }
                        ],
                    }
                )
        return messages

    async def stream(self, system, turns, tools):
        async with self.client.messages.stream(
            model=self.model,
            max_tokens=300,
            # Thinking stays on at the lowest effort. Disabling it is the
            # obvious latency move and a trap here: the model then sometimes
            # writes a tool call into its visible text, which on this app means
            # saying "log_discovery" out loud to a prospect.
            thinking={"type": "adaptive"},
            output_config={"effort": "low"},
            system=system,
            tools=tools,
            messages=self._to_messages(turns),
        ) as stream:
            async for event in stream:
                if event.type == "text":
                    yield TextDelta(event.text)
            final = await stream.get_final_message()

        for block in final.content:
            if block.type == "tool_use":
                yield ToolCall(block.id, block.name, block.input)


# -- Ollama ---------------------------------------------------------------
class OllamaBrain:
    """A model running on your own machine. No account, no key, no network.

    Quality is below hosted Claude and the phrasing is stiffer, but it is free
    and private, and it is enough to tune the call script against.
    """

    name = "ollama"

    def __init__(self, model: str, host: str = "http://localhost:11434") -> None:
        self.model = model
        self.host = host.rstrip("/")

    @staticmethod
    def _to_messages(turns: list[Turn]) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for turn in turns:
            if turn.role == "user":
                messages.append({"role": "user", "content": turn.text})
            elif turn.role == "assistant":
                message: dict[str, Any] = {"role": "assistant", "content": turn.text}
                if turn.tool_calls:
                    message["tool_calls"] = [
                        {"function": {"name": c["name"], "arguments": c["input"]}}
                        for c in turn.tool_calls
                    ]
                messages.append(message)
            elif turn.role == "tool":
                # Ollama has a dedicated tool role and matches on name, not id.
                messages.append(
                    {"role": "tool", "content": turn.tool_result, "name": turn.tool_name}
                )
        return messages

    @staticmethod
    def _to_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Anthropic tool schemas into OpenAI-style function schemas."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["input_schema"],
                },
            }
            for tool in tools
        ]

    async def stream(self, system, turns, tools):
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *self._to_messages(turns)],
            "tools": self._to_tools(tools),
            "stream": True,
            "options": {
                # Local models drift long and listy without a hard ceiling, and
                # a long turn on a phone call is a hang-up.
                "num_predict": 220,
                "temperature": 0.8,
            },
        }
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream("POST", f"{self.host}/api/chat", json=payload) as response:
                response.raise_for_status()
                counter = 0
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        log.warning("ollama sent a non-JSON line, skipping")
                        continue

                    message = chunk.get("message") or {}
                    if message.get("content"):
                        yield TextDelta(message["content"])

                    for call in message.get("tool_calls") or []:
                        function = call.get("function") or {}
                        arguments = function.get("arguments") or {}
                        if isinstance(arguments, str):
                            # Some builds hand back a JSON string here.
                            try:
                                arguments = json.loads(arguments)
                            except json.JSONDecodeError:
                                log.warning("unparseable tool arguments, skipping call")
                                continue
                        counter += 1
                        yield ToolCall(f"call_{counter}", function.get("name", ""), arguments)

                    if chunk.get("done"):
                        return


def build_brain(settings) -> Brain:
    """Pick a backend from configuration, with a readable failure if it can't."""
    provider = (settings.brain_provider or "").lower()

    if provider == "ollama":
        return OllamaBrain(settings.ollama_model, settings.ollama_host)

    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise RuntimeError(
                "BRAIN_PROVIDER is anthropic but ANTHROPIC_API_KEY is not set. "
                "Either set the key, or set BRAIN_PROVIDER=ollama to run a model "
                "on this machine with no account at all."
            )
        return AnthropicBrain(settings.anthropic_api_key, settings.model)

    raise RuntimeError(f"Unknown BRAIN_PROVIDER {provider!r}. Use 'anthropic' or 'ollama'.")
