#!/usr/bin/env bash
# Silent watchdog for chief-of-staff agents. Run it ONCE, in the background (Bash run_in_background).
# It prints nothing and keeps running while agents behave. It exits, which wakes COS, only when an
# agent needs attention:
#   blocked    a permission prompt or question has been open for BLOCKED_GRACE seconds
#   no-report  the agent has been idle/done for IDLE_GRACE seconds and no SendMessage from it reached COS
#              (time doesn't count while its pane footer shows a background shell still running,
#              and an idle compaction, "Compacted while idle", isn't a turn; nor when its latest input was
#              a prompt Tanner typed in its tab, which it answers there)
#   gone       the agent's pane is no longer there
# An agent whose tab Tanner has focused is skipped. State lives in ~/.claude/chief-of-staff/watch/state.json,
# so restarting after an alert loses nothing.
#
#   watch_agents.sh [--once] [--interval 20] [--blocked-grace 45] [--idle-grace 240]
exec python3 -I - "$@" <<'PY'
import glob, json, os, re, subprocess, sys, time

args = sys.argv[1:]
def opt(name, default):
    return int(args[args.index(name) + 1]) if name in args else default
ONCE = "--once" in args
INTERVAL, BLOCKED_GRACE, IDLE_GRACE = opt("--interval", 20), opt("--blocked-grace", 45), opt("--idle-grace", 240)
STATE = os.environ.get("COS_WATCH_STATE") or os.path.expanduser("~/.claude/chief-of-staff/watch/state.json")
os.makedirs(os.path.dirname(STATE), exist_ok=True)
open(os.path.join(os.path.dirname(STATE), "pid"), "w").write(str(os.getpid()))
cos_pane = os.environ.get("HERDR_PANE_ID")
if os.environ.get("HERDR_ENV") != "1" or not cos_pane:
    sys.exit("not running inside a herdr pane")

def herdr(*a):
    return json.loads(subprocess.run(["herdr", *a], capture_output=True, text=True, check=True).stdout)["result"]
def agents():
    return {a["pane_id"]: a for a in herdr("agent", "list")["agents"]}
def tail(name, n=10):
    r = subprocess.run(["herdr", "agent", "read", name, "--source", "recent-unwrapped", "--lines", str(n)],
                       capture_output=True, text=True)
    return r.stdout.strip()

def follow(path):
    # a resumed/continued session ends its old transcript with a "continued-in" record naming the new
    # session, and herdr keeps reporting the old id, so follow that chain to the file COS writes now
    seen = set()
    while True:
        seen.add(path)
        with open(path, "rb") as f:
            f.seek(max(0, os.path.getsize(path) - 65536))
            lines = f.read().splitlines()
        nxt = None
        for line in lines:
            if line.startswith(b'{"type":"continued-in"'):
                try:
                    nxt = json.loads(line).get("continuedInSessionId") or nxt
                except ValueError:
                    pass
        cand = os.path.join(os.path.dirname(path), f"{nxt}.jsonl") if nxt else None
        if not cand or cand in seen or not os.path.exists(cand):
            return path
        path = cand

cos = agents().get(cos_pane)
sid = cos["agent_session"]["value"] if cos else None
paths = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{sid}.jsonl")) if sid else []
if not paths:
    sys.exit("can't find COS's transcript; reports can't be counted")
transcript = follow(paths[0])
size = lambda: os.path.getsize(transcript)

def reports_since(name, offset):
    with open(transcript, "rb") as f:
        f.seek(offset)
        data = f.read()
    # the transcript is JSONL, so the quotes in from-name="x" are stored escaped
    return data.count(f'from-name=\\"{name}\\"'.encode())

def activity(a):
    # newest record timestamp in the agent's own transcript. herdr can bump completion_seq with no new turn
    # (the pane redraws while idle), so an unchanged value means there was no turn to report on
    sid = (a.get("agent_session") or {}).get("value")
    found = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{sid}.jsonl")) if sid else []
    if not found:
        return None
    p = follow(found[0])
    with open(p, "rb") as f:
        f.seek(max(0, os.path.getsize(p) - 65536))
        ts = re.findall(rb'"timestamp":"([^"]+)"', f.read())
    return ts[-1].decode() if ts else None

