"""Tests for autonomy mode functionality."""

import pytest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from orchestrator.lib.config import AUTONOMY_MODES, EscalationConfig
from orchestrator.runner.impl.stages import get_effective_autonomy


@dataclass
class MockRunContext:
    """Minimal mock of RunContext for testing."""
    autonomy_override: str | None = None
    project_dir: Path = Path("/fake/project")


class TestGetEffectiveAutonomy:
    """Test get_effective_autonomy function."""

    def test_returns_override_when_set_to_supervised(self):
        ctx = MockRunContext(autonomy_override="supervised")
        assert get_effective_autonomy(ctx) == "supervised"

    def test_returns_override_when_set_to_gatekeeper(self):
        ctx = MockRunContext(autonomy_override="gatekeeper")
        assert get_effective_autonomy(ctx) == "gatekeeper"

    def test_returns_override_when_set_to_autonomous(self):
        ctx = MockRunContext(autonomy_override="autonomous")
        assert get_effective_autonomy(ctx) == "autonomous"

    def test_raises_on_invalid_override(self):
        ctx = MockRunContext(autonomy_override="yolo")
        with pytest.raises(ValueError) as exc_info:
            get_effective_autonomy(ctx)
        assert "Invalid autonomy mode 'yolo'" in str(exc_info.value)
        assert "Valid modes:" in str(exc_info.value)

    @patch("orchestrator.runner.impl.stages.load_escalation_config")
    def test_loads_from_escalation_config_when_no_override(self, mock_load):
        mock_load.return_value = EscalationConfig(
            autonomy="gatekeeper",
            commit_confidence_threshold=0.7,
            merge_confidence_threshold=0.8,
            sensitive_paths=None,
        )
        ctx = MockRunContext(autonomy_override=None)
        assert get_effective_autonomy(ctx) == "gatekeeper"
        mock_load.assert_called_once_with(ctx.project_dir)

    def test_uses_passed_escalation_config_instead_of_loading(self):
        ctx = MockRunContext(autonomy_override=None)
        escalation = EscalationConfig(
            autonomy="autonomous",
            commit_confidence_threshold=0.7,
            merge_confidence_threshold=0.8,
            sensitive_paths=None,
        )
        # Should use passed config, not load from disk
        assert get_effective_autonomy(ctx, escalation) == "autonomous"

    def test_override_takes_precedence_over_passed_config(self):
        ctx = MockRunContext(autonomy_override="supervised")
        escalation = EscalationConfig(
            autonomy="autonomous",
            commit_confidence_threshold=0.7,
            merge_confidence_threshold=0.8,
            sensitive_paths=None,
        )
        # Override should win
        assert get_effective_autonomy(ctx, escalation) == "supervised"


class TestAutonomyModes:
    """Test AUTONOMY_MODES constant."""

    def test_contains_all_expected_modes(self):
        assert "supervised" in AUTONOMY_MODES
        assert "gatekeeper" in AUTONOMY_MODES
        assert "autonomous" in AUTONOMY_MODES
        assert len(AUTONOMY_MODES) == 3
