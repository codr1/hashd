"""
Parse test output to extract structured failure information.

Supports:
- Go test output (go test, build failures, assertion details)
- pytest output (Python)

Returns structured data that can be formatted for LLM consumption.
"""

import re
from dataclasses import dataclass, field


@dataclass
class FailureInfo:
    """A single test or build failure."""
    name: str  # Test name or package name
    file: str | None = None  # Source file
    line: int | None = None  # Line number
    message: str = ""  # Error message
    failure_type: str = "test"  # "test", "build", "compile"


@dataclass
class ParsedTestOutput:
    """Structured test output."""
    failures: list[FailureInfo] = field(default_factory=list)
    summary: str = ""  # One-line summary
    raw_output: str = ""  # Original output (truncated)

    def is_empty(self) -> bool:
        return len(self.failures) == 0


def parse_test_output(stdout: str, stderr: str) -> ParsedTestOutput:
    """
    Parse test output and extract structured failure info.

    Tries Go parser first, then pytest, falls back to raw output.
    """
    combined = f"{stdout}\n{stderr}"

    # Try Go test parser
    result = _parse_go_test(stdout, stderr)
    if not result.is_empty():
        return result

    # Try pytest parser
    result = _parse_pytest(stdout, stderr)
    if not result.is_empty():
        return result

    # No structured parsing worked - return raw
    return ParsedTestOutput(
        raw_output=_truncate(combined, 2000),
        summary="Test failed (unparsed output)"
    )


def _parse_go_test(stdout: str, stderr: str) -> ParsedTestOutput:
    """Parse Go test output."""
    failures = []

    # Pattern: FAIL package [build failed]
    build_fail_pattern = re.compile(r'^FAIL\s+(\S+)\s+\[build failed\]', re.MULTILINE)
    for match in build_fail_pattern.finditer(stdout):
        pkg = match.group(1)
        failures.append(FailureInfo(
            name=pkg,
            failure_type="build",
            message="Build failed"
        ))

    # Pattern: --- FAIL: TestName (duration)
    # Followed by:     file_test.go:line: message
    test_fail_pattern = re.compile(
        r'^--- FAIL: (\S+)\s+\([\d.]+s\)\n((?:[ \t]+.*\n)*)',
        re.MULTILINE
    )
    for match in test_fail_pattern.finditer(stdout):
        test_name = match.group(1)
        detail_block = match.group(2)

        # Extract file:line: message from first detail line
        file_path = None
        line_num = None
        message = ""

        if detail_block:
            lines = detail_block.split('\n')
            first_line = lines[0] if lines else ""

            # Pattern: whitespace + file.go:line: message
            detail_match = re.match(
                r'^\s+([^\s:]+\.go):(\d+):\s*(.*)$',
                first_line
            )
            if detail_match:
                file_path = detail_match.group(1)
                line_num = int(detail_match.group(2))
                message = detail_match.group(3).strip()

                # If message is empty, grab subsequent non-empty lines
                if not message and len(lines) > 1:
                    message_parts = []
                    for line in lines[1:6]:  # Next 5 lines max
                        stripped = line.strip()
                        if not stripped:
                            continue
                        # Skip testify metadata lines
                        if stripped.startswith('Error Trace:'):
                            continue
                        # Extract actual error from testify "Error:" line
                        if stripped.startswith('Error:'):
                            stripped = stripped[6:].strip()
                        message_parts.append(stripped)
                    message = ' '.join(message_parts)[:200]  # Limit length

        failures.append(FailureInfo(
            name=test_name,
            file=file_path,
            line=line_num,
            message=message,
            failure_type="test"
        ))

    # Pattern: file.go:line:col: error message (compile errors)
    # Track seen errors to avoid duplicates
    seen_compile_errors: set[tuple[str, int, str]] = set()
    compile_error_pattern = re.compile(
        r'^([^\s:]+\.go):(\d+):(\d+):\s*(.+)$',
        re.MULTILINE
    )

    # Check both stderr (primary) and stdout (some tooling puts errors there)
    for source in [stderr, stdout]:
        for match in compile_error_pattern.finditer(source):
            filepath, line, _col, message = match.groups()
            line_num = int(line)
            msg = message.strip()

            # Skip duplicates
            key = (filepath, line_num, msg)
            if key in seen_compile_errors:
                continue
            seen_compile_errors.add(key)

            failures.append(FailureInfo(
                name=filepath,
                file=filepath,
                line=line_num,
                message=msg,
                failure_type="compile"
            ))

    if not failures:
        return ParsedTestOutput()

    # Build summary
    build_fails = [f for f in failures if f.failure_type == "build"]
    test_fails = [f for f in failures if f.failure_type == "test"]
    compile_fails = [f for f in failures if f.failure_type == "compile"]

    parts = []
    if compile_fails:
        parts.append(f"{len(compile_fails)} compile error(s)")
    if build_fails:
        parts.append(f"{len(build_fails)} package(s) failed to build")
    if test_fails:
        parts.append(f"{len(test_fails)} test(s) failed")

    return ParsedTestOutput(
        failures=failures,
        summary=", ".join(parts),
        raw_output=_truncate(f"{stdout}\n{stderr}", 1000)
    )


