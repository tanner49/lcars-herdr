# herdr socket API: notes from building LCARS

Tested against herdr 0.9.3 (protocol 22) on macOS. The installed binary is the authority: `herdr api schema --output schema.json` writes the full JSON schema, which has 128 methods.

## Transport

- **Socket:** a unix socket at `$HERDR_SOCKET_PATH` (default `~/.config/herdr/herdr.sock`).
- **Request:** write one JSON line, `{"id", "method", "params"}`.
- **Response:** read one JSON line back, either `{"id", "result"}` or `{"id", "error": {code, message}}`.
- **One request per connection:** herdr closes the connection after each response, so open a fresh one per call.
- **Speed:** calls are fast, about 0.3 ms for `pane.read` or `pane.send_text` on an M3 Pro.
- **Python asyncio gotcha:** `StreamReader.readline()` added about 105 ms per response on this socket. Plain `loop.sock_connect` / `sock_sendall` / `sock_recv` don't have that problem (see `LineSocket` in `server.py`).

## Reading state

- **Everything at once:** `session.snapshot` returns all workspaces, tabs, panes, agents and layouts in one call (about 14 KB for 10 tabs).
  - Agents carry `name`, `agent` (kind), `agent_status` (`idle`, `working`, `blocked`, `done` or `unknown`), title and cwd.
  - Layouts carry each pane's rect in cells.
- **Per object:** there are also `workspace|tab|pane|agent .list` and `.get` calls.
- **Pane size:** don't trust the layout rect. After the herdr window was resized, `layouts[].rect` and `pane.layout` reported 120×40 while the panes' terminals were really 160×47. No call reports a pane's columns. What worked:
  - **rows:** `scroll.viewport_rows`, which is accurate;
  - **columns:** the widest rendered line, or the rect's width when its height agrees with `viewport_rows`.

  Sizing a mirror too narrow wraps lines and shifts every row, so mouse reports land rows off.
- **Who sets the size:** panes follow the attached herdr TUI client's window (the server log shows `client connected … cols= rows=`), and keep the last client's size after it detaches. After a server restart, restored panes spawn at the first client's size, or 80×24 with none attached. New tabs spawn at the saved layout size (120×40 here). One 54×23 client that ran for 10 s at login left every restored pane at 53×23. The API has no call to set a pane's terminal size (`pane.resize` moves split borders).
- **Pane scrollback:** `pane.get` includes `scroll: {offset_from_bottom, max_offset_from_bottom, viewport_rows}`. Agents that run full-screen (in the terminal's alternate screen), such as Claude Code, report `max_offset_from_bottom: 0`, meaning herdr keeps no scrollback for them.

## Reading output

- **`pane.read`** takes:
  - `source`: `visible`, `recent`, `recent_unwrapped` or `detection`;
  - `format`: `text` or `ansi`;
  - `lines`: how many lines to return.
- **Colours:** `visible` with `ansi` returns the rendered viewport with SGR colours. Rows keep cells written after deletes (written over with spaces), so a typed trailing space can't be told apart from a stale one.
- **Not available:**
  - no cursor position anywhere in the API;
  - no raw output stream;
  - no per-client attach or PTY size;
  - the read `revision` field stayed at 0.
- **Output-change events:** the event catalogue lists `pane_output_changed`, but `events.wait` rejects it with "currently supports pane agent status matches", and `events.subscribe` has no output subscription. `pane.output_matched` (push when a substring or regex matches) exists but wasn't used.

## Writing

- **`pane.send_text`** writes raw bytes. Control characters, escape sequences, bracketed-paste markers (`\x1b[200~…\x1b[201~`) and SGR mouse reports (`\x1b[<b;x;yM` / `m`) all work. That's what makes an interactive mirror possible.
- **`pane.send_keys`** takes logical key names (`enter`, `esc`, `up`, `tab`, `ctrl+c`, …). It has no wheel or page keys.
- **`pane.scroll {offset_from_bottom}`** scrolls herdr's host scrollback, and `visible` reads follow it.
- **`agent.prompt`** does a bracketed paste plus Enter, optionally waits, and refuses if the agent is `blocked`.
- **Focus:** `tab.focus`, `pane.focus`, `agent.focus` and `workspace.focus` change what the herdr TUI shows.
- **Create and close:** `tab.create` (label, cwd, env, `focus: false`) returns the tab and root pane, and `tab.close` closes. Likewise `workspace.create` and `workspace.close`.
- **Rename:** `workspace.rename` and `tab.rename`.

## Events

`events.subscribe` keeps a connection open and pushes `{"event", "data"}` lines. You can subscribe to:
- tab, pane, workspace and worktree lifecycle events;
- `layout.updated`;
- per-pane `pane.agent_status_changed`, `pane.output_matched` and `pane.scroll_changed`.

## Mirroring a pane: what works

1. Poll `pane.read(visible, ansi)` and redraw into xterm.js only when it changes. Poll right after input, then every 8 ms for about a second, otherwise every 50 ms. Keystroke to painted echo comes out at about 30 ms in Chrome.
2. Forward xterm.js `onData` as raw `pane.send_text`.
3. herdr's frames don't carry the app's terminal modes. So, locally:
   - turn on bracketed paste (`?2004h`);
   - for agent panes, turn on button-event mouse reporting (`?1002h` + `?1006h`), so clicks and drags reach the app as they would in herdr.
4. Fit by font size, not a CSS transform, because xterm maps the mouse to cells using its own cell size. Binary-search line height to fill the height, since row heights snap to device pixels.
5. Infer the cursor:
   - **row:** the agent's `❯` input row, or the last non-blank row;
   - **column:** the end of the visible text, plus spaces typed after it, minus arrow-key moves;
   - **prompt:** a shell prompt ending in `% `, `$ ` or `❯ ` sets the left limit.
