"""Tests for suggestions module."""

import json
import pytest
from datetime import datetime

from orchestrator.lib.suggestions import (
    Suggestion,
    SuggestionsFile,
    load_suggestions,
    save_suggestions,
    rotate_suggestions,
    create_suggestions_from_discovery,
    get_suggestion_by_id,
    get_suggestion_by_name,
    mark_suggestion_in_progress,
    mark_suggestion_done,
)


class TestSuggestion:
    """Tests for Suggestion dataclass."""

    def test_default_values(self):
        s = Suggestion(id=1, title="Test", summary="Sum", rationale="Rat")
        assert s.status == "available"
        assert s.story_id is None
        assert s.reqs_refs == []

    def test_with_all_fields(self):
        s = Suggestion(
            id=1,
            title="Test",
            summary="Sum",
            rationale="Rat",
            reqs_refs=["Section 1"],
            status="in_progress",
            story_id="STORY-0001",
        )
        assert s.reqs_refs == ["Section 1"]
        assert s.status == "in_progress"
        assert s.story_id == "STORY-0001"


class TestLoadSaveSuggestions:
    """Tests for load_suggestions and save_suggestions."""

    def test_load_returns_none_when_missing(self, tmp_path):
        result = load_suggestions(tmp_path / "nonexistent")
        assert result is None

    def test_save_and_load_roundtrip(self, tmp_path):
        sf = SuggestionsFile(
            generated_at="2024-01-01T10:00:00",
            suggestions=[
                Suggestion(id=1, title="Auth", summary="Add auth", rationale="Security"),
                Suggestion(id=2, title="API", summary="Add API", rationale="Features"),
            ],
        )

        assert save_suggestions(tmp_path, sf)

        loaded = load_suggestions(tmp_path)
        assert loaded is not None
        assert loaded.generated_at == "2024-01-01T10:00:00"
        assert len(loaded.suggestions) == 2
        assert loaded.suggestions[0].title == "Auth"
        assert loaded.suggestions[1].title == "API"

    def test_load_handles_invalid_json(self, tmp_path):
        (tmp_path / "suggestions.json").write_text("not valid json {{{")
        result = load_suggestions(tmp_path)
        assert result is None

    def test_load_handles_missing_fields(self, tmp_path):
        # Minimal valid structure
        data = {
            "generated_at": "2024-01-01",
            "suggestions": [{"id": 1, "title": "T", "summary": "S", "rationale": "R"}],
        }
        (tmp_path / "suggestions.json").write_text(json.dumps(data))

        result = load_suggestions(tmp_path)
        assert result is not None
        assert result.suggestions[0].status == "available"


class TestRotateSuggestions:
    """Tests for rotate_suggestions."""

    def test_returns_none_when_no_file(self, tmp_path):
        result = rotate_suggestions(tmp_path)
        assert result is None

    def test_renames_file_with_timestamp(self, tmp_path):
        sf = SuggestionsFile(
            generated_at="2024-01-15T14:30:00",
            suggestions=[Suggestion(id=1, title="T", summary="S", rationale="R")],
        )
        save_suggestions(tmp_path, sf)

        original_path = tmp_path / "suggestions.json"
        assert original_path.exists()

        rotated_path = rotate_suggestions(tmp_path)
        assert rotated_path is not None
        assert rotated_path.name == "suggestions.2024-01-15_14-30-00.json"
        assert not original_path.exists()
        assert rotated_path.exists()


class TestCreateSuggestionsFromDiscovery:
    """Tests for create_suggestions_from_discovery."""

    def test_creates_from_discovery_data(self):
        data = [
            {"title": "Auth", "summary": "Add auth", "rationale": "Security", "reqs_refs": ["2.1"]},
            {"title": "API", "summary": "Add API", "rationale": "Features"},
        ]

        result = create_suggestions_from_discovery(data)

        assert len(result.suggestions) == 2
        assert result.suggestions[0].id == 1
        assert result.suggestions[0].title == "Auth"
        assert result.suggestions[0].reqs_refs == ["2.1"]
        assert result.suggestions[1].id == 2
        assert result.suggestions[1].title == "API"
        assert result.suggestions[1].reqs_refs == []  # Default
        assert result.generated_at  # Should be set

    def test_handles_missing_fields(self):
        data = [{"title": "Minimal"}]

        result = create_suggestions_from_discovery(data)

        assert result.suggestions[0].title == "Minimal"
        assert result.suggestions[0].summary == ""
        assert result.suggestions[0].rationale == ""

    def test_handles_empty_list(self):
        result = create_suggestions_from_discovery([])
        assert result.suggestions == []


