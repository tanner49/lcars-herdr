#!/usr/bin/env bash
# Start a named Claude agent in a new herdr tab and send it a brief.
#
#   spawn_agent.sh <name> <brief-file> [-- extra claude args]
#
# Prints JSON: {"name", "tab_id", "pane_id", "cwd"}. Run from the chief-of-staff pane (inside herdr).
set -euo pipefail

name=${1:?usage: spawn_agent.sh <name> <brief-file> [-- extra claude args]}
brief=${2:?usage: spawn_agent.sh <name> <brief-file> [-- extra claude args]}
shift 2
[[ ${1:-} == "--" ]] && shift
cwd="$HOME/Documents"

[[ "${HERDR_ENV:-}" == 1 ]] || { echo "not running inside a herdr pane" >&2; exit 1; }
[[ $name =~ ^[a-z][a-z0-9_-]{0,31}$ ]] || { echo "name must match [a-z][a-z0-9_-]{0,31}: $name" >&2; exit 2; }
[[ -s $brief ]] || { echo "brief file missing or empty: $brief" >&2; exit 2; }

json_field() { python3 -c "import json,sys; d=json.load(sys.stdin)['result']; print($1)"; }

tab_json=$(herdr tab create --workspace "$HERDR_WORKSPACE_ID" --label "$name" --cwd "$cwd" --no-focus)
tab_id=$(json_field "d['tab']['tab_id']" <<<"$tab_json")
pane_id=$(json_field "d['root_pane']['pane_id']" <<<"$tab_json")

herdr agent start "$name" --kind claude --pane "$pane_id" --timeout 60000 -- -n "$name" --permission-mode auto "$@" >/dev/null
# A long brief can land in the input box without being submitted, so press Enter if the agent doesn't start.
if ! herdr agent prompt "$name" "$(cat "$brief")" --wait --until working --until blocked --timeout 15000 >/dev/null 2>&1; then
  herdr pane send-keys "$pane_id" enter
  sleep 3
  status=$(herdr agent get "$name" | json_field "d['agent']['agent_status']")
  [[ $status == working || $status == blocked ]] || { echo "brief not submitted to $name (status $status); check tab $tab_id" >&2; exit 3; }
fi

printf '{"name": "%s", "tab_id": "%s", "pane_id": "%s", "cwd": "%s"}\n' "$name" "$tab_id" "$pane_id" "$cwd"
pidfile="$HOME/.claude/chief-of-staff/watch/pid"
if ! { [[ -s $pidfile ]] && kill -0 "$(<"$pidfile")" 2>/dev/null; }; then
  echo "watchdog not running: start ~/.claude/skills/chief-of-staff/scripts/watch_agents.sh with run_in_background" >&2
fi
