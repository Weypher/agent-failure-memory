"""Fixed prompt and action interface, with no persistent episode state."""
from dataclasses import dataclass
from typing import Protocol

from .artifacts import digest

SYSTEM_PROMPT = """Complete the household task in this text environment. Return exactly one
action from the current admissible commands, with no explanation or formatting.
Use observations and your action history to track progress. Memories are fallible
advice from earlier tasks; apply them only when relevant. Never assume an object's
location without observing it. An unsuccessful action is feedback, not success."""
REFLECTION_PROMPT = """Read this unsuccessful household-task trajectory. Extract one concise,
transferable lesson supported by specific recorded steps. Do not invent a success
or use hidden task solutions. Return JSON only with keys trigger (<=300 characters),
lesson (<=1200 characters), evidence_steps (nonempty array of 1-based step numbers)."""
PROMPT_ID = digest({"agent": SYSTEM_PROMPT, "reflection": REFLECTION_PROMPT})


@dataclass(frozen=True)
class Reply:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_nusd: int = 0
    request_id: str | None = None
    model: str = "debug-script"


class ModelError(RuntimeError):
    """Infrastructure errors are excluded from task success denominators."""


class ModelOutputIncomplete(ModelError):
    """Provider returned a settled, retained response that is not a complete answer
    (finish_reason other than 'stop', or a refusal). Model behaviour, not transport failure."""


class BudgetStop(ModelError):
    pass


class Model(Protocol):
    kind: str
    model_id: str

    def complete(self, messages: list[dict], call_id: str) -> Reply: ...


class ScriptedModel:
    """Explicitly debug-only actions; never a natural failure or study result."""
    kind = "debug_scripted"
    model_id = "debug-script"

    def __init__(self, actions):
        self.actions = iter(actions)

    def complete(self, messages, call_id):
        return Reply(next(self.actions, "look"))


def messages_for(description, memories, history, state):
    memory_text = "\n".join(f"[{m.id}] When: {m.trigger}\nLesson: {m.lesson}" for m in memories)
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Task: {description}\nMemories:\n{memory_text or '(none)'}"}]
    for observation, commands, action in history:
        messages.extend([{"role": "user", "content": observation + "\nAdmissible commands:\n" + "\n".join(commands)},
                         {"role": "assistant", "content": action}])
    messages.append({"role": "user", "content": state.observation + "\nAdmissible commands:\n" +
                     "\n".join(state.admissible_commands)})
    return messages
