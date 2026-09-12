"""Placeholder decision logic - owned by Deem & Mariam.

This is a stand-in ONLY so the pipeline runs end to end this sprint. Replace
`evaluate()` with the real placeholder rule logic (Week 2), then the real
two-layer detection (Sprint 3). Keep the input/output contract below stable
so backend/main.py and database.py don't need to change when this file does.
"""

from dataclasses import asdict, dataclass


@dataclass
class AIDecision:
    status: str            # "normal" | "warning" | "critical"
    alert: str | None
    action: str | None     # None | "safe_mode" | "inspect" | "lock" | "unlock" | "reset"
    score: float            # 0.0-1.0 suspicion score, per component

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate(component_type: str, data: dict) -> AIDecision:
    """Stub only: always reports normal, no action.

    Deem/Mariam: replace the body with real per-component-type rules.
    component_type is one of: dht22, pir, servo_lock, fan_led, push_button.
    data is that component's own fields, e.g. for dht22:
    {"temperature": 24.5, "humidity": 48.0}.
    """
    return AIDecision(status="normal", alert=None, action=None, score=0.0)
