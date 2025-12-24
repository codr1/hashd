"""
Safe .env file parser.

Parses KEY=value files without shell execution.
Rejects dangerous patterns that could enable injection.
"""

import re
from pathlib import Path

FORBIDDEN_PATTERNS = [
    r'`',           # backticks
    r'\$\(',        # command substitution
    r'\$\{',        # variable expansion
    r';',           # command chaining
    r'&&',          # AND chaining
    r'\|\|',        # OR chaining
    r'\|',          # pipe
]

KEY_PATTERN = re.compile(r'^[A-Z][A-Z0-9_]*$')


def load_env(filepath: str) -> dict:
    """
    Parse env file safely, return dict.

    Raises:
        FileNotFoundError: if file doesn't exist
        ValueError: if syntax invalid or forbidden pattern found
    """
    result = {}
    path = Path(filepath)

    if not path.exists():
        raise FileNotFoundError(f"Env file not found: {filepath}")

    for lineno, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()

        # Skip empty and comments
        if not line or line.startswith('#'):
            continue

        # Must have =
        if '=' not in line:
            raise ValueError(f"Line {lineno}: Invalid syntax (no '=')")

        key, _, value = line.partition('=')
        key = key.strip()
        value = value.strip()

        # Validate key
        if not KEY_PATTERN.match(key):
            raise ValueError(f"Line {lineno}: Invalid key '{key}'")

        # Strip quotes if present
        if len(value) >= 2:
            if (value.startswith('"') and value.endswith('"')) or \
               (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]

        # Check for forbidden patterns
        for pattern in FORBIDDEN_PATTERNS:
            if re.search(pattern, value):
                raise ValueError(f"Line {lineno}: Forbidden pattern in value")

        result[key] = value

    return result
