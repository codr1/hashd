"""Tests for agents_config module."""

import json
import pytest

from orchestrator.lib.agents_config import (
    AgentsConfig,
    load_agents_config,
    get_stage_command,
    get_stage_binary,
    DEFAULT_STAGE_COMMANDS,
)


class TestLoadAgentsConfig:
    """Tests for load_agents_config()."""

    def test_returns_defaults_when_no_project_dir(self):
        config = load_agents_config(None)
        assert config.stages == DEFAULT_STAGE_COMMANDS

    def test_returns_defaults_when_file_missing(self, tmp_path):
        config = load_agents_config(tmp_path)
        assert config.stages == DEFAULT_STAGE_COMMANDS

    def test_loads_custom_config(self, tmp_path):
        config_data = {
            "stages": {
                "review": "custom-claude --fast -p {prompt}"
            }
        }
        (tmp_path / "agents.json").write_text(json.dumps(config_data))

        config = load_agents_config(tmp_path)
        assert config.stages["review"] == "custom-claude --fast -p {prompt}"
        # Other stages should still have defaults
        assert config.stages["implement"] == DEFAULT_STAGE_COMMANDS["implement"]

    def test_handles_invalid_json(self, tmp_path):
        (tmp_path / "agents.json").write_text("not valid json {{{")
        config = load_agents_config(tmp_path)
        assert config.stages == DEFAULT_STAGE_COMMANDS


class TestGetStageCommand:
    """Tests for get_stage_command()."""

    def test_basic_substitution(self):
        config = AgentsConfig()
        result = get_stage_command(
            config, "implement",
            {"worktree": "/tmp/ws", "prompt": "do stuff"}
        )
        assert result.cmd[0] == "codex"
        assert "-C" in result.cmd
        assert "/tmp/ws" in result.cmd
        assert "do stuff" in result.cmd
        assert result.prompt_via_stdin is False

    def test_prompt_via_stdin_when_not_in_template(self):
        config = AgentsConfig()
        result = get_stage_command(config, "breakdown", {"prompt": "test"})
        assert result.prompt_via_stdin is True
        assert "test" not in result.cmd

    def test_prompt_via_arg_when_in_template(self):
        config = AgentsConfig()
        result = get_stage_command(
            config, "review",
            {"prompt": "review this code"}
        )
        assert result.prompt_via_stdin is False
        assert "review this code" in result.cmd

    def test_raises_on_unknown_stage(self):
        config = AgentsConfig()
        with pytest.raises(ValueError, match="Unknown stage"):
            get_stage_command(config, "nonexistent", {})

    def test_raises_on_missing_required_variable(self):
        config = AgentsConfig()
        with pytest.raises(ValueError, match="missing"):
            get_stage_command(config, "implement", {"prompt": "test"})

    def test_implement_resume_requires_session_id(self):
        config = AgentsConfig()
        with pytest.raises(ValueError, match="session_id"):
            get_stage_command(
                config, "implement_resume",
                {"worktree": "/tmp/ws", "prompt": "test"}
            )

    def test_implement_resume_with_session_id(self):
        config = AgentsConfig()
        result = get_stage_command(
            config, "implement_resume",
            {"worktree": "/tmp/ws", "session_id": "abc-123", "prompt": "retry"}
        )
        assert "abc-123" in result.cmd
        assert "resume" in result.cmd

    def test_detects_json_output_format(self):
        config = AgentsConfig()
        result = get_stage_command(config, "review", {"prompt": "test"})
        assert result.output_format == "json"

    def test_no_output_format_when_absent(self):
        config = AgentsConfig()
        result = get_stage_command(config, "pm_discovery", {"prompt": "test"})
        assert result.output_format is None

    def test_prompt_with_special_characters(self):
        config = AgentsConfig()
        prompt = 'fix the "quoted" string and \'this\' too'
        result = get_stage_command(
            config, "implement",
            {"worktree": "/tmp/ws", "prompt": prompt}
        )
        # Prompt should appear intact in cmd, not mangled by shlex
        assert prompt in result.cmd

    def test_prompt_with_newlines(self):
        config = AgentsConfig()
        prompt = "line one\nline two\nline three"
        result = get_stage_command(
            config, "review",
            {"prompt": prompt}
        )
        assert prompt in result.cmd


class TestGetStageBinary:
    """Tests for get_stage_binary()."""

    def test_returns_codex_for_implement(self):
        config = AgentsConfig()
        assert get_stage_binary(config, "implement") == "codex"

    def test_returns_claude_for_review(self):
        config = AgentsConfig()
        assert get_stage_binary(config, "review") == "claude"

    def test_raises_on_unknown_stage(self):
        config = AgentsConfig()
        with pytest.raises(ValueError, match="Unknown stage"):
            get_stage_binary(config, "nonexistent")

    def test_custom_binary(self, tmp_path):
        config_data = {
            "stages": {
                "review": "my-custom-agent --flag"
            }
        }
        (tmp_path / "agents.json").write_text(json.dumps(config_data))
        config = load_agents_config(tmp_path)
        assert get_stage_binary(config, "review") == "my-custom-agent"
