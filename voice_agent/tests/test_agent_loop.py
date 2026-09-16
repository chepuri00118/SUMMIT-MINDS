import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.brain.agent import MAX_TOOL_ROUNDS, CallAgent
from app.brain.backends import TextDelta, ToolCall


class ScriptedBrain:
    """Replays fixed responses so the loop can be tested without a model."""

    name = "scripted"

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    async def stream(self, system, turns, tools):
        self.calls += 1
        events = self.script[min(self.calls - 1, len(self.script) - 1)]
        for event in events:
            yield event


def drain(agent):
    async def run():
        return "".join([chunk async for chunk in agent.next_turn()])
    return asyncio.run(run())


def test_end_call_stops_the_loop_immediately():
    # Regression: should_hang_up was never set by anything, so the agent looped
    # back to the model after end_call instead of stopping.
    brain = ScriptedBrain([[
        TextDelta("Thanks, I'll let you go."),
        ToolCall("1", "end_call", {"outcome": "not_interested", "summary": "no"}),
    ]])
    agent = CallAgent({"first_name": "Mike"}, tool_handler=lambda n, p: "ok", brain=brain)
    text = drain(agent)
    assert "let you go" in text
    assert agent.should_hang_up
    assert brain.calls == 1


def test_transfer_also_ends_the_loop():
    brain = ScriptedBrain([[ToolCall("1", "transfer_to_human", {"reason": "asked"})]])
    agent = CallAgent({}, tool_handler=lambda n, p: "ok", brain=brain)
    drain(agent)
    assert agent.should_hang_up
    assert brain.calls == 1


def test_a_model_looping_on_tools_cannot_hold_the_line_open():
    # A small local model that keeps calling a non-terminal tool would
    # otherwise loop forever, which on a real call is dead air and a bill.
    forever = [[ToolCall("1", "log_discovery", {"field": "other", "value": "x"})]]
    brain = ScriptedBrain(forever)
    agent = CallAgent({}, tool_handler=lambda n, p: "ok", brain=brain)
    drain(agent)
    assert brain.calls == MAX_TOOL_ROUNDS


def test_a_plain_answer_makes_exactly_one_model_call():
    brain = ScriptedBrain([[TextDelta("Hi Mike, quick question.")]])
    agent = CallAgent({}, tool_handler=lambda n, p: "ok", brain=brain)
    assert drain(agent) == "Hi Mike, quick question."
    assert brain.calls == 1


def test_a_failing_tool_does_not_kill_the_call():
    def explode(name, payload):
        raise RuntimeError("crm is down")
    brain = ScriptedBrain([
        [ToolCall("1", "log_discovery", {"field": "other", "value": "x"})],
        [TextDelta("Anyway - where were we?")],
    ])
    agent = CallAgent({}, tool_handler=explode, brain=brain)
    assert "where were we" in drain(agent)
    assert any(t.role == "tool" and "crm is down" in t.tool_result for t in agent.turns)


def test_barge_in_abandons_the_turn():
    class Interrupting:
        name = "x"
        async def stream(self, system, turns, tools):
            yield TextDelta("We do structural detailing for ")
            agent.cancel_current_turn()
            yield TextDelta("SHOULD NOT BE SPOKEN")
    agent = CallAgent({}, tool_handler=lambda n, p: "ok", brain=Interrupting())
    assert "SHOULD NOT" not in drain(agent)
