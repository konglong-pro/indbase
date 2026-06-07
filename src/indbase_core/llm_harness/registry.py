"""Prompt registry for harness calls (v0.3.1 fake provider only)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PromptSpec:
    name: str
    version: str
    purpose: str


PROMPTS: dict[str, PromptSpec] = {
    "taxonomy.tag_arbitration": PromptSpec(
        name="taxonomy.tag_arbitration",
        version="v0.3.1-fake",
        purpose="Map a feature atom to tag assignment, candidate, or local keyword.",
    ),
    "taxonomy.category_arbitration": PromptSpec(
        name="taxonomy.category_arbitration",
        version="v0.3.1-fake",
        purpose="Suggest an existing category for a document profile.",
    ),
}


def get_prompt(name: str) -> PromptSpec:
    spec = PROMPTS.get(name)
    if spec is None:
        raise ValueError(f"Unknown harness prompt: {name}")
    return spec
