#!/bin/bash
# =============================================================================
# Hashd Setup Script
# =============================================================================
# Run this after cloning to set up the development environment.
# =============================================================================

set -e

echo "Setting up Hashd..."

# =============================================================================
# Check for uv
# =============================================================================
if ! command -v uv &> /dev/null; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    echo "Please restart your shell or run: source ~/.bashrc"
    echo "Then run this script again."
    exit 1
fi

# =============================================================================
# Sync dependencies (uv handles venv automatically)
# =============================================================================
echo "Syncing dependencies..."
uv sync

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
# Set up wf command
# =============================================================================
echo "Setting up wf command..."
mkdir -p ~/.local/bin
ln -sf "$(pwd)/bin/wf" ~/.local/bin/wf

# =============================================================================
# Done
# =============================================================================
echo ""
echo "Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Verify wf is working:"
echo "     wf --help"
echo ""
echo "  2. Register your project:"
echo "     wf project add /path/to/your/repo"
echo ""
echo "  3. Plan a story:"
echo "     wf plan story \"Add new feature\""
echo ""
echo "  4. Run the pipeline:"
echo "     wf run STORY-0001 --loop"
echo ""
