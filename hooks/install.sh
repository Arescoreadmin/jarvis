#!/usr/bin/env bash
# JARVIS hook installer for Claude Code CLI and OpenAI Codex CLI.
#
# Usage: bash hooks/install.sh
#
# What it does:
#   1. Generates a JARVIS API token
#   2. Patches ~/.claude/settings.json with PostToolUse + Stop hooks
#   3. Installs the Codex CLI wrapper (if codex is found in PATH)
#   4. Adds JARVIS_URL and JARVIS_TOKEN to ~/.bashrc / ~/.zshrc

set -e

JARVIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JARVIS_URL="${JARVIS_URL:-http://localhost:8765}"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
HOOK_SCRIPT="$JARVIS_DIR/hooks/claude_code_hook.py"

echo "JARVIS Hook Installer"
echo "====================="
echo "JARVIS dir: $JARVIS_DIR"
echo "JARVIS URL: $JARVIS_URL"
echo ""

# ── 1. Generate token ─────────────────────────────────────────────────────────
echo "[1/4] Generating JARVIS API token..."
TOKEN=$(cd "$JARVIS_DIR" && python3 -c "
import sys, os
sys.path.insert(0, '.')
from api.server import create_token
print(create_token())
" 2>/dev/null || echo "")

if [[ -z "$TOKEN" ]]; then
    echo "  ⚠  Could not generate token — JARVIS may not be set up yet."
    echo "     Run 'make token' after setup and set JARVIS_TOKEN manually."
    TOKEN="REPLACE_WITH_OUTPUT_OF_make_token"
fi
echo "  Token: ${TOKEN:0:20}..."

# ── 2. Patch ~/.claude/settings.json ─────────────────────────────────────────
echo "[2/4] Patching Claude Code settings..."
mkdir -p "$HOME/.claude"

if [[ ! -f "$CLAUDE_SETTINGS" ]]; then
    echo "{}" > "$CLAUDE_SETTINGS"
fi

python3 - "$CLAUDE_SETTINGS" "$HOOK_SCRIPT" "$JARVIS_URL" "$TOKEN" <<'PYEOF'
import json, sys

settings_path, hook_script, jarvis_url, token = sys.argv[1:]

with open(settings_path) as f:
    settings = json.load(f)

hook_cmd = (
    f"JARVIS_URL={jarvis_url} JARVIS_TOKEN={token} "
    f"python3 {hook_script} ${{CLAUDE_HOOK_EVENT_TYPE:-PostToolUse}}"
)

post_tool_hook = {
    "matcher": ".*",
    "hooks": [{"type": "command", "command": f"python3 {hook_script} PostToolUse"}]
}
stop_hook = {
    "hooks": [{"type": "command", "command": f"python3 {hook_script} Stop"}]
}

hooks = settings.setdefault("hooks", {})

# PostToolUse — append if not already present
post_list = hooks.setdefault("PostToolUse", [])
scripts = [h.get("hooks", [{}])[0].get("command", "") for h in post_list if h.get("hooks")]
if not any("claude_code_hook" in s for s in scripts):
    post_list.append(post_tool_hook)

# Stop
stop_list = hooks.setdefault("Stop", [])
scripts = [h.get("hooks", [{}])[0].get("command", "") for h in stop_list if h.get("hooks")]
if not any("claude_code_hook" in s for s in scripts):
    stop_list.append(stop_hook)

# Inject env vars into hook environment section
env = settings.setdefault("env", {})
env["JARVIS_URL"] = jarvis_url
env["JARVIS_TOKEN"] = token

with open(settings_path, "w") as f:
    json.dump(settings, f, indent=2)

print(f"  Patched: {settings_path}")
PYEOF

# ── 3. Install Codex wrapper ──────────────────────────────────────────────────
echo "[3/4] Checking for Codex CLI..."
CODEX_PATH=$(which codex 2>/dev/null || echo "")

if [[ -n "$CODEX_PATH" ]]; then
    CODEX_REAL="${CODEX_PATH}-real"
    if [[ ! -f "$CODEX_REAL" ]]; then
        cp "$CODEX_PATH" "$CODEX_REAL"
        echo "  Backed up: $CODEX_PATH → $CODEX_REAL"
    fi
    cp "$JARVIS_DIR/hooks/codex_wrapper.sh" "$CODEX_PATH"
    chmod +x "$CODEX_PATH"
    # Inject vars into wrapper
    sed -i "s|CODEX_REAL:-codex-real|CODEX_REAL:-${CODEX_REAL}|g" "$CODEX_PATH"
    echo "  Installed Codex wrapper at: $CODEX_PATH"
else
    echo "  Codex not found in PATH — skipping. Install codex CLI and re-run."
fi

# ── 4. Export env vars ────────────────────────────────────────────────────────
echo "[4/4] Adding env vars to shell profile..."
EXPORT_BLOCK="
# JARVIS CLI integration
export JARVIS_URL=\"$JARVIS_URL\"
export JARVIS_TOKEN=\"$TOKEN\"
export CODEX_REAL=\"${CODEX_REAL:-codex-real}\""

for RC in "$HOME/.bashrc" "$HOME/.zshrc"; do
    if [[ -f "$RC" ]]; then
        if ! grep -q "JARVIS_URL" "$RC"; then
            echo "$EXPORT_BLOCK" >> "$RC"
            echo "  Updated: $RC"
        else
            echo "  Already present in: $RC"
        fi
    fi
done

echo ""
echo "Done. Reload your shell:"
echo "  source ~/.bashrc   (or ~/.zshrc)"
echo ""
echo "Verify with:"
echo "  curl -s -X POST $JARVIS_URL/events/dev \\"
echo "    -H 'Authorization: Bearer $TOKEN' \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"source\":\"test\",\"event_type\":\"test\",\"cwd\":\"$(pwd)\",\"payload\":{}}'"