def _parse_pytest(stdout: str, stderr: str) -> ParsedTestOutput:
    """Parse pytest output."""
    failures = []
    combined = f"{stdout}\n{stderr}"

    # Pattern: FAILED test_file.py::test_name - message
    failed_pattern = re.compile(
        r'^FAILED\s+([^:]+)::(\S+)(?:\s+-\s+(.+))?$',
        re.MULTILINE
    )

    # Build a map of file -> line from traceback (last occurrence wins,
    # which is typically the assertion line rather than the test setup)
    location_pattern = re.compile(r'^([^\s:]+\.py):(\d+):', re.MULTILINE)
    file_to_line: dict[str, int] = {}
    for match in location_pattern.finditer(combined):
        filepath = match.group(1)
        file_to_line[filepath] = int(match.group(2))

    # Pattern: E   AssertionError: message
    # or: E   assert x == y
    assertion_pattern = re.compile(r'^E\s+(?:AssertionError:\s*)?(.+)$', re.MULTILINE)
    assertion_messages = [m.group(1).strip() for m in assertion_pattern.finditer(combined)]

    for match in failed_pattern.finditer(combined):
        filepath, test_name, message = match.groups()

        # Try to get line number from traceback
        line_num = file_to_line.get(filepath)

        # If no message from FAILED line, try to get from assertions
        if not message and assertion_messages:
            message = assertion_messages.pop(0)

        failures.append(FailureInfo(
            name=test_name,
            file=filepath,
            line=line_num,
            message=message or "",
            failure_type="test"
        ))

    if not failures:
        # Check for collection errors
        if "ERROR collecting" in combined or "ModuleNotFoundError" in combined:
            # Try to extract the specific error
            import_error_match = re.search(
                r"ModuleNotFoundError: No module named ['\"]([^'\"]+)['\"]",
                combined
            )
            msg = f"Missing module: {import_error_match.group(1)}" if import_error_match else "Import error"

            failures.append(FailureInfo(
                name="collection",
                failure_type="build",
                message=msg
            ))

    if not failures:
        return ParsedTestOutput()

    return ParsedTestOutput(
        failures=failures,
        summary=f"{len(failures)} test(s) failed",
        raw_output=_truncate(combined, 1000)
    )


def format_parsed_output(parsed: ParsedTestOutput) -> str:
    """
    Format parsed test output for LLM consumption.

    Returns markdown-formatted string with structured failure info.
    """
    if parsed.is_empty():
        if parsed.raw_output:
            return f"```\n{parsed.raw_output}\n```"
        return "No test output available."

    parts = [f"**{parsed.summary}**\n"]

    # Group by type
    compile_errors = [f for f in parsed.failures if f.failure_type == "compile"]
    build_errors = [f for f in parsed.failures if f.failure_type == "build"]
    test_errors = [f for f in parsed.failures if f.failure_type == "test"]

    if compile_errors:
        parts.append("\n**Compile errors:**")
        for err in compile_errors[:10]:  # Limit to 10
            loc = f"{err.file}:{err.line}" if err.file and err.line else err.name
            parts.append(f"- `{loc}`: {err.message}")
        if len(compile_errors) > 10:
            parts.append(f"- ... and {len(compile_errors) - 10} more")

    if build_errors:
        parts.append("\n**Failed to build:**")
        for err in build_errors[:5]:
            parts.append(f"- `{err.name}`")
            if err.message and err.message != "Build failed":
                parts.append(f"  {err.message}")
        if len(build_errors) > 5:
            parts.append(f"- ... and {len(build_errors) - 5} more")

    if test_errors:
        parts.append("\n**Failed tests:**")
        for err in test_errors[:10]:
            loc = f"{err.file}:{err.line}" if err.file and err.line else ""
            if loc:
                parts.append(f"- `{err.name}` at `{loc}`")
            else:
                parts.append(f"- `{err.name}`")
            if err.message:
                # Indent message, truncate if too long
                msg = err.message[:150] + "..." if len(err.message) > 150 else err.message
                parts.append(f"  {msg}")
        if len(test_errors) > 10:
            parts.append(f"- ... and {len(test_errors) - 10} more")

    # Include truncated raw output for context
    if parsed.raw_output:
        parts.append("\n**Raw output (truncated):**")
        parts.append(f"```\n{parsed.raw_output}\n```")

    return "\n".join(parts)


def _truncate(s: str, max_len: int) -> str:
    """Truncate string, keeping the end (most relevant for errors)."""
    if len(s) <= max_len:
        return s.strip()
    return "...(truncated)\n" + s[-max_len:].strip()
