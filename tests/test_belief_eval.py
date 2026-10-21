"""Fork: the belief-ledger memory eval (scripts/belief_eval.py) through pytest.

Same seeded history and checks as the script, in a temp DATA_DIR. Fictional data only.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "belief_eval.py"
_SPEC = importlib.util.spec_from_file_location("belief_eval", _PATH)
belief_eval = importlib.util.module_from_spec(_SPEC)
sys.modules["belief_eval"] = belief_eval
_SPEC.loader.exec_module(belief_eval)


@pytest.fixture(scope="module")
def checks(tmp_path_factory):
    return belief_eval.run_eval(tmp_path_factory.mktemp("belief-eval"))


@pytest.mark.parametrize("case", list(belief_eval.CASES))
def test_case(checks, case):
    mine = [c for c in checks if c.case == case]
    assert mine, f"no checks for {case}"
    failed = [f"{c.step}: expected {c.expected!r}, got {c.actual!r}" for c in mine if not c.ok]
    assert not failed, "\n".join(failed)


def test_every_check_belongs_to_a_case(checks):
    assert {c.case for c in checks} == set(belief_eval.CASES)


def test_main_scores_keeps_the_dir_and_restores_settings(tmp_path, monkeypatch, capsys):
    from src import constants

    monkeypatch.setenv("BELIEF_STALE_DAYS", "5")  # the eval runs on the defaults
    data_dir_before = constants.DATA_DIR
    assert belief_eval.main(["--data-dir", str(tmp_path), "--keep"]) == 0
    out = capsys.readouterr().out
    n = len(belief_eval.CASES)
    assert f"cases: {n}/{n}" in out
    score = out.split("score ")[1].split()[0]
    passed, total = score.split("/")
    assert passed == total and int(total) > 50
    assert (tmp_path / "beliefs.db").exists() and (tmp_path / "memory.json").exists()
    assert "ledger kept:" in out
    assert constants.DATA_DIR == data_dir_before
    assert os.environ["BELIEF_STALE_DAYS"] == "5"
    # Seeding the same owner twice would not be the same history: refused.
    assert belief_eval.main(["--data-dir", str(tmp_path)]) == 2
    # Another owner in the same dir is a fresh history.
    assert belief_eval.main(["--data-dir", str(tmp_path), "--owner", "eval-2"]) == 0


def test_a_mismatch_exits_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(belief_eval, "CONFIRMED", belief_eval.CONFIRMED + ("Not a belief",))
    assert belief_eval.main(["--data-dir", str(tmp_path)]) == 1
    assert "FAIL memory: the expected confirmed set is in memory" in capsys.readouterr().out


def test_default_temp_dir_is_removed(monkeypatch, tmp_path, capsys):
    made = []
    real = belief_eval.tempfile.mkdtemp

    def mkdtemp(**kw):
        made.append(real(dir=tmp_path, **kw))
        return made[-1]

    monkeypatch.setattr(belief_eval.tempfile, "mkdtemp", mkdtemp)
    assert belief_eval.main([]) == 0
    assert made and not Path(made[0]).exists()
    assert "ledger kept:" not in capsys.readouterr().out


def test_chat_needs_data_dir_and_bakeoff():
    with pytest.raises(SystemExit):
        belief_eval.main(["--chat"])


def test_score_answer():
    link = belief_eval.module_link(9004)
    answer = (f"Priya Nair is allergic to peanuts ({link}). Her birthday is on 21 May "
              f"(source: {belief_eval.module_link(9015)}).")
    got = {label: (fact, cited) for label, fact, cited in belief_eval.score_answer(answer)}
    assert got == {"peanut allergy": (True, True), "birthday (corrected)": (True, True),
                   "design review": (False, False)}
