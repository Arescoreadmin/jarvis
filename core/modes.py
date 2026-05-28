from enum import Enum
from dataclasses import dataclass


class Mode(str, Enum):
    EXECUTIVE = "executive"
    DEEP_WORK = "deep_work"
    CREATIVE = "creative"
    CRISIS = "crisis"
    SOCIAL = "social"


@dataclass
class ModeProfile:
    name: Mode
    interruption_threshold: str  # urgent | high | medium | low
    max_response_length: str     # very_short | short | medium | long
    humor_enabled: bool
    proactive_surfaces: bool
    system_addendum: str


PROFILES: dict[Mode, ModeProfile] = {
    Mode.EXECUTIVE: ModeProfile(
        name=Mode.EXECUTIVE,
        interruption_threshold="high",
        max_response_length="short",
        humor_enabled=True,
        proactive_surfaces=True,
        system_addendum=(
            "MODE: EXECUTIVE. Be maximally brief. Act and report. "
            "Surface only what requires a decision or action."
        ),
    ),
    Mode.DEEP_WORK: ModeProfile(
        name=Mode.DEEP_WORK,
        interruption_threshold="urgent",
        max_response_length="very_short",
        humor_enabled=False,
        proactive_surfaces=False,
        system_addendum=(
            "MODE: DEEP WORK. Minimal interruptions. Hold all non-urgent items. "
            "Confirmations are single words. The user is focused — protect that."
        ),
    ),
    Mode.CREATIVE: ModeProfile(
        name=Mode.CREATIVE,
        interruption_threshold="high",
        max_response_length="medium",
        humor_enabled=True,
        proactive_surfaces=True,
        system_addendum=(
            "MODE: CREATIVE. Expand ideas, don't constrain them. Ask generative questions. "
            "Make unexpected connections. Be a thinking partner."
        ),
    ),
    Mode.CRISIS: ModeProfile(
        name=Mode.CRISIS,
        interruption_threshold="low",
        max_response_length="short",
        humor_enabled=False,
        proactive_surfaces=True,
        system_addendum=(
            "MODE: CRISIS. Precision instrument. No humor. No softening. "
            "Every word must carry weight. What is the threat, what are the options, what is the call."
        ),
    ),
    Mode.SOCIAL: ModeProfile(
        name=Mode.SOCIAL,
        interruption_threshold="high",
        max_response_length="medium",
        humor_enabled=True,
        proactive_surfaces=True,
        system_addendum=(
            "MODE: SOCIAL. The user may be with others. Relationship-aware. "
            "Warmer and more context-provided. Help them look sharp and sound prepared."
        ),
    ),
}


class ModeManager:
    def __init__(self, memory):
        self._memory = memory
        self._current: Mode = Mode.EXECUTIVE

    @property
    def current(self) -> Mode:
        return self._current

    @property
    def profile(self) -> ModeProfile:
        return PROFILES[self._current]

    def set(self, mode: Mode) -> str:
        prev = self._current
        self._current = mode
        self._memory.set_semantic("active_mode", mode.value)
        return f"Mode: {prev.value} → {mode.value}"

    def from_string(self, s: str) -> Mode:
        mapping = {
            "executive": Mode.EXECUTIVE,
            "exec": Mode.EXECUTIVE,
            "deep": Mode.DEEP_WORK,
            "deep work": Mode.DEEP_WORK,
            "deepwork": Mode.DEEP_WORK,
            "creative": Mode.CREATIVE,
            "create": Mode.CREATIVE,
            "crisis": Mode.CRISIS,
            "emergency": Mode.CRISIS,
            "social": Mode.SOCIAL,
        }
        return mapping.get(s.lower().strip(), Mode.EXECUTIVE)

    def should_interrupt(self, priority: str) -> bool:
        order = ["low", "medium", "high", "urgent"]
        threshold = self.profile.interruption_threshold
        try:
            return order.index(priority) >= order.index(threshold)
        except ValueError:
            return False
