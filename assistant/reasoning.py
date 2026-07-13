"""Reasoning chain tracking for AI Assistant responses."""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ReasoningStep:
    """A single step in the reasoning chain."""
    step_id: int
    type: str  # "tool_call" | "llm_inference" | "knowledge_lookup"
    description: str  # human-readable description
    input: dict = field(default_factory=dict)
    output: dict = field(default_factory=dict)
    confidence: float = 1.0  # 0.0-1.0
    sources: list[str] = field(default_factory=list)  # reference URLs


class ReasoningChain:
    """Tracks the reasoning steps behind an assistant's response."""

    def __init__(self, max_steps: int = 20):
        self.steps: list[ReasoningStep] = []
        self.max_steps = max_steps
        self._counter = 0

    def add_step(
        self,
        step_type: str,
        description: str,
        input_data: Optional[dict] = None,
        output_data: Optional[dict] = None,
        confidence: float = 1.0,
        sources: Optional[list[str]] = None,
    ) -> ReasoningStep:
        """Add a reasoning step and return it."""
        if len(self.steps) >= self.max_steps:
            # Drop oldest non-essential step
            popped = False
            for i, s in enumerate(self.steps):
                if s.type != "tool_call":
                    self.steps.pop(i)
                    popped = True
                    break
            # Fallback: if all steps are tool_calls, pop the oldest unconditionally
            if not popped:
                self.steps.pop(0)

        self._counter += 1
        step = ReasoningStep(
            step_id=self._counter,
            type=step_type,
            description=description,
            input=input_data or {},
            output=output_data or {},
            confidence=confidence,
            sources=sources or [],
        )
        self.steps.append(step)
        return step

    def to_dict(self) -> list[dict]:
        """Serialize all steps to dicts."""
        return [
            {
                "step_id": s.step_id,
                "type": s.type,
                "description": s.description,
                "input": s.input,
                "output": s.output,
                "confidence": s.confidence,
                "sources": s.sources,
            }
            for s in self.steps
        ]

    def to_markdown(self) -> str:
        """Render reasoning chain as markdown."""
        if not self.steps:
            return ""
        lines = ["### 🔍 推理过程"]
        for s in self.steps:
            icon = {"tool_call": "🔧", "llm_inference": "🧠", "knowledge_lookup": "📚"}.get(s.type, "→")
            lines.append(f"{icon} **Step {s.step_id}**: {s.description}")
            if s.sources:
                for src in s.sources:
                    lines.append(f"    📎 {src}")
            if s.confidence < 1.0:
                lines.append(f"    🎯 置信度: {s.confidence:.0%}")
        return "\n".join(lines)

    def __len__(self):
        return len(self.steps)

    def __bool__(self):
        return len(self.steps) > 0
