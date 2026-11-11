"""Demo mode (LCARS_DEMO=1): a fictional herdr session, so the console can be tried or screenshotted without herdr.

Shapes match herdr's session.snapshot / pane.read results closely enough for server.py. Everything here is made up.
"""

import time

COLS, ROWS = 120, 36

WORKSPACES = [("w1", "Platform"), ("w2", "Research")]
# tab id, workspace, label, kind (None = shell), status, preview lines
TABS = [
    ("w1:t1", "w1", "api-refactor", "agent", "working", [
        "⏺ Moving the v1 handlers behind the new router. 14 of 22 endpoints done.",
        "  ⎿  Edited src/routes/orders.ts (+48 −31)",
        "  ⎿  Ran npm test -- routes (212 passed)",
        "⏺ Next: payments and refunds, then delete the legacy dispatcher.",
    ]),
    ("w1:t2", "w1", "flaky-tests", "agent", "blocked", [
        "⏺ The timeout in checkout.spec comes from a shared fixture that never resets the clock.",
        "  Do you want me to make the fixture per-test? This touches 9 spec files.",
        "  ❯ 1. Yes, update all 9",
        "    2. Only checkout.spec for now",
    ]),
    ("w1:t3", "w1", "docs-site", "agent", "done", [
        "⏺ Rebuilt the docs site with the new navigation. All 64 pages render; 3 broken links fixed.",
        "  Preview: http://localhost:4321  ·  branch docs/nav-v2 pushed",
        "✻ Worked for 6m 12s · done 2:41 PM",
    ]),
    ("w1:t4", "w1", "release-notes", "agent", "idle", [
        "⏺ Drafted release notes for 3.8.0 from 41 merged PRs, grouped into features, fixes and internals.",
        "  The draft is in RELEASE_NOTES.md. Waiting for your edits.",
    ]),
    ("w2:t1", "w2", "perf-profile", "agent", "working", [
        "⏺ Profiling the import job: 71% of wall time is JSON parsing of the vendor feed.",
        "  ⎿  Ran py-spy record -o import.svg -- python -m jobs.import",
        "⏺ Trying a streaming parser next.",
    ]),
    ("w2:t2", "w2", "data-cleanup", "agent", "idle", [
        "⏺ Found 1,204 duplicate customer rows across two sources; matched on normalized email.",
        "  Dry run written to out/dedupe_plan.csv. Nothing deleted yet.",
    ]),
    ("w2:t3", "w2", "scratch", None, "unknown", [
        "~/projects % git status -sb",
        "## main...origin/main",
        "~/projects %",
    ]),
]


def snapshot(now: float | None = None) -> dict:
    now = time.time() if now is None else now
    flip = int(now // 9) % 2  # api-refactor alternates working / done so the board (and its alert) is alive
    tabs, panes, agents, layouts = [], [], [], []
    for i, (tid, ws, label, kind, status, _) in enumerate(TABS):
        if label == "api-refactor":
            status = "working" if flip == 0 else "done"
        pid = tid.replace(":t", ":p")
        tabs.append({"tab_id": tid, "workspace_id": ws, "label": label, "number": i + 1, "pane_count": 1,
                     "agent_status": status, "focused": False})
        panes.append({"pane_id": pid, "tab_id": tid, "workspace_id": ws, "agent_status": status,
                      "cwd": f"/home/demo/{label}", "foreground_cwd": f"/home/demo/{label}",
                      "scroll": {"offset_from_bottom": 0, "max_offset_from_bottom": 0, "viewport_rows": ROWS}})
        if kind:
            agents.append({"pane_id": pid, "tab_id": tid, "workspace_id": ws, "name": label, "agent": "agent",
                           "agent_status": status, "terminal_title_stripped": label})
        layouts.append({"tab_id": tid, "workspace_id": ws,
                        "panes": [{"pane_id": pid, "rect": {"x": 0, "y": 0, "width": COLS, "height": ROWS}}]})
    return {"version": "demo", "workspaces": [{"workspace_id": w, "label": l, "number": n + 1}
                                             for n, (w, l) in enumerate(WORKSPACES)],
            "tabs": tabs, "panes": panes, "agents": agents, "layouts": layouts}


def previews() -> dict:
    return {tid.replace(":t", ":p"): lines for tid, _, _, _, _, lines in TABS}


def _c(r, g, b):
    return f"\x1b[38;2;{r};{g};{b}m"


RESET, DIM, BOLD = "\x1b[0m", _c(130, 130, 140), "\x1b[1m"
ACCENT, OK, ARROW = _c(255, 170, 90), _c(120, 200, 120), _c(140, 170, 255)


def frame(pane_id: str) -> str:
    """A rendered screen for a demo pane, as herdr's pane.read(visible, ansi) would return it."""
    tab = next((t for t in TABS if t[0].replace(":t", ":p") == pane_id), TABS[0])
    _, _, label, kind, status, lines = tab
    rows = []
    if kind is None:
        rows = [ln for ln in lines]
        return "\r\n".join(rows) + " "
    rows += [f"{DIM}  ~/{label} · demo session{RESET}", ""]
    rows += [f"{ACCENT}❯{RESET} Pick up where we left off on {label}.", ""]
    history = [
        f"{OK}⏺{RESET} Read the task notes and the last three commits.",
        f"  {DIM}⎿  Read NOTES.md (88 lines){RESET}",
        f"  {DIM}⎿  Ran git log --oneline -3{RESET}",
        "",
    ]
    rows += history
    for ln in lines:
        if ln.startswith("⏺"):
            rows.append(f"{OK}⏺{RESET}{ln[1:]}")
        elif "⎿" in ln:
            rows.append(f"{DIM}{ln}{RESET}")
        elif ln.strip().startswith("❯"):
            rows.append(f"{ARROW}{ln}{RESET}")
        else:
            rows.append(ln)
    rows.append("")
    while len(rows) < ROWS - 4:
        rows.insert(2, "")
    rule = "─" * (COLS - 2)
    mode = "esc to interrupt" if status == "working" else "? for shortcuts"
    rows += [f"{DIM}{rule}{RESET}", f"{ACCENT}❯{RESET} ", f"{DIM}{rule}{RESET}", f"{DIM}  {mode}{RESET}"]
    return "\r\n".join(rows[-ROWS:])
