#!/bin/bash
# =============================================================================
# Hashd Setup Script
# =============================================================================
# Run this after cloning to set up the development environment.
# =============================================================================

set -e

echo "Setting up Hashd..."

# =============================================================================
# Create virtual environment if needed
# =============================================================================
if [ ! -d ".venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv .venv
fi

# Activate virtual environment
source .venv/bin/activate

# =============================================================================
# Install dependencies
# =============================================================================
echo "Installing Python dependencies..."
pip install -r requirements.txt --quiet

# Install dev dependencies if they exist
if [ -f "requirements-dev.txt" ]; then
    pip install -r requirements-dev.txt --quiet
fi

# =============================================================================
# Install git hooks
# =============================================================================
echo "Installing git hooks..."
if [ -d ".git" ]; then
    ln -sf ../../hooks/pre-commit .git/hooks/pre-commit
    chmod +x .git/hooks/pre-commit
    echo "Git hooks installed."
else
    echo "Warning: Not a git repository. Skipping hook installation."
fi

# =============================================================================
# Create directory structure
# =============================================================================
echo "Creating directory structure..."
mkdir -p projects workstreams worktrees runs locks cache secrets config

# =============================================================================
# Done
# =============================================================================
echo ""
echo "Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Create a project configuration:"
echo "     mkdir -p projects/myproject"
echo "     Edit projects/myproject/project.env"
echo "     Edit projects/myproject/project_profile.env"
echo ""
echo "  2. Create a workstream:"
echo "     ./bin/wf new my_feature \"Add new feature\""
echo ""
echo "  3. Run the pipeline:"
echo "     ./bin/wf run my_feature --loop"
echo ""
