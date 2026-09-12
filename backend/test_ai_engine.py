"""Tests for the placeholder AI contract.

These only check the STUB's shape and behavior: any component type in,
"normal, no action" out. Once Deem/Mariam replace evaluate()'s body with
real per-component-type rules, replace these with tests for that real
logic, keep test_decision_to_dict and the "all five types accepted"
coverage, since those protect the contract main.py and database.py rely on.
"""

from ai_engine import evaluate


def test_stub_returns_normal_for_any_reading():
    decision = evaluate("dht22", {"temperature": 24.0, "humidity": 45.0})
    assert decision.status == "normal"
    assert decision.action is None
    assert decision.alert is None
    assert decision.score == 0.0


def test_stub_accepts_all_five_component_types():
    samples = [
        ("dht22", {"temperature": 24.0, "humidity": 45.0}),
        ("pir", {"motion": True}),
        ("servo_lock", {"state": "locked"}),
        ("fan_led", {"state": "on"}),
        ("push_button", {"state": "pressed"}),
    ]
    for component_type, data in samples:
        decision = evaluate(component_type, data)
        assert decision.status == "normal"


def test_decision_to_dict_matches_database_insert_reading_contract():
    decision = evaluate("dht22", {"temperature": 24.0, "humidity": 45.0})
    as_dict = decision.to_dict()
    assert as_dict == {"status": "normal", "alert": None, "action": None, "score": 0.0}
