# Custom sounds

Drop audio files here to replace the console's built-in (synthesized) tones. Name each file after its event:

| File | Plays when |
|---|---|
| `click-menu.wav` | you click a sidebar block (ALL, workspaces, NEW SHELL, SETTINGS…) |
| `click-panel.wav` | you open an agent panel on the board |
| `click-button.wav` | you click any other button (BOARD, RENAME, settings controls…) |
| `click.wav` | fallback for any click sound above that has no file |
| `done.wav` | an agent finishes (working → done / idle) |
| `blocked.wav` | an agent gets blocked (waiting on an approval or a question) |
| `talk-start.wav` / `talk-stop.wav` | push-to-talk starts / stops |
| `error.wav` | an error toast appears |

`.wav`, `.mp3`, `.ogg` and `.m4a` work. If an event has several files, the first (alphabetically) plays, so a control always makes the same sound. Reload the window (Cmd-R) after adding or removing files. Volume, mute and the
per-category switches in SETTINGS apply to these too. Audio files in this folder are git-ignored.