def typed_by_tanner(pane):
    # the latest input above the input box (the last two rules; its text is unsent): "❯ …" is a prompt typed
    # in the tab, "› Message from @…" came from COS or another agent, and COS's own brief arrives typed.
    # A turn the agent starts itself (a shell finishing, a timer) shows no input and goes with the one before.
    lines = pane.splitlines()
    rules = [i for i, l in enumerate(lines) if l.startswith("───")]
    for l in reversed(lines[:rules[-2]] if len(rules) >= 2 else []):
        if l.startswith("› Message from @"):
            return False
        if l.startswith("❯ "):
            return not l.startswith("❯ You are an agent working for Tanner, dispatched by")
    return False  # can't tell: alert as before

try:
    state = json.load(open(STATE))
except Exception:
    state = {}
idle_since, blocked_since, prev = {}, {}, {}
ppid = os.getppid()

while True:
    if (moved := follow(transcript)) != transcript:
        transcript, prev = moved, {}  # COS continued in a new session mid-run; old offsets don't apply
    polled = size()  # read before statuses, so a turn that starts after this poll writes past it
    live = agents()
    if cos_pane not in live:
        sys.exit(0)
    alerts = []
    named = {a["name"]: a for p, a in live.items() if p != cos_pane and a.get("name")}
    for name, a in named.items():
        st = state.setdefault(name, {"offset": size(), "acked": a.get("completion_seq"), "blocked_acked": None,
                                     "transcript": transcript})
        if st.get("transcript") != transcript:
            # offsets are bytes into one transcript; in a new one, count from its start
            st["offset"], st["transcript"] = 0, transcript
        status, seq = a["agent_status"], a.get("completion_seq")
        if "activity" not in st and st["acked"] == seq:
            st["activity"] = activity(a)  # baseline for an agent whose latest completion is already checked
        if a.get("focused"):
            idle_since.pop(name, None); blocked_since.pop(name, None)
            continue
        before, prev[name] = prev.get(name), (status, polled)
        if status == "working":
            if before and before[0] in ("idle", "done"):
                # a new turn started: count from the last poll that saw it idle, not from now,
                # since a short turn can report before a poll sees it working
                st["offset"] = before[1]
            idle_since.pop(name, None); blocked_since.pop(name, None)
        elif status == "blocked":
            t0 = blocked_since.setdefault(name, time.time())
            if time.time() - t0 >= BLOCKED_GRACE and st["blocked_acked"] != a["state_change_seq"]:
                st["blocked_acked"] = a["state_change_seq"]
                alerts.append((name, "blocked", "waiting on an approval or question; Tanner answers in its tab", tail(name)))
        elif status in ("idle", "done"):
            blocked_since.pop(name, None)
            recent = tail(name, 8)
            if re.search(r"shells? still running|\d+ shells? ·", recent):
                idle_since[name] = time.time()  # waiting on its own background shell: not idle
                continue
            if "Compacted while idle" in recent:
                st["acked"], st["offset"] = seq, size()  # idle compaction isn't a turn: nothing to report
                st["activity"] = activity(a)
                idle_since.pop(name, None)
                continue
            t0 = idle_since.setdefault(name, time.time())
            if time.time() - t0 >= IDLE_GRACE and st["acked"] != seq:
                st["acked"] = seq
                before_act, st["activity"] = st.get("activity"), activity(a)
                new_turn = before_act is None or before_act != st["activity"]
                found = reports_since(name, st["offset"])
                st["offset"] = size()
                if found == 0 and new_turn and not typed_by_tanner(tail(name, 300)):  # acked either way
                    alerts.append((name, "no-report", f"idle {int(time.time() - t0)}s and nothing from it reached COS", tail(name)))
    for name in set(state) - set(named):
        alerts.append((name, "gone", "no longer in herdr", ""))
        del state[name]
    json.dump(state, open(STATE, "w"))
    if alerts or ONCE:
        for name, kind, why, pane in alerts:
            print(f"WATCHDOG {kind}: {name}: {why}")
            if pane:
                print("  | " + pane.replace("\n", "\n  | "))
        sys.exit(0)
    if os.getppid() != ppid:
        sys.exit(0)
    time.sleep(INTERVAL)
PY
