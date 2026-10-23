"""Thinking defaults to on in Agent mode and off in Chat mode (src/thinking_defaults.py)."""
import pytest

from src import constants
from src import thinking_defaults as td


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "DATA_DIR", str(tmp_path))
    monkeypatch.delenv(td.ENV, raising=False)
    return tmp_path


def test_untouched_chat_thinks_in_agent_mode_only():
    assert td.turn_default("s1", "agent", "off") == "on"
    assert td.turn_default("s1", "agent", "") == "on"
    assert td.turn_default("s1", "chat", "off") is None
    assert td.turn_default("s1", "", "off") is None


def test_stored_on_or_effort_is_left_to_upstream():
    assert td.turn_default("s1", "agent", "on") is None
    assert td.turn_default("s1", "agent", "effort:high") is None


def test_a_toggle_choice_holds_in_both_modes(data_dir):
    td.record_choice("s1")
    td.record_choice("s1")
    assert td.has_choice("s1") and not td.has_choice("s2")
    assert td.turn_default("s1", "agent", "off") is None
    assert td.turn_default("s2", "agent", "off") == "on"
    assert (data_dir / "fork" / "thinking_choices.json").exists()


def test_env_off_restores_upstream(monkeypatch):
    monkeypatch.setenv(td.ENV, "off")
    assert td.turn_default("s1", "agent", "off") is None
    assert td.context_fields("s1") == {"thinking_explicit": False, "thinking_agent_default": False}


def test_context_fields_and_unreadable_store(data_dir):
    assert td.context_fields("s1") == {"thinking_explicit": False, "thinking_agent_default": True}
    td.record_choice("s1")
    assert td.context_fields("s1")["thinking_explicit"] is True
    (data_dir / "fork" / "thinking_choices.json").write_text("not json")
    assert td.has_choice("s1") is False