class TestGetSuggestionById:
    """Tests for get_suggestion_by_id."""

    def test_finds_by_id(self):
        sf = SuggestionsFile(
            generated_at="",
            suggestions=[
                Suggestion(id=1, title="First", summary="", rationale=""),
                Suggestion(id=2, title="Second", summary="", rationale=""),
            ],
        )

        result = get_suggestion_by_id(sf, 2)
        assert result is not None
        assert result.title == "Second"

    def test_returns_none_when_not_found(self):
        sf = SuggestionsFile(generated_at="", suggestions=[])
        result = get_suggestion_by_id(sf, 99)
        assert result is None


class TestGetSuggestionByName:
    """Tests for get_suggestion_by_name."""

    def test_finds_by_substring(self):
        sf = SuggestionsFile(
            generated_at="",
            suggestions=[
                Suggestion(id=1, title="User Authentication", summary="", rationale=""),
                Suggestion(id=2, title="API Endpoints", summary="", rationale=""),
            ],
        )

        result = get_suggestion_by_name(sf, "auth")
        assert result is not None
        assert result.id == 1

    def test_case_insensitive(self):
        sf = SuggestionsFile(
            generated_at="",
            suggestions=[
                Suggestion(id=1, title="USER AUTH", summary="", rationale=""),
            ],
        )

        result = get_suggestion_by_name(sf, "user")
        assert result is not None

    def test_returns_none_when_not_found(self):
        sf = SuggestionsFile(
            generated_at="",
            suggestions=[
                Suggestion(id=1, title="Something", summary="", rationale=""),
            ],
        )

        result = get_suggestion_by_name(sf, "nothing")
        assert result is None

    def test_returns_first_match(self):
        sf = SuggestionsFile(
            generated_at="",
            suggestions=[
                Suggestion(id=1, title="User Login", summary="", rationale=""),
                Suggestion(id=2, title="User Logout", summary="", rationale=""),
            ],
        )

        result = get_suggestion_by_name(sf, "user")
        assert result.id == 1  # First match


class TestMarkSuggestionInProgress:
    """Tests for mark_suggestion_in_progress."""

    def test_marks_and_saves(self, tmp_path):
        sf = SuggestionsFile(
            generated_at="",
            suggestions=[
                Suggestion(id=1, title="Test", summary="", rationale=""),
            ],
        )
        save_suggestions(tmp_path, sf)

        result = mark_suggestion_in_progress(tmp_path, 1, "STORY-0001")
        assert result is True

        loaded = load_suggestions(tmp_path)
        assert loaded.suggestions[0].status == "in_progress"
        assert loaded.suggestions[0].story_id == "STORY-0001"

    def test_returns_false_when_not_found(self, tmp_path):
        sf = SuggestionsFile(generated_at="", suggestions=[])
        save_suggestions(tmp_path, sf)

        result = mark_suggestion_in_progress(tmp_path, 99, "STORY-0001")
        assert result is False

    def test_returns_false_when_no_file(self, tmp_path):
        result = mark_suggestion_in_progress(tmp_path / "nonexistent", 1, "STORY-0001")
        assert result is False


class TestMarkSuggestionDone:
    """Tests for mark_suggestion_done."""

    def test_marks_done(self, tmp_path):
        sf = SuggestionsFile(
            generated_at="",
            suggestions=[
                Suggestion(
                    id=1,
                    title="Test",
                    summary="",
                    rationale="",
                    status="in_progress",
                    story_id="STORY-0001",
                ),
            ],
        )
        save_suggestions(tmp_path, sf)

        result = mark_suggestion_done(tmp_path, 1)
        assert result is True

        loaded = load_suggestions(tmp_path)
        assert loaded.suggestions[0].status == "done"
