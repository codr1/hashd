"""Tests for the prompts module."""

import pytest
from pathlib import Path

from orchestrator.lib.prompts import (
    load_prompt,
    render_prompt,
    build_section,
    clear_cache,
    PromptError,
    PROMPTS_DIR,
)


class TestLoadPrompt:
    """Tests for load_prompt function."""

    def test_load_existing_prompt(self):
        """Should load an existing prompt template."""
        clear_cache()
        content = load_prompt("review")
        assert "CRITICAL:" in content
        assert "{commit_title}" in content

    def test_html_comments_stripped(self):
        """Should strip HTML comments from loaded prompts."""
        clear_cache()
        content = load_prompt("review")
        assert "<!--" not in content
        assert "-->" not in content
        # But the actual prompt content should be there
        assert "CRITICAL:" in content

    def test_load_nonexistent_prompt_raises(self):
        """Should raise PromptError for missing template."""
        clear_cache()
        with pytest.raises(PromptError) as exc_info:
            load_prompt("nonexistent_prompt_xyz")
        assert "not found" in str(exc_info.value)
        assert "nonexistent_prompt_xyz" in str(exc_info.value)

    def test_caching_works(self):
        """Should cache loaded prompts."""
        clear_cache()
        content1 = load_prompt("review")
        content2 = load_prompt("review")
        # Same object from cache
        assert content1 is content2

    def test_clear_cache(self):
        """Should clear the cache."""
        clear_cache()
        load_prompt("review")
        # Check cache has entry
        assert load_prompt.cache_info().hits >= 0
        clear_cache()
        assert load_prompt.cache_info().hits == 0


class TestRenderPrompt:
    """Tests for render_prompt function."""

    def test_render_with_variables(self):
        """Should interpolate variables into template."""
        clear_cache()
        result = render_prompt(
            "review",
            commit_title="Add auth",
            commit_description="Implement OAuth",
            diff="+ new code",
            review_history_section=""
        )
        assert "Add auth" in result
        assert "Implement OAuth" in result
        assert "+ new code" in result

    def test_render_missing_variable_raises(self):
        """Should raise PromptError with helpful message for missing variable."""
        clear_cache()
        with pytest.raises(PromptError) as exc_info:
            render_prompt("review", commit_title="Test")
        error_msg = str(exc_info.value)
        assert "Missing required variable" in error_msg
        assert "commit_title" in error_msg  # Shows what was provided

    def test_render_nonexistent_template_raises(self):
        """Should raise PromptError for missing template."""
        clear_cache()
        with pytest.raises(PromptError):
            render_prompt("nonexistent_xyz", foo="bar")

    def test_escaped_braces_in_json(self):
        """Should preserve {{ and }} as literal braces in output."""
        clear_cache()
        result = render_prompt(
            "review",
            commit_title="Test",
            commit_description="Desc",
            diff="diff",
            review_history_section=""
        )
        # The JSON example should have actual braces
        assert '"version": 1' in result
        assert '"decision":' in result


class TestBuildSection:
    """Tests for build_section function."""

    def test_with_content(self):
        """Should format section with header and content."""
        result = build_section("Hello world", "## Title")
        assert result == "## Title\n\nHello world\n"

    def test_with_none_and_empty_msg(self):
        """Should show empty message when content is None."""
        result = build_section(None, "## Title", "Nothing here")
        assert result == "## Title\n\nNothing here\n"

    def test_with_none_and_no_empty_msg(self):
        """Should return empty string when content is None and no empty_msg."""
        result = build_section(None, "## Title")
        assert result == ""

    def test_with_empty_string_content(self):
        """Empty string is falsy, should use empty_msg if provided."""
        result = build_section("", "## Title", "Nothing here")
        assert result == "## Title\n\nNothing here\n"


class TestPromptsDir:
    """Tests for prompts directory configuration."""

    def test_prompts_dir_exists(self):
        """Should point to existing directory."""
        assert PROMPTS_DIR.exists()
        assert PROMPTS_DIR.is_dir()

    def test_all_expected_prompts_exist(self):
        """Should have all expected prompt files."""
        expected = [
            "review.md",
            "review_history.md",
            "implement.md",
            "implement_history.md",
            "breakdown.md",
            "plan_discovery.md",
            "refine_story.md",
            "final_review.md",
        ]
        for filename in expected:
            path = PROMPTS_DIR / filename
            assert path.exists(), f"Missing prompt: {filename}"

    def test_prompts_have_documentation_header(self):
        """All prompts should have HTML comment documentation (in raw file)."""
        for prompt_file in PROMPTS_DIR.glob("*.md"):
            # Read raw file, not through load_prompt (which strips comments)
            content = prompt_file.read_text()
            assert content.startswith("<!--"), f"{prompt_file.name} missing doc header"
            assert "Variables:" in content, f"{prompt_file.name} missing Variables docs"
