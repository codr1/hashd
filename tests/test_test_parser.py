"""Tests for test output parser."""

import pytest
from orchestrator.lib.test_parser import (
    parse_test_output,
    format_parsed_output,
    ParsedTestOutput,
    FailureInfo,
)


class TestGoTestParser:
    """Tests for Go test output parsing."""

    def test_parses_build_failures(self):
        stdout = """
FAIL	github.com/example/pkg1 [build failed]
ok  	github.com/example/pkg2	(cached)
FAIL	github.com/example/pkg3 [build failed]
"""
        result = parse_test_output(stdout, "")
        build_failures = [f for f in result.failures if f.failure_type == "build"]
        assert len(build_failures) == 2
        assert "pkg1" in build_failures[0].name
        assert "pkg3" in build_failures[1].name

    def test_parses_compile_errors(self):
        stderr = """
# github.com/example/pkg
file.go:14:2: undefined: foo
file.go:20:5: cannot use x (type int) as type string
"""
        result = parse_test_output("", stderr)
        compile_errors = [f for f in result.failures if f.failure_type == "compile"]
        assert len(compile_errors) == 2
        assert compile_errors[0].file == "file.go"
        assert compile_errors[0].line == 14
        assert "undefined: foo" in compile_errors[0].message
        assert compile_errors[1].line == 20

    def test_parses_test_failures_with_details(self):
        stdout = """
--- FAIL: TestSomething (0.01s)
    foo_test.go:42: Expected 1, got 2
--- FAIL: TestAnotherThing (0.00s)
    bar_test.go:56: assertion failed
FAIL
"""
        result = parse_test_output(stdout, "")
        test_failures = [f for f in result.failures if f.failure_type == "test"]
        assert len(test_failures) == 2

        # First failure
        assert test_failures[0].name == "TestSomething"
        assert test_failures[0].file == "foo_test.go"
        assert test_failures[0].line == 42
        assert "Expected 1, got 2" in test_failures[0].message

        # Second failure
        assert test_failures[1].name == "TestAnotherThing"
        assert test_failures[1].file == "bar_test.go"
        assert test_failures[1].line == 56
        assert "assertion failed" in test_failures[1].message

    def test_parses_testify_style_output(self):
        stdout = """
--- FAIL: TestComplex (0.00s)
    complex_test.go:100:
        	Error Trace:	complex_test.go:100
        	Error:      	Not equal:
        	            	expected: "foo"
        	            	actual  : "bar"
FAIL
"""
        result = parse_test_output(stdout, "")
        test_failures = [f for f in result.failures if f.failure_type == "test"]
        assert len(test_failures) == 1
        assert test_failures[0].name == "TestComplex"
        assert test_failures[0].file == "complex_test.go"
        assert test_failures[0].line == 100
        # Should extract the error message, not the trace
        assert "Not equal:" in test_failures[0].message or "expected:" in test_failures[0].message

    def test_parses_subtest_failures(self):
        stdout = """
--- FAIL: TestParent/subtest_case (0.00s)
    parent_test.go:30: subtest failed
FAIL
"""
        result = parse_test_output(stdout, "")
        test_failures = [f for f in result.failures if f.failure_type == "test"]
        assert len(test_failures) == 1
        assert test_failures[0].name == "TestParent/subtest_case"

    def test_deduplicates_compile_errors(self):
        """Same error in both stdout and stderr should only appear once."""
        error = "file.go:10:5: undefined: x"
        stdout = f"# pkg\n{error}"
        stderr = f"# pkg\n{error}"
        result = parse_test_output(stdout, stderr)
        compile_errors = [f for f in result.failures if f.failure_type == "compile"]
        assert len(compile_errors) == 1

    def test_summary_includes_counts(self):
        stdout = "FAIL	pkg1 [build failed]"
        stderr = "file.go:1:1: error"
        result = parse_test_output(stdout, stderr)
        assert "1 compile error" in result.summary
        assert "1 package" in result.summary


