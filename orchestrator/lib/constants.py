"""Shared constants for the orchestrator."""

import re

# Workstream ID validation
WS_ID_PATTERN = re.compile(r'^[a-z][a-z0-9_]*$')
MAX_WS_ID_LEN = 16
