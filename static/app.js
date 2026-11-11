/* LCARS herdr console — browser side. Plain JS, no build step. */
(() => {
  "use strict";
  const TOKEN = window.LCARS_TOKEN;
  const $ = (id) => document.getElementById(id);
  const WS_COLORS = ["c-ice", "c-lavender", "c-peach", "c-gold", "c-orange", "c-tan"];
  const PTT_LABEL = { AltRight: "RIGHT OPTION", MetaRight: "RIGHT COMMAND" };
  const FORCE_MOUSE = /[#?&]mouse=1/.test(location.hash); // tests: treat a scratch shell like an agent pane
  const SHOW_TESTS = /[#?&]tests=1/.test(location.hash);   // tests: show the "lcars-tests" scratch workspaces
  const TEST_WS = "lcars-tests";
  const AUTO_MAX = 16; // AUTO text size cap (px): a small herdr pane leaves space rather than giant text
  const visible = (b) => SHOW_TESTS ? b : { ...b, tabs: b.tabs.filter((t) => t.workspace_label !== TEST_WS),
                                                   workspaces: b.workspaces.filter((w) => w.label !== TEST_WS) };

  const state = { board: null, filter: "all", sel: null /* tab_id */, ws: null, keysOn: false,
                  settings: { ...window.LCARS_SETTINGS }, prevStatus: null };

  // ------------------------------------------------------------------ socket
  function connect() {
    const ws = new WebSocket(`ws://${location.host}/ws?token=${encodeURIComponent(TOKEN)}`);
    state.ws = ws;
    ws.onopen = () => { if (selectedTab()) send({ type: "watch", pane_id: selectedTab().pane_id }); };
    ws.onmessage = (ev) => handle(JSON.parse(ev.data));
    ws.onopen = ((orig) => () => { state.wsFails = 0; orig(); })(ws.onopen);
    // a server restart mints a new token: once the server answers again, reload to pick it up
    // (checking first, so a stopped server leaves the page retrying instead of on an error page)
    ws.onclose = () => {
      state.wsFails = (state.wsFails || 0) + 1;
      setTimeout(async () => {
        if (state.wsFails <= 3) return connect();
        try { if ((await fetch("/", { cache: "no-store" })).ok) return location.reload(); } catch (_) { /* still down */ }
        connect();
      }, 1000);
    };
  }
  function handle(m) {
      if (m.type === "board") { state.board = m.board; render(); }
      else if (m.type === "frame") onFrame(m);
      else if (m.type === "frame_error") toast(`MIRROR: ${m.error}`, "c-red");
      else if (m.type === "scroll") { if (selectedTab()?.pane_id === m.pane_id) { state.scrollOff = m.offset; if (term) placeCursor(); } }
      else if (m.type === "toast") toast(m.text, m.level === "error" ? "c-red" : "c-ice");
      else if (m.type === "settings") { if (!saver.busy && !saver.pending) { state.settings = m.settings; applySettings(); } } // another window
      else if (m.type === "created") { state.pendingSelect = m.tab_id; toast(`TAB ${m.tab_id} CREATED`, "c-ice"); }
  }
  window.__lcars = { state, handle }; // tests: feed synthetic board updates, read state
  const send = (m) => { if (state.ws && state.ws.readyState === 1) state.ws.send(JSON.stringify(m)); };

  // ------------------------------------------------------------------ board
  const selectedTab = () => state.board && state.board.tabs.find((t) => t.tab_id === state.sel);
  const wsColor = (wsId) => {
    const i = (state.board?.workspaces || []).findIndex((w) => w.workspace_id === wsId);
    return WS_COLORS[(i < 0 ? 0 : i) % WS_COLORS.length];
  };
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const home = (p) => (p || "").replace(/^\/Users\/[^/]+/, "~");

  function render() {
    const b = state.board && visible(state.board); // the tests' scratch workspaces never show on the board
    if (!b) return;
    if (state.pendingSelect && b.tabs.some((t) => t.tab_id === state.pendingSelect)) {
      const id = state.pendingSelect; state.pendingSelect = null; select(id);
    }
    if (state.sel && !state.board.tabs.some((t) => t.tab_id === state.sel)) select(null); // gone from herdr, not just hidden

    // workspace filter blocks
    const wf = $("ws-filters");
    const want = b.workspaces.map((w) => `${w.workspace_id}=${w.label}`).join("|");
    if (wf.dataset.key !== want) {
      wf.dataset.key = want;
      wf.innerHTML = b.workspaces.map((w, i) =>
        `<button class="block ${wsColor(w.workspace_id)}" data-filter="${esc(w.workspace_id)}" data-rename="${esc(w.workspace_id)}" title="Click to filter · double-click to rename"><span>${esc(w.label)}</span><i>0${i + 2}</i></button>`).join("");
    }
    document.querySelectorAll("[data-filter]").forEach((el) => el.classList.toggle("active", el.dataset.filter === state.filter));

    // agent alerts: working -> done/idle chirps, anything -> blocked beeps (not on first load)
    const now = Object.fromEntries(b.tabs.map((t) => [t.tab_id, t.status]));
    if (state.prevStatus) {
      for (const [id, st] of Object.entries(now)) {
        const was = state.prevStatus[id];
        if (was === "working" && (st === "done" || st === "idle")) sound("done");
        else if (st === "blocked" && was && was !== "blocked") sound("blocked");
      }
    }
    state.prevStatus = now;

    // readouts
    const count = (s) => b.tabs.filter((t) => t.status === s).length;
    for (const s of ["working", "blocked", "done", "idle"]) $(`ro-${s}`).textContent = count(s);

    // panels grouped by workspace
    const tabs = b.tabs.filter((t) => state.filter === "all" || t.workspace_id === state.filter);
    let html = "", lastWs = null;
    for (const t of tabs) {
      if (t.workspace_id !== lastWs) {
        lastWs = t.workspace_id;
        html += `<div class="ws-head ${wsColor(t.workspace_id)}"><span class="cap"></span><span class="name" data-rename="${esc(t.workspace_id)}" title="Double-click to rename">${esc(t.workspace_label)}</span><span class="rule"></span><button class="ws-rename" data-rename="${esc(t.workspace_id)}" data-click="1">RENAME</button></div>`;
      }
      const who = t.agent ? `${t.agent.kind}${t.agent.name ? " · " + t.agent.name : ""}` : "shell";
      html += `<div class="panel st-${esc(t.status)} ${wsColor(t.workspace_id)} ${t.tab_id === state.sel ? "selected" : ""}" data-tab="${esc(t.tab_id)}">
        <div class="p-head"><span class="p-cap"></span><span class="p-label">${esc(t.label)}</span><span class="p-rule"></span>
          <span class="p-status"><span class="light"></span>${esc(t.status)}</span></div>
        <pre class="p-lines">${t.preview.map(esc).join("\n") || "<span style='color:#5a4a3a'>— no output —</span>"}</pre>
        <div class="p-foot"><span>${esc(t.tab_id)}</span><span>${esc(who)}</span><span>${esc(home(t.cwd))}</span>${t.app_created ? "<span>LCARS-OWNED</span>" : ""}</div>
      </div>`;
    }
    if (!b.herdr.ok) html = `<div class="ws-head c-red"><span class="cap"></span><span class="name">HERDR LINK DOWN: ${esc(b.herdr.error)}</span><span class="rule"></span></div>`;
    $("board").innerHTML = html;

    // detail header + buttons
    const t = selectedTab();
    $("btn-focus").disabled = !t;
    $("btn-close").disabled = !(t && !t.agent); // plain shells only; agent tabs are closed from herdr
    $("btn-close").title = t && t.agent ? "Close agent tabs from herdr" : "Close this shell tab";
    if (t) {
      $("detail-title").textContent = t.label;
      $("detail-light").className = `light st-${t.status}`;
      $("detail-meta").textContent = `${t.status} · ${t.pane_id} · ${t.agent ? t.agent.kind : "shell"} · ${home(t.cwd)}`;
      $("vox-target").textContent = `TARGET: ${t.label}`;
    } else {
      $("vox-target").textContent = "NO TARGET";
    }
    renderVoice(b.voice);
  }

  // ---- rename workspaces (herdr workspace.rename): RENAME pill, or double-click a header / sidebar block
  function renameWorkspace(wsId) {
    const w = state.board?.workspaces.find((x) => x.workspace_id === wsId); if (!w) return;
    promptModal("RENAME WORKSPACE", `${w.label} (${w.workspace_id})`, w.label, (label) => {
      if (label && label !== w.label) send({ type: "rename_workspace", workspace_id: wsId, label });
    });
  }
  document.addEventListener("dblclick", (e) => {
    const r = e.target.closest("[data-rename]"); if (r && !r.dataset.click) renameWorkspace(r.dataset.rename);
  });
  $("board").addEventListener("click", (e) => {
    const r = e.target.closest("[data-rename][data-click]");
    if (r) { renameWorkspace(r.dataset.rename); return; }
    const p = e.target.closest(".panel");
    if (!p) return;
    if (p.dataset.tab !== state.sel) select(p.dataset.tab);
    else { send({ type: "watch", pane_id: selectedTab()?.pane_id }); term?.focus(); } // refresh, don't deselect
  });
  $("sidebar").addEventListener("click", (e) => {
    const f = e.target.closest("[data-filter]");
    if (f) { state.filter = f.dataset.filter; render(); }
  });

  function select(tabId) {
    state.sel = tabId;
    const t = selectedTab();
    $("main").className = t ? "has-selection" : "no-selection";
    $("detail").hidden = !t;
    send({ type: "watch", pane_id: t ? t.pane_id : null });
    if (t) {
      ensureTerm(t.size?.cols || t.rect?.width || 120, t.size?.rows || t.rect?.height || 40); // frames correct it
      term.reset(); resetCur(); state.scrollOff = 0; state.mouseMode = null;
      // ?1047 toggle: xterm draws no cursor until "initialized" (focus or alt-screen), and reset clears it.
      // ?2004h: herdr frames don't carry the app's modes, so turn bracketed paste on locally; otherwise a
      // multi-line paste into Claude Code would submit at every newline.
      term.write("\x1b[?1047h\x1b[?1047l\x1b[?2004h");
      setTimeout(() => { term.focus(); setKeys(document.activeElement === term.textarea); }, 0); // keys go straight to the pane
    }
    history.replaceState(null, "", t ? `#pane=${encodeURIComponent(t.pane_id)}` : "#");
    render();
  }

  // ------------------------------------------------------------------ terminal mirror
  let term = null;
  function ensureTerm(cols, rows) {
    if (!term) {
      term = new Terminal({
        cols, rows, scrollback: 0, convertEol: false,
        cursorBlink: true, cursorStyle: "block", cursorInactiveStyle: "block", // visible even when unfocused
        fontFamily: 'SF Mono, Menlo, monospace', fontSize: 13, lineHeight: 1.0,
        minimumContrastRatio: 4.5, // agent TUIs in a light theme emit dark-on-default text; keep it legible on black
        macOptionClickForcesSelection: true, // in agent panes clicks go to the app; Option-drag still selects to copy
        theme: { background: "#000000", foreground: "#ffd9b3", cursor: "#ffcc66", cursorAccent: "#000000",
                 selectionBackground: "#665544" },
      });
      term.open($("term"));
      term.onData((d) => {
        const t = selectedTab(); if (!t) return;
        const before = [cur.back, cur.trail];
        trackCursorKeys(d);
        sendInput({ type: "input", pane_id: t.pane_id, data: d });
        if (cur.back !== before[0] || cur.trail !== before[1]) placeCursor(); // move now, don't wait for the echo
      });
      term.textarea.addEventListener("focus", () => setKeys(true));
      term.textarea.addEventListener("blur", () => setKeys(false));
      window.__term = term; // for tests/debugging
      new ResizeObserver(() => requestFit()).observe($("term-wrap"));
      $("term-wrap").addEventListener("mousedown", () => setTimeout(() => term.focus(), 0));
      $("term-wrap").addEventListener("mouseup", (e) => setTimeout(() => onMirrorClick(e), 0)); // after xterm's selection settles
      $("term-wrap").addEventListener("wheel", onWheel, { passive: false, capture: true });
    }
    if (term.cols !== cols || term.rows !== rows) term.resize(cols, rows);
    requestFit(); // no-op unless the space, the pane size or the minimum changed
  }
  // Fit the pane's width by changing the font size, never a CSS scale: xterm maps mouse positions to cells
  // with its own cell size, so a scaled terminal selects/clicks rows away from the pointer. If the pane is
  // taller than the viewport, shift it up (layout offset, not a transform) to keep the bottom in view.
  // One fit at a time, and only when its inputs change (frames arriving don't refit, so the layout doesn't
  // shift under the pointer while output streams in).
  const fit = { key: "", running: false, queued: false };
  function requestFit() {
    if (fit.running && performance.now() - fit.started > 2000) fit.running = false; // a pass that died mid-way
    if (fit.running) { fit.queued = true; return; }
    requestAnimationFrame(() => fitTerm());
  }
  function fitDone(key) {
    fit.running = false;
    if (key) fit.key = key;
    if (fit.queued) { fit.queued = false; requestFit(); }
  }
  function fitTerm(depth = 0, st = null, key = null) {
    // Fill the space. 1) Font size: AUTO fits herdr's columns to the width, up to AUTO_MAX (herdr sizes the panes
    // to its own window, and with no herdr window open they can be tiny, e.g. 53x23 after a reboot); a SETTINGS
    // size is used as is (a pane too wide for it scrolls sideways). 2) Line spacing: binary search in [1, 1.5] for
    // the tallest rows that fit (row heights snap to device pixels, so proportional estimates oscillate; cropping
    // < half a row is allowed). The view is bottom-anchored (latest output, agent input box). Never a CSS scale:
    // xterm maps the mouse itself.
    if (!term) return;
    const el = $("term"), wrap = $("term-wrap"), screen = el.querySelector(".xterm-screen");
    const availW = wrap.clientWidth - 14, availH = wrap.clientHeight - 12;
    const fixed = state.settings.font_size === "auto" ? null : state.settings.font_size;
    if (depth === 0) {
      key = `${availW}x${availH}:${term.cols}x${term.rows}:${fixed || "auto"}`;
      if (key === fit.key || fit.running) return;
      fit.running = true; fit.started = performance.now();
      st = { phase: "font", fsCap: AUTO_MAX, lo: 1, hi: 1.5, fitFs: null };
    }
    if (depth > 40) return fitDone(key);
    const fs = term.options.fontSize, lh = term.options.lineHeight;
    const w = screen ? screen.offsetWidth : 0, h = screen ? screen.offsetHeight : 0;
    if (availW <= 0 || availH <= 0 || !w || !h) return fitDone(null); // not laid out yet: retry on the next request
    const again = () => requestAnimationFrame(() => fitTerm(depth + 1, st, key)); // xterm re-measures on the next frame
    if (st.phase === "font") {
      if (lh !== 1) { term.options.lineHeight = 1; return again(); } // measure widths at plain spacing
      const fitFs = Math.floor((fs * availW / w) * 20) / 20; // 0.05 px steps: fills to within a few px
      const wantFs = fixed || Math.max(6, Math.min(st.fsCap, fitFs));
      if (Math.abs(wantFs - fs) >= 0.05) { term.options.fontSize = wantFs; return again(); }
      if (!fixed && w > availW + 0.5 && fs - 0.05 >= 6) { st.fsCap = +(fs - 0.05).toFixed(2); term.options.fontSize = st.fsCap; return again(); }
      st.fitFs = fitFs; st.phase = "lh";
    }
    if (st.phase === "lh") {
      const fits = h <= availH + 0.45 * (h / term.rows);
      if (lh === 1 && !fits) st.phase = "done"; // even plain rows are taller than the space: crop the top
      else {
        if (fits) st.lo = Math.max(st.lo, lh); else st.hi = Math.min(st.hi, lh);
        if (st.hi - st.lo > 0.011) {
          const mid = Math.round(((st.lo + st.hi) / 2) * 100) / 100;
          if (mid !== lh) { term.options.lineHeight = mid; return again(); }
        }
        if (lh !== st.lo) { term.options.lineHeight = st.lo; return again(); } // settle on the tallest that fit
        st.phase = "done";
      }
    }
    wrap.classList.toggle("fixed-font", w > availW + 1); // sideways scroll only when a set size forces it
    state.autoFont = fs;
    state.fitFont = Math.max(6, st.fitFs || fs); // the size that exactly fills the width
    const note = $("term-note"); // say why there's space left over: the pane itself is small
    note.hidden = !(state.fitFont > AUTO_MAX + 0.5 && w < availW - 40);
    if (!note.hidden) note.textContent = `HERDR PANE IS ${term.cols}×${term.rows} · OPEN HERDR IN A LARGER WINDOW TO RESIZE IT`;
    el.style.marginTop = `${Math.round(availH - h)}px`; // bottom-anchored: shorter -> gap above; taller -> top cropped
    renderFontSetting();
    fitDone(key);
  }

  // ---- latency readout: input sent -> first changed frame carrying that input's seq, painted
  const lat = { seq: 0, sent: new Map(), samples: [] };
  window.__lat = lat.samples; // read by tests/latency_ui.py
  function sendInput(m) {
    m.seq = ++lat.seq;
    lat.sent.set(m.seq, performance.now());
    send(m);
  }
  function noteLatency(seq) {
    if (!seq || !lat.sent.has(seq)) return;
    const t0 = lat.sent.get(seq);
    for (const k of lat.sent.keys()) if (k <= seq) lat.sent.delete(k);
    requestAnimationFrame(() => {
      const ms = Math.round(performance.now() - t0);
      lat.samples.push(ms); if (lat.samples.length > 200) lat.samples.shift(); // tests only, not shown
    });
  }

  // ---- cursor: herdr's API has no cursor position, so infer it.
  // Row: the agent input row ("❯"/"›", plus continuation lines), else the last non-blank row (shell prompt).
  // Column: the last visible character on that row, plus spaces typed after it (a typed space is invisible
  // in the frame, and herdr's rows keep stale blanks after deletes, so they can't be read back), minus
  // arrow-key moves. Prompts ending in "% " / "$ " / "❯ " etc. set the left limit.
  const cur = { back: 0, trail: 0, expect: null, lastEnd: -1, lastY: -1 };
  const resetCur = () => { cur.back = 0; cur.trail = 0; cur.expect = null; };
  function trackCursorKeys(d) {
    const n = (re) => (d.match(re) || []).length;
    const mouse = d.match(/^\x1b\[<(\d+);(\d+);(\d+)([Mm])$/);
    if (mouse) { // a click in the app's input moves its cursor there (release ends a click or a drag)
      if (mouse[4] === "m" && (+mouse[1] & 3) === 0) moveCursorTo(+mouse[2] - 1, +mouse[3] - 1);
      return;
    }
    if (/\r|\x03|\x15|\x1b\[[AB]|\x1bO[AB]/.test(d) || d === "\x1b") { resetCur(); return; }
    if (/\x1b\[H|\x1bOH|\x01/.test(d)) { cur.back = 1e6; return; }
    if (/\x1b\[F|\x1bOF|\x05/.test(d)) { cur.back = 0; return; }
    const lr = n(/\x1b\[D|\x1bOD|\x02/g) - n(/\x1b\[C|\x1bOC|\x06/g);
    if (lr) { cur.back = Math.max(0, cur.back + lr); return; }
    if (cur.back !== 0) return; // mid-line edit: the echoed frame shows where things moved
    const at = performance.now();
    if (d === "\x7f" || d === "\b") { // backspace: over a typed space we know; over a letter, see the frame
      if (cur.trail > 0) cur.trail--; else cur.expect = { at, kind: "bs", from: cur.lastEnd };
      return;
    }
    if (d === "\x17") { cur.trail = 0; cur.expect = { at, kind: "word" }; return; } // ctrl-w keeps the space before
    if (d === "\t") { cur.trail = 0; return; } // completion: result unknown
    const text = d.replace(/\x1b\[20[01]~/g, ""); // strip bracketed-paste markers
    if (!text || text.startsWith("\x1b") || /[\x00-\x1f\x7f]/.test(text)) return; // other control keys
    const tail = text.length - text.replace(/ +$/, "").length;
    if (tail === text.length) cur.trail += tail; // only spaces: the visible end won't move, so move now
    else cur.expect = { at, kind: "text", tail }; // letters: settle when the echo arrives
  }
  function cursorLine() { // -> [row, end of visible text, left limit]
    const buf = term.buffer.active, rows = term.rows;
    const text = (y) => { const l = buf.getLine(y); return l ? l.translateToString(false) : ""; };
    const endCol = (y) => {
      const l = buf.getLine(y); if (!l) return 0;
      for (let x = term.cols - 1; x >= 0; x--) {
        const c = l.getCell(x), ch = c && c.getChars();
        if (ch && ch !== " ") return Math.min(term.cols - 1, x + Math.max(1, c.getWidth()));
      }
      return 0;
    };
    const isRule = (s) => { const t = s.trim(); if (!t) return false; let k = 0; for (const c of t) if ("─━═".includes(c)) k++; return k > 0.6 * [...t].length; };
    let y = -1, minCol = 0;
    for (let i = rows - 1; i >= Math.max(0, rows - 20); i--) {
      const m = text(i).match(/^(\s*)[❯›]/);
      if (m) {
        minCol = m[1].length + 2; y = i;
        for (let j = i + 1; j < rows && !isRule(text(j)); j++) if (text(j).trim()) y = j; // multi-line input
        if (y !== i) minCol = 0;
        break;
      }
    }
    if (y < 0) {
      for (let i = rows - 1; i >= 0; i--) if (text(i).trim()) { y = i; break; }
      if (y < 0) return [0, 0, 0];
      const p = text(y).match(/^.*?[%$#>❯›»] /); // shell prompt "… % ": typing starts after the space
      if (p) minCol = p[0].length;
    }
    return [y, Math.max(minCol, endCol(y)), minCol];
  }

  function onFrame(m) {
    const t = selectedTab();
    if (!t || t.pane_id !== m.pane_id) return;
    ensureTerm(m.cols || term?.cols || 120, m.rows || term?.rows || 40);
    // Agent panes: Claude Code runs full-screen and asks its terminal for SGR mouse reports (its mode table has
    // 1000/1002/1003/1006). herdr's frames don't carry modes, so put the local xterm in button-event mode
    // (?1002: press, drag motion, release; ?1006: SGR) and clicks/drag-highlights reach the app as in herdr.
    // Shells keep local selection (and click-to-move on the prompt line).
    const wantMouse = !!t.agent || FORCE_MOUSE;
    const modes = wantMouse === state.mouseMode ? "" : wantMouse ? "\x1b[?1002h\x1b[?1006h" : "\x1b[?1002l\x1b[?1006l";
    state.mouseMode = wantMouse;
    term.write(modes + "\x1b[H\x1b[2J" + m.text, () => { placeCursor(); noteLatency(m.seq); });
  }
  function placeCursor() {
    if (state.scrollOff > 0) { term.write("\x1b[?25l"); return; } // scrolled back: no live cursor on screen
    const [y, end, minCol] = cursorLine();
    const e = cur.expect;
    if (e && performance.now() - e.at > 1000) cur.expect = null; // the app ignored the key
    else if (e && (end !== cur.lastEnd || y !== cur.lastY)) {   // the echo arrived
      cur.expect = null;
      if (e.kind === "text") cur.trail = e.tail;
      else if (e.kind === "bs") cur.trail = Math.max(0, e.from - 1 - end); // deleted a letter after a space
      else if (e.kind === "word") cur.trail = end > minCol ? 1 : 0;
    }
    cur.lastEnd = end; cur.lastY = y;
    const lineEnd = Math.min(term.cols - 1, end + cur.trail);
    const x = Math.max(minCol, lineEnd - cur.back);
    cur.back = lineEnd - x; // clamp so Right arrows recover immediately
    term.write(`\x1b[${y + 1};${x + 1}H\x1b[?25h`);
    window.__cursor = [y, x];
  }
  function moveCursorTo(x, y) { // only on the row we track; elsewhere the next frames decide
    const [cy, end, minCol] = cursorLine();
    if (y !== cy) return;
    const lineEnd = Math.min(term.cols - 1, end + cur.trail);
    cur.back = Math.max(0, lineEnd - Math.max(minCol, Math.min(lineEnd, x)));
    placeCursor();
  }
  // Shell prompt line: a plain click (no drag-selection) moves the cursor with arrow keys, like click-to-move
  // terminals. Agent panes get real mouse reports instead (see onFrame).
  function cellAt(e) {
    const scr = $("term").querySelector(".xterm-screen").getBoundingClientRect();
    return [Math.floor((e.clientX - scr.left) / (scr.width / term.cols)), Math.floor((e.clientY - scr.top) / (scr.height / term.rows))];
  }
  function onMirrorClick(e) {
    const t = selectedTab();
    if (!t || !term || state.mouseMode || e.button !== 0 || e.altKey || term.hasSelection() || state.scrollOff > 0) return;
    const [x, y] = cellAt(e);
    const [cy, end, minCol] = cursorLine();
    if (y !== cy) return;
    const lineEnd = Math.min(term.cols - 1, end + cur.trail), curX = lineEnd - cur.back;
    const target = Math.max(minCol, Math.min(lineEnd, x));
    const delta = target - curX;
    if (!delta) return;
    const seq = (delta < 0 ? "\x1b[D" : "\x1b[C").repeat(Math.abs(delta));
    trackCursorKeys(seq);
    sendInput({ type: "input", pane_id: t.pane_id, data: seq });
    placeCursor();
  }
  function setKeys(on) { // focus shows as the gold left edge of the terminal
    state.keysOn = on;
    $("term-wrap").classList.toggle("keys-on", on);
  }

  // ---- mouse wheel: herdr scrolls a shell's scrollback (pane.scroll); full-screen apps (Claude Code)
  // get mouse-wheel reports at the cell under the pointer. The server picks which.
  const wheelAcc = { dy: 0, pending: false, col: 1, row: 1 };
  function onWheel(e) {
    const t = selectedTab(); if (!t || !term) return;
    if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) return; // sideways: let a fixed-size terminal scroll horizontally
    e.preventDefault(); e.stopPropagation();
    const scr = $("term").querySelector(".xterm-screen").getBoundingClientRect(); // includes the CSS scale
    wheelAcc.col = Math.min(term.cols, Math.max(1, Math.floor((e.clientX - scr.left) / (scr.width / term.cols)) + 1));
    wheelAcc.row = Math.min(term.rows, Math.max(1, Math.floor((e.clientY - scr.top) / (scr.height / term.rows)) + 1));
    wheelAcc.dy += e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY;
    if (wheelAcc.pending) return;
    wheelAcc.pending = true;
    requestAnimationFrame(() => { // at most one message per frame; ~25 px of wheel = one step
      wheelAcc.pending = false;
      const steps = Math.trunc(wheelAcc.dy / 25);
      if (!steps) return;
      wheelAcc.dy -= steps * 25;
      send({ type: "wheel", pane_id: t.pane_id, dir: steps < 0 ? "up" : "down", count: Math.min(Math.abs(steps), 10),
             col: wheelAcc.col, row: wheelAcc.row });
    });
  }

  // ------------------------------------------------------------------ buttons
  $("btn-unselect").addEventListener("click", () => select(null));
  $("btn-focus").addEventListener("click", () => { const t = selectedTab(); if (t) send({ type: "focus_tab", tab_id: t.tab_id }); });
  $("btn-close").addEventListener("click", () => {
    const t = selectedTab(); if (!t) return;
    confirmModal("CLOSE TAB", `Close the shell tab ${t.label} (${t.tab_id})? Anything running in it will end.`,
      () => send({ type: "close_tab", tab_id: t.tab_id }));
  });

  function openModal({ title, text, color, yes, value, onYes }) {
    $("modal-title").textContent = title; $("modal-text").textContent = text;
    for (const id of ["modal-box", "modal-bar", "modal-yes"]) $(id).className = $(id).className.replace(/\bc-\w+/g, "").trim() + ` ${color}`;
    $("modal-yes").textContent = yes;
    const input = $("modal-input"), asking = value !== undefined;
    input.hidden = !asking; input.value = asking ? value : "";
    $("modal").hidden = false;
    state.modalOpen = true;
    const done = (ok) => {
      $("modal").hidden = true; state.modalOpen = false;
      $("modal-yes").onclick = $("modal-no").onclick = input.onkeydown = null;
      if (ok) onYes(asking ? input.value.trim() : undefined);
      if (selectedTab() && term) term.focus(); // back to typing in the pane
    };
    $("modal-yes").onclick = () => done(true); $("modal-no").onclick = () => done(false);
    input.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); done(true); } else if (e.key === "Escape") done(false); };
    setTimeout(() => (asking ? (input.focus(), input.select()) : $("modal-no").focus()), 0);
  }
  const confirmModal = (title, text, onYes) => openModal({ title, text, color: "c-red", yes: "CONFIRM", onYes });
  const promptModal = (title, text, value, onYes) => openModal({ title, text, color: "c-ice", yes: "SAVE", value, onYes });
  function toast(text, color = "c-ice") {
    if (color === "c-red") sound("error");
    const el = document.createElement("div");
    el.className = `toast ${color}`; el.textContent = text;
    $("toasts").appendChild(el); setTimeout(() => el.remove(), 4000);
  }

  // ------------------------------------------------------------------ push-to-talk
  const vox = { ctx: null, stream: null, node: null, chunks: [], rec: false, idleTimer: null, busy: false, ready: false };

  function renderVoice(v) {
    vox.ready = v && v.state === "ready";
    if (vox.rec || vox.busy) return;
    $("vox-light").className = `vox-light ${v?.state === "ready" ? "ready" : v?.state === "error" ? "error" : ""}`;
    $("vox-state").textContent = `VOICE: ${v ? v.state : "standby"}`;
    if (v && v.state !== "ready" && !vox.lastText) $("vox-text").textContent = v.detail;
  }

  async function micOpen() {
    if (vox.stream) return;
    vox.ctx = new AudioContext({ sampleRate: 16000 }); // Chrome resamples the mic to 16 kHz for us
    await vox.ctx.audioWorklet.addModule("/static/pcm-worklet.js");
    vox.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: false, noiseSuppression: true, autoGainControl: true },
    });
    const src = vox.ctx.createMediaStreamSource(vox.stream);
    vox.node = new AudioWorkletNode(vox.ctx, "pcm-capture");
    vox.node.port.onmessage = (e) => { if (vox.rec) vox.chunks.push(e.data); };
    src.connect(vox.node);
  }
  function micCloseLater() {
    clearTimeout(vox.idleTimer);
    vox.idleTimer = setTimeout(() => { // release the mic (and macOS's orange dot) after 30 s idle
      vox.stream?.getTracks().forEach((t) => t.stop()); vox.ctx?.close();
      vox.stream = vox.ctx = vox.node = null;
    }, 30000);
  }
  async function pttStart() {
    if (vox.rec || vox.busy) return;
    const t = selectedTab();
    if (!t) { toast("SELECT A PANEL FIRST — VOICE NEEDS A TARGET PANE", "c-gold"); return; }
    if (!vox.ready) { toast("VOICE MODEL NOT READY", "c-gold"); return; }
    vox.rec = true; vox.chunks = []; vox.target = t;
    clearTimeout(vox.idleTimer);
    $("btn-talk").classList.add("on");
    $("vox-light").className = "vox-light rec"; $("vox-state").textContent = "VOICE: LISTENING";
    try { await micOpen(); sound("talk-start"); }
    catch (e) { vox.rec = false; $("btn-talk").classList.remove("on"); toast(`MIC: ${e.message}`, "c-red"); renderVoice(state.board?.voice); }
  }

  async function pttStop(cancel = false) {
    if (!vox.rec) return;
    vox.rec = false;
    $("btn-talk").classList.remove("on");
    micCloseLater();
    if (cancel) { renderVoice(state.board?.voice); return; }
    sound("talk-stop");
    const n = vox.chunks.reduce((a, c) => a + c.length, 0);
    const pcm = new Float32Array(n); let o = 0;
    for (const c of vox.chunks) { pcm.set(c, o); o += c.length; }
    vox.chunks = [];
    if (n < 16000 * 0.3) { renderVoice(state.board?.voice); return; }
    vox.busy = true;
    $("vox-light").className = "vox-light busy"; $("vox-state").textContent = "VOICE: TRANSCRIBING";
    try {
      const r = await fetch(`/api/transcribe?pane_id=${encodeURIComponent(vox.target.pane_id)}&send=1`, {
        method: "POST", headers: { "X-LCARS-Token": TOKEN, "Content-Type": "application/octet-stream" }, body: pcm.buffer,
      });
      const j = await r.json();
      if (j.error) throw new Error(j.error);
      vox.lastText = j.text;
      $("vox-text").textContent = j.text ? `“${j.text}”  →  ${vox.target.label}  (${j.seconds}s audio, ${j.ms} ms)` : "(no speech heard)";
    } catch (e) { toast(`VOICE: ${e.message}`, "c-red"); }
    finally { vox.busy = false; renderVoice(state.board?.voice); }
  }

  // Hold the talk key (SETTINGS: Right Option or Right Command). Any other key while it's held cancels:
  // that was a shortcut (Option-combo, Cmd-C...), not push-to-talk.
  window.addEventListener("keydown", (e) => {
    if (state.modalOpen) return;
    if (e.code === state.settings.ptt_key) { if (!e.repeat) pttStart(); e.preventDefault(); }
    else if (vox.rec) pttStop(true);
  }, true);
  window.addEventListener("keyup", (e) => { if (e.code === state.settings.ptt_key) { e.preventDefault(); pttStop(); } }, true);
  window.addEventListener("blur", () => pttStop(true));
  const talk = $("btn-talk");
  talk.addEventListener("pointerdown", (e) => { e.preventDefault(); talk.setPointerCapture(e.pointerId); pttStart(); });
  talk.addEventListener("pointerup", () => pttStop());
  talk.addEventListener("pointercancel", () => pttStop(true));

  // ------------------------------------------------------------------ sounds
  // Built-in LCARS-style tones are synthesized here (no recorded assets). A file in sounds/ named after the
  // event (done.wav, click.mp3, ...) replaces the built-in tone; see sounds/README.md.
  const CUSTOM = window.LCARS_SOUNDS || {};
  const TONES = { // [frequency Hz, ms, gap ms] steps; triangle waves, soft attack. Fixed, never random.
    "click-menu": () => [[1319, 45, 0]],
    "click-panel": () => [[1568, 70, 0]],
    "click-button": () => [[988, 40, 0]],
    done: () => [[988, 80, 25], [1480, 120, 0]],
    blocked: () => [[1568, 70, 50], [1175, 70, 50], [1568, 70, 50], [1175, 90, 0]],
    "talk-start": () => [[880, 50, 10], [1319, 70, 0]],
    "talk-stop": () => [[1319, 50, 10], [660, 80, 0]],
    error: () => [[440, 110, 20], [330, 160, 0]],
  };
  const KIND = { "click-menu": "click_sounds", "click-panel": "click_sounds", "click-button": "click_sounds",
                 done: "alert_sounds", blocked: "alert_sounds" };
  let actx = null, lastPlay = {};
  window.__sounds = []; // tests: what played
  function audio() {
    if (!actx) { try { actx = new AudioContext(); } catch (_) { return null; } }
    if (actx.state === "suspended") actx.resume().catch(() => {});
    return actx;
  }
  function sound(name, force = false) {
    const st = state.settings;
    if (!force && (st.muted || (KIND[name] && !st[KIND[name]]))) return;
    const now = performance.now();
    if (!force && !name.startsWith("click") && now - (lastPlay[name] || 0) < 1200) return; // several agents at once: one chirp
    lastPlay[name] = now;
    const vol = st.volume / 100;
    window.__sounds.push(name);
    const files = CUSTOM[name] || (name.startsWith("click-") && CUSTOM.click) || [];
    if (files.length) {
      const url = files[0]; // deterministic: the same control always makes the same sound
      const c = audio(), buf = decoded[url];
      if (c && buf) { // preloaded: plays with no lag
        const src = c.createBufferSource(), g = c.createGain();
        src.buffer = buf; g.gain.value = vol; src.connect(g).connect(c.destination); src.start();
      } else { const a = new Audio(url); a.volume = vol; a.play().catch(() => {}); }
      return;
    }
    const c = audio(); if (!c) return;
    let t = c.currentTime + 0.01;
    for (const [f, ms, gap] of TONES[name]()) {
      const o = c.createOscillator(), g = c.createGain();
      o.type = "triangle"; o.frequency.setValueAtTime(f, t);
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(Math.max(0.0002, 0.22 * vol), t + 0.006);
      g.gain.exponentialRampToValueAtTime(0.0001, t + ms / 1000);
      o.connect(g).connect(c.destination); o.start(t); o.stop(t + ms / 1000 + 0.02);
      t += (ms + gap) / 1000;
    }
  }
  // decode custom sound files up front so a click plays instantly
  const decoded = {}; window.__decoded = decoded; // tests: which files are ready to play instantly
  (async () => {
    const c = audio(); if (!c) return;
    for (const url of [...new Set(Object.values(CUSTOM).flat())]) {
      try { decoded[url] = await c.decodeAudioData(await (await fetch(url)).arrayBuffer()); } catch (_) { /* falls back to <audio> */ }
    }
  })();
  // panel beeps on LCARS controls, one fixed sound per kind (not the terminal; TALK has its own chirps):
  // sidebar menu blocks -> click-menu, agent panels -> click-panel, every other button -> click-button
  document.addEventListener("pointerdown", (e) => {
    audio(); // first gesture unlocks audio for later alerts
    const el = e.target.closest("button, .panel, .ws-head .name");
    if (!el || el.id === "btn-talk" || el.disabled || e.target.closest("#term-wrap")) return;
    sound(el.closest(".panel") ? "click-panel" : el.closest(".sidebar") ? "click-menu" : "click-button");
  }, true);

  // ------------------------------------------------------------------ settings
  // Saves go out one at a time (changes made meanwhile are merged into the next one), so quick clicks land in
  // order; while saving, this window's local state wins over the server's echoes.
  const saver = { busy: false, pending: null };
  function saveSettings(patch) {
    state.settings = { ...state.settings, ...patch };
    applySettings();
    saver.pending = { ...(saver.pending || {}), ...patch };
    flushSettings();
  }
  async function flushSettings() {
    if (saver.busy || !saver.pending) return;
    const patch = saver.pending; saver.pending = null; saver.busy = true;
    try {
      const r = await fetch("/api/settings", { method: "POST", headers: { "X-LCARS-Token": TOKEN, "Content-Type": "application/json" },
                                               body: JSON.stringify(patch) });
      if (!r.ok) throw new Error(r.status);
    } catch (_) { toast("SETTINGS: COULD NOT SAVE", "c-red"); }
    saver.busy = false;
    flushSettings();
  }
  function renderFontSetting() {
    const fs = state.settings.font_size;
    const now = state.autoFont ? ` · ${(+state.autoFont).toFixed(1)}` : "";
    $("set-font-val").textContent = fs === "auto" ? `AUTO${now}` : `${fs}`;
    $("set-font-auto").classList.toggle("seg-btn", fs !== "auto");
    $("set-font-hint").textContent = fs === "auto"
      ? `Fits the pane's width, up to ${AUTO_MAX}. − / + set a fixed size.`
      : `Fixed at ${fs}. AUTO fits the width again; a pane too wide for ${fs} scrolls sideways.`;
  }
  function applySettings() {
    const st = state.settings;
    $("main").classList.toggle("board-hidden", !st.board_beside);
    document.querySelectorAll("#settings [data-set]").forEach((g) =>
      g.querySelectorAll("[data-val]").forEach((b) => b.classList.toggle("on", String(st[g.dataset.set]) === b.dataset.val)));
    $("set-mute").classList.toggle("on", st.muted);
    $("set-mute").textContent = st.muted ? "MUTED" : "MUTE";
    $("set-volume").value = st.volume; $("set-volume-val").textContent = st.volume;
    $("set-sound-hint").textContent = Object.keys(CUSTOM).length
      ? `Sound files from sounds/: ${Object.keys(CUSTOM).join(", ")}.`
        + (Object.keys(CUSTOM).length < Object.keys(TONES).length ? " Built-in tones for the rest." : "")
      : "Built-in tones. Drop files in sounds/ to replace them (see sounds/README.md).";
    $("vox-text").textContent = vox.lastText ? $("vox-text").textContent
      : `Hold ${PTT_LABEL[st.ptt_key]} (or the TALK block) and speak. Text lands in the selected pane without Enter.`;
    renderFontSetting();
    requestFit();
  }
  function openSettings() {
    state.modalOpen = true; $("settings").hidden = false; applySettings(); $("set-done").focus();
  }
  function closeSettings() {
    state.modalOpen = false; $("settings").hidden = true;
    if (selectedTab() && term) term.focus();
  }
  $("btn-settings").addEventListener("click", openSettings);
  $("set-done").addEventListener("click", closeSettings);
  $("settings").addEventListener("keydown", (e) => { if (e.key === "Escape") closeSettings(); });
  $("settings").addEventListener("click", (e) => { if (e.target.id === "settings") closeSettings(); });
  document.querySelectorAll("#settings [data-set]").forEach((g) => g.addEventListener("click", (e) => {
    const b = e.target.closest("[data-val]"); if (!b) return;
    const v = b.dataset.val === "true" ? true : b.dataset.val === "false" ? false : b.dataset.val;
    saveSettings({ [g.dataset.set]: v });
  }));
  const stepFont = (d) => { // steps the size you see by one point, to a fixed size (7-24); AUTO goes back to fitting
    const fs = state.settings.font_size; // from settings, so rapid clicks don't use a stale size
    const shown = fs === "auto" ? (state.autoFont || 12) : fs;
    saveSettings({ font_size: d > 0 ? Math.min(24, Math.floor(shown) + 1) : Math.max(7, Math.ceil(shown) - 1) });
  };
  $("set-font-up").addEventListener("click", () => stepFont(1));
  $("set-font-dn").addEventListener("click", () => stepFont(-1));
  $("set-font-auto").addEventListener("click", () => saveSettings({ font_size: "auto" }));
  $("set-mute").addEventListener("click", () => saveSettings({ muted: !state.settings.muted }));
  $("set-volume").addEventListener("input", (e) => { $("set-volume-val").textContent = e.target.value; });
  $("set-volume").addEventListener("change", (e) => { saveSettings({ volume: +e.target.value }); sound("done", true); });
  $("set-test").addEventListener("click", () => sound("done", true));

  // ------------------------------------------------------------------ install as an app (Dock / Cmd-Tab)
  // Chrome offers install when the manifest is valid; installed, LCARS gets its own app (icon, Dock, Cmd-Tab).
  let installPrompt = null;
  window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); installPrompt = e; $("btn-install").hidden = false; });
  window.addEventListener("appinstalled", () => { installPrompt = null; $("btn-install").hidden = true; toast("LCARS INSTALLED AS AN APP", "c-ice"); });
  $("btn-install").addEventListener("click", async () => {
    if (!installPrompt) return;
    installPrompt.prompt();
    const { outcome } = await installPrompt.userChoice;
    if (outcome === "accepted") { installPrompt = null; $("btn-install").hidden = true; }
  });

  // ------------------------------------------------------------------ misc
  setInterval(() => { $("clock").textContent = new Date().toLocaleTimeString([], { hour12: false }); }, 1000);
  const m = location.hash.match(/pane=([^&]+)/);
  if (m) {
    const want = decodeURIComponent(m[1]);
    const iv = setInterval(() => {
      const t = state.board?.tabs.find((x) => x.pane_id === want);
      if (t) { clearInterval(iv); select(t.tab_id); }
    }, 100);
    setTimeout(() => clearInterval(iv), 5000);
  }
  applySettings();
  connect();
})();
