"""Standard-library compatibility entry point for the v1 core tests in tests/."""

import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[2] / "tests/test_goal_evaluation.py"
_spec = importlib.util.spec_from_file_location("goal_eval_core_tests", _path)
assert _spec and _spec.loader
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
GoalEvaluationTests = _module.GoalEvaluationTests
UpstreamCharacterizationTests = _module.UpstreamCharacterizationTests