class TestPytestParser:
    """Tests for pytest output parsing."""

    def test_parses_failed_tests(self):
        stdout = """
FAILED tests/test_foo.py::test_something - AssertionError
FAILED tests/test_bar.py::test_other
"""
        result = parse_test_output(stdout, "")
        assert len(result.failures) == 2
        assert result.failures[0].name == "test_something"
        assert result.failures[0].file == "tests/test_foo.py"
        assert result.failures[1].name == "test_other"

    def test_extracts_assertion_messages(self):
        stdout = """
FAILED tests/test_foo.py::test_math
E       assert 1 == 2
E       AssertionError: Values differ
"""
        result = parse_test_output(stdout, "")
        assert len(result.failures) == 1
        # Should get assertion message
        assert result.failures[0].message  # Not empty

    def test_parses_collection_errors(self):
        stdout = "ERROR collecting tests/broken.py"
        result = parse_test_output(stdout, "")
        assert len(result.failures) == 1
        assert result.failures[0].failure_type == "build"

    def test_extracts_module_not_found(self):
        stdout = """
ERROR collecting tests/broken.py
ModuleNotFoundError: No module named 'nonexistent'
"""
        result = parse_test_output(stdout, "")
        assert len(result.failures) == 1
        assert "nonexistent" in result.failures[0].message

    def test_extracts_line_numbers_from_traceback(self):
        stdout = """
tests/test_foo.py:42: in test_something
    assert x == y
FAILED tests/test_foo.py::test_something - AssertionError
"""
        result = parse_test_output(stdout, "")
        assert len(result.failures) == 1
        assert result.failures[0].line == 42


class TestFormatParsedOutput:
    """Tests for output formatting."""

    def test_formats_compile_errors(self):
        parsed = ParsedTestOutput(
            failures=[
                FailureInfo("file.go", "file.go", 10, "undefined: x", "compile"),
            ],
            summary="1 compile error",
        )
        output = format_parsed_output(parsed)
        assert "**1 compile error**" in output
        assert "`file.go:10`" in output
        assert "undefined: x" in output

    def test_formats_test_failures_with_location(self):
        parsed = ParsedTestOutput(
            failures=[
                FailureInfo("TestFoo", "foo_test.go", 42, "expected 1 got 2", "test"),
            ],
            summary="1 test failed",
        )
        output = format_parsed_output(parsed)
        assert "`TestFoo`" in output
        assert "`foo_test.go:42`" in output
        assert "expected 1 got 2" in output

    def test_formats_empty_output(self):
        parsed = ParsedTestOutput(raw_output="some raw output")
        output = format_parsed_output(parsed)
        assert "some raw output" in output
        assert "```" in output  # Should be in code block

    def test_truncates_long_lists(self):
        failures = [
            FailureInfo(f"error{i}.go", f"error{i}.go", i, "error", "compile")
            for i in range(15)
        ]
        parsed = ParsedTestOutput(failures=failures, summary="15 errors")
        output = format_parsed_output(parsed)
        assert "... and 5 more" in output

    def test_truncates_long_messages(self):
        long_message = "x" * 200
        parsed = ParsedTestOutput(
            failures=[
                FailureInfo("TestFoo", "foo_test.go", 1, long_message, "test"),
            ],
            summary="1 test failed",
        )
        output = format_parsed_output(parsed)
        assert "..." in output  # Should be truncated
        assert "x" * 151 not in output  # Should not have full message


class TestFallback:
    """Tests for fallback behavior."""

    def test_returns_raw_on_unparseable(self):
        stdout = "Something weird happened\nNo recognizable patterns here"
        result = parse_test_output(stdout, "")
        assert result.is_empty()
        assert "Something weird" in result.raw_output

    def test_fallback_summary(self):
        result = parse_test_output("random output", "")
        assert "unparsed" in result.summary.lower()


class TestEdgeCases:
    """Tests for edge cases."""

    def test_empty_input(self):
        result = parse_test_output("", "")
        assert result.is_empty()

    def test_only_passing_tests(self):
        stdout = """
ok  	github.com/example/pkg1	0.001s
ok  	github.com/example/pkg2	(cached)
"""
        result = parse_test_output(stdout, "")
        # No failures, so should return empty (fallback won't trigger on success)
        assert result.is_empty()

    def test_handles_ansi_codes_in_output(self):
        """Real output often has ANSI color codes."""
        stdout = "\x1b[32mRunning tests...\x1b[0m\nFAIL\tpkg [build failed]"
        result = parse_test_output(stdout, "")
        # Should still parse despite ANSI codes
        assert len(result.failures) >= 1
