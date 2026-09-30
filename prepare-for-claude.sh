#!/bin/bash

# Configuration
PROJECT_NAME="$(basename "$PWD")"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
OUTPUT_NAME="claude_handoff_${PROJECT_NAME}_${TIMESTAMP}.zip"

# Windows Desktop path with proper quote handling for spaces
WIN_DESKTOP="/mnt/c/Users/Vaibhav Verma/Desktop"

if [ ! -d "$WIN_DESKTOP" ]; then
    echo "Desktop path not found at: $WIN_DESKTOP"
    exit 1
fi

DEST_PATH="$WIN_DESKTOP/$OUTPUT_NAME"
TEMP_DIR=$(mktemp -d)
BUNDLE_DIR="$TEMP_DIR/project_bundle"
mkdir -p "$BUNDLE_DIR"

echo "Generating directory map..."
if command -v tree &> /dev/null; then
    tree -I "node_modules|.git|dist|build|.next|.cache|venv|__pycache__" --dirsfirst > "$BUNDLE_DIR/PROJECT_MAP.txt"
else
    find . -maxdepth 3 -not -path '*/.*' -not -path './node_modules*' > "$BUNDLE_DIR/PROJECT_MAP.txt"
fi

# Extract git status/log if commits exist (ignore error if empty)
if [ -d ".git" ]; then
    echo "Checking git status..."
    git log -n 15 --oneline > "$BUNDLE_DIR/RECENT_CHANGES.txt" 2>/dev/null || echo "No commits yet." > "$BUNDLE_DIR/RECENT_CHANGES.txt"
    git status -s >> "$BUNDLE_DIR/RECENT_CHANGES.txt" 2>/dev/null || true
fi

echo "Compressing project..."
zip -r "$DEST_PATH" . \
    -x "node_modules/*" \
    -x ".git/*" \
    -x "dist/*" \
    -x "build/*" \
    -x ".next/*" \
    -x ".cache/*" \
    -x "venv/*" \
    -x "__pycache__/*" \
    -x "*.zip"

# Append the generated context files into the zip
zip -u -j "$DEST_PATH" "$BUNDLE_DIR/PROJECT_MAP.txt" "$BUNDLE_DIR/RECENT_CHANGES.txt" 2>/dev/null || true
rm -rf "$TEMP_DIR"

echo "Ready on Windows Desktop: $DEST_PATH"
