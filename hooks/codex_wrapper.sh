#!/usr/bin/env bash
# Codex CLI wrapper — wraps `codex` and fires JARVIS events on session start/end.
#
# Setup:
#   1. Rename the real codex binary: mv $(which codex) $(dirname $(which codex))/codex-real
#   2. Copy this script to the same location: cp hooks/codex_wrapper.sh /usr/local/bin/codex
#   3. chmod +x /usr/local/bin/codex
#   4. Set JARVIS_URL and JARVIS_TOKEN in your shell profile.
#
# Or use the install script: bash hooks/install.sh

JARVIS_URL="${JARVIS_URL:-http://localhost:8765}"
JARVIS_TOKEN="${JARVIS_TOKEN:-}"
CODEX_REAL="${CODEX_REAL:-codex-real}"
SESSION_ID="codex-$(date +%s)-$$"

_post() {
    local payload="$1"
    if [[ -z "$JARVIS_TOKEN" ]]; then return; fi
    curl -s \
        --max-time 2 \
        -X POST "$JARVIS_URL/events/dev" \
        -H "Authorization: Bearer $JARVIS_TOKEN" \
        -H "Content-Type: application/json" \
        -d "$payload" \
        > /dev/null 2>&1 &
}

_json_escape() {
    python3 -c "import json,sys; print(json.dumps(sys.stdin.read()))" <<< "$1" 2>/dev/null || echo "\"$1\""
}

CWD_JSON=$(_json_escape "$PWD")
ARGS_JSON=$(_json_escape "$*")
SESSION_JSON=$(_json_escape "$SESSION_ID")

_post "{
    \"source\": \"codex\",
    \"event_type\": \"session_start\",
    \"session_id\": $SESSION_JSON,
    \"cwd\": $CWD_JSON,
    \"payload\": {\"args\": $ARGS_JSON}
}"

# Run the real codex binary
"$CODEX_REAL" "$@"
EXIT_CODE=$?

_post "{
    \"source\": \"codex\",
    \"event_type\": \"session_end\",
    \"session_id\": $SESSION_JSON,
    \"cwd\": $CWD_JSON,
    \"payload\": {\"exit_code\": $EXIT_CODE, \"args\": $ARGS_JSON}
}"

exit $EXIT_CODE
