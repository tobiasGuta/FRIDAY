# FRIDAY v0.5.6 — accepted Focus Voice baseline

Status: **Windows user acceptance reported on 2026-09-25; release PRs remain draft until review/merge authorization.**

This is a preservation record, not a claim that a long-duration reliability soak
or every hardware/device combination has been tested.

## Exact tested revisions

| Milestone | Exact accepted head | Preserved remote branch |
| --- | --- | --- |
| v0.5.5 hybrid shell + cinematic voice | `1572f66e2f85dee56523b00b7b52828a01f1e00b` | `stable/v0.5.5-hybrid-accepted-20260925` |
| v0.5.6 Focus Voice | `b0eca4f13b57b634f08cb89d3e1b4f8a659a197d` | `stable/v0.5.6-focus-accepted-20260925` |

The stable branches are named checkpoints **at these exact source commits**;
branches are movable Git refs, not immutable release tags. Avoid force-pushing
or deleting them. The v0.5.6 checkpoint includes v0.5.5 because PR #10 is
stacked on PR #9.

The accepted v0.5.6 head passed GitHub's offline Windows and Ubuntu matrix
(tests, Python compilation and Ruff):
<https://github.com/tobiasGuta/FRIDAY/actions/runs/36182025708>.

## What the user actually confirmed

- The minimal near-black voice view and amber Qt-painted orb displayed correctly
  on Windows; the dashboard was not shown during an ordinary conversation.
- The read-only Brightspace context drawer displayed real cached course events
  (with their original source-labeled due distinction).
- FRIDAY completed a multi-turn voice conversation including local clock and
  weather tool responses in the acceptance session, and the user reported that
  the new experience worked.

Keep these facts separate from the offline CI results. The earlier intermittent
30-second Gemini Live turn stall was not conclusively root-caused; its
non-sensitive diagnostics and manual recovery remain relevant if it recurs.

## Behavior that must remain unchanged in visual experiments

1. On app launch the Gemini session is disconnected and the microphone is off.
   Neither opening Focus view nor changing panels starts a paid session.
2. One manual Connect/Disconnect control and one canonical Start talking /
   Stop recording path; no wake word, auto-recording or auto-reconnect.
3. Real session states drive the orb. Decorative ring animation is not a
   claimed measured audio waveform. Never route PCM through the Qt paint loop.
4. Academic is sourced only from the local Brightspace snapshot. A
   source-labeled `VEVENT` due entry is **not** an independently verified
   `VTODO DUE` deadline; an empty feed does not prove no coursework exists.
5. Reminders remain local/approval-gated. Existing Confirm/Cancel controls
   are canonical; merely showing a card never commits a reminder.
6. Calendar context reports actual scheduler/optional Google sync state; it
   does not invent a Google event list or publish Brightspace entries.
7. Untrusted text is rendered as plain text. No raw audio, credentials, feed
   URL or persistent conversation memory should be written by the UI.
8. Minimize/tray/explicit quit preserve the independent owned scheduler
   lifecycle and safe voice/audio shutdown.
9. A context drawer is display-only and cannot expand model tool permissions,
   execute arbitrary actions, or cause background API calls.

The existing `tests/test_desktop_gui.py`, `tests/test_focus_context.py`,
`tests/test_desktop_session.py`, Gemini mapping/adapter tests and scheduler /
Brightspace regressions form the offline acceptance suite. On Windows, rerun
live voice, manual reconnect, prompt/approval and the actual Brightspace
drawer after any rendering change.

## Safe restore and experiment workflow

Before switching branches, **fully quit FRIDAY from its system tray**. From
`D:\Tools\FRIDAY`, check `git status`. Do not overwrite local uncommitted work.

To inspect or run the preserved source without modifying main:

```powershell
cd D:\Tools\FRIDAY
git fetch origin
git switch --detach b0eca4f13b57b634f08cb89d3e1b4f8a659a197d
.\.venv\Scripts\Activate.ps1
python -m friday desktop --input-device 1
```

Detach is intentional: it avoids accidentally committing or advancing the
checkpoint. This preserves source code only; it does **not** restore local
`.env`, credential-vault entries, or SQLite state. Do not put those secrets
or private calendar-feed URLs in Git.

For a new hologram experiment, create a **new branch** from the accepted
checkpoint, not from an unreviewed moving branch:

```powershell
git switch -c feat/hologram-experiment b0eca4f13b57b634f08cb89d3e1b4f8a659a197d
```

Run `python -m pytest -q`, `python -m compileall -q src tests`, and
`python -m ruff check .` before asking for Windows acceptance. Keep any
shader/QML experiment optional, off by default, and easy to revert.

## Promotion order

- PR #9 (`feat/v0.5.5-hybrid-shell`) must be reviewed and merged into
  `main` first.
- Retarget PR #10 (`feat/v0.5.6-focus-voice`) onto `main` after PR #9
  merges, re-check its diff and latest CI, then review and merge it.
- The exact accepted source checkpoint remains available even if release
  documentation is edited afterward. Create an annotated `v0.5.6` tag on
  the final accepted main commit only after those promotions and checks;
  this document does not claim such a tag or GitHub Release already exists.
