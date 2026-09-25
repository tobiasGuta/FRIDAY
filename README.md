# FRIDAY

A harness-first personal AI assistant. **v0.5.5 is a draft hybrid desktop shell: Home, Voice, Academic, Reminders, Calendar and Settings preserve the working v0.5.4 capabilities.**

No distribution license has been selected yet. Repository visibility is not a grant of reuse rights.

FRIDAY owns the application lifecycle, event types, provider interface and configuration. Gemini Live is an optional provider; a deterministic fake provider enables offline tests. The eventual local voice provider can implement the same contract without leaking SDK-specific types into the core.

## What works today (v0.5.5 draft)

- `friday doctor`: safe configuration diagnostics (never prints your API key).
- `friday demo`: simulated conversation with a fake provider; no network or key needed.
- `friday live`: Gemini Live text-triggered diagnostic with optional WAV output.
- `friday talk`: opt-in live 16 kHz microphone input and 24 kHz speaker output with Enter-to-talk/Enter-to-stop, transcripts, and interruption playback flush.
- `friday devices`: list available PortAudio microphone and speaker device indices.
- `friday clock`: read your computer's local date, time, and configured timezone entirely offline.
- `friday tools`: list enabled application tools and policies without an API key.
- `friday talk --web`: opt in to the typed `search_web` function, with Tavily Basic Search as the default backend; bounded source excerpts and HTTPS links appear in the terminal. Optional Gemini Search grounding remains available by explicit configuration.
- `friday web-search --query "..."`: make one explicit search without starting the microphone or Gemini Live session.
- `friday weather --location "Brooklyn, New York" --day today`: check weather without opening the microphone or using Gemini/Tavily credits.
- `friday talk`: a read-only `get_weather` function provides current conditions and today/tomorrow forecasts for a location explicitly named by the user, with Open-Meteo attribution.
- `friday talk` and `friday live`: Gemini may call the narrowly allowlisted `get_local_time` function instead of guessing the current date or time.
- Session state changes, idempotent shutdown, bounded event queue, basic session metrics, and automated tests.

**Not yet implemented:** recurring reminders, wake word, always-on listening, persistent memory, arbitrary computer actions, local inference, session resumption, or hardware-independent echo cancellation. The currently available model-callable functions are the read-only clock, location-explicit weather, and opt-in web search. The `live` diagnostic still writes WAV only; use `talk` to hear FRIDAY automatically.

## Requirements

Python 3.11+; the offline suite is verified here on Python 3.13. Windows audio hardware is validated separately on your machine. `uv` is convenient but ordinary `pip` also works.

```bash
# from the project directory
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# Linux/macOS:
# source .venv/bin/activate
python -m pip install -e '.[gemini,voice,web,dev]'
python -m friday doctor
python -m friday clock
python -m friday demo --once "Hello FRIDAY"
python -m pytest
```

On PowerShell, `'.[gemini,voice,web,dev]'` works as written. With `uv`, install the gemini, voice, and dev extras.

## FRIDAY desktop interface (v0.5.5 draft)

The optional PySide6 shell provides click-to-talk, an animated voice orb, plain-text
transcripts, an upcoming-reminders list and app-owned **Confirm / Cancel** controls.
The existing approval gate still requires a distinct human approval; merely seeing
or hearing a reminder draft never saves it. Web search is optional.

Install dependencies in the project virtual environment:

```powershell
cd D:\Tools\FRIDAY
.\.venv\Scripts\Activate.ps1
python -m pip install -e '.[gemini,voice,web,schedule,calendar,desktop,brightspace,dev]'
python -m friday desktop --input-device 1
```

The window starts **disconnected**: no microphone capture or paid Gemini session
until you click **Connect**. Use **Start talking**, speak, then **Stop recording**;
wait for **Ready** before the next turn. Reminder drafts are on by default and web
search is off unless you opt in. You can use `--no-reminders`, `--web`, or
`--input-language auto`; the original `talk` CLI remains available.

### Hybrid desktop shell (v0.5.5 Slices 1–2; Windows Slice 2 acceptance pending)

FRIDAY now has a six-page PySide6 Widgets shell: **Home**, **Voice**,
**Academic**, **Reminders**, **Calendar** and **Settings**. Navigation does not
start a voice session, a database worker or a network request. The top bar
mirrors the actual session/scheduler/academic state and computer-local time.

- **Home**: responsive wide/tall cards, a state-mirrored decorative orb, up to
  three cached Brightspace items with explicit/source-labeled/scheduled types,
  up to three future pending reminders via a **read-only SQLite connection**,
  and true scheduler status. The conversation preview is **current-session
  only** and does not persist a chat history. At narrower window widths the
  cards stack and the detailed top chips hide (the full states remain on the
  dedicated pages). The Home Sync now shortcut uses the existing Academic
  action and navigates to its status; it never starts Gemini or bypasses
  credential checks.
- **Voice**: the existing animated orb, one canonical microphone button,
  plain-text transcript, reminder draft Confirm/Cancel controls, and existing
  opt-in reminder/web settings. A new approval draft opens this page so that
  the real host-owned controls remain accessible.
- **Academic**: the original protected feed entry, Save, Sync now, Remove,
  optional 30-minute desktop-scheduler refresh, next-connection read-only
  voice opt-in, and due-label distinctions.
- **Reminders**: existing pending-reminder list. Actual approval remains
  on Voice; the UI does not invent batch-approval or direct schedule actions.
- **Calendar**: original owned-worker controls and optional Google sync.
  The new UI does not claim to read unrelated Google calendar events.
- **Settings**: navigation to the existing functional controls, plus an
  honest appearance placeholder. No new persistent preferences are written.

The UI redraw is intentionally within the existing Qt Widgets framework;
no new permission, provider integration, paid session behavior, or credential
storage mechanism is introduced. The dedicated cinematic Voice polish and source-supported academic course
  grouping remain subsequent slices. The mockups include
illustrative courses/events and controls that must **not** be interpreted as
implemented data or actions.

### Brightspace calendar (introduced in v0.5.4; Windows feed accepted)

The optional `brightspace` extra provides iCalendar parsing, HTTPS retrieval
and protected credential storage. This is **not** an official Brightspace OAuth
app and FRIDAY never asks for your CUNY sign-in credentials. Your account must
already expose the official **All Calendars and Tasks** subscription in the
Brightspace Calendar tool.

1. In Brightspace, enable Calendar Feeds and open **Subscribe**. Select
   **All Calendars and Tasks**. Keep its private subscription URL secret.
2. Quit FRIDAY completely from the system tray, install the extras shown above,
   and relaunch the desktop shortcut.
3. Open **Academic** from the v0.5.5 sidebar (the older v0.5.4 interface
   used a Brightspace tab under Conversation). Paste the URL
   **only into FRIDAY's masked field**, then click **Save feed**. The URL is
   written to the OS credential vault, not to the repository or a plaintext
   configuration. If protected storage is unavailable, saving fails closed.
4. Click **Sync now**. The read-only HTTP worker retrieves and parses the feed,
   shows the next seven days of published calendar items, and identifies the
   last successful refresh. No Gemini connection is required to sync.
5. To allow FRIDAY to answer calendar questions, check **Enable read-only
   academic voice lookup** before clicking Connect. This permission applies to
   the next Live session; it does not expose the subscription address.
6. Optional: check **Refresh every 30 min while scheduler runs** after a
   successful manual sync. It refreshes only while FRIDAY's owned desktop
   scheduler is active (including when the window is hidden). No periodic sync
   starts automatically when you open the app.
7. **Remove feed** asks for confirmation, then deletes the stored credential
   and local academic snapshot. No assignment submission or Google Calendar
   event is created by this integration.

The implementation only accepts an HTTPS feed hosted at
`brightspace.cuny.edu`. It will not follow redirects to another hostname;
if your official private feed uses a different domain, do **not** work around
this by disabling URL checks. Report only the hostname (not the private URL)
so we can evaluate a justified allowlist change.

**Calendar coverage limitation:** VEVENT start times are scheduled
events, not independently verified submission due dates. A VEVENT whose
source title ends in ` - Due` is displayed as **Brightspace-labeled due
(event)**, distinct from an explicit VTODO DUE deadline. This label reflects
Brightspace's title, not an independent submission-time verification.
All-day dates remain dates, not UTC midnight. Recurrence rules are flagged,
not expanded; the list may omit individual later instances.
Brightspace's feed can omit activities that lack published calendar dates.
FRIDAY must never equate an empty agenda with "no assignments." On sync
failure the previously validated cache remains, but its last-success timestamp
must be considered when answering questions. This is **not** grades or a
complete assignment API. No private URL or raw calendar file should ever be
shared in an issue, chat, or test fixture.

### Create a desktop shortcut (Windows)

Run this once from PowerShell in the project directory, after installing the
desktop dependencies:

```powershell
.\scripts\install-desktop-shortcut.ps1 -InputDevice 1
```

This creates **FRIDAY.lnk** on your Windows desktop, pointing at the current
`.venv\Scripts\pythonw.exe` with `-m friday desktop --input-device 1`.
Opening it does not spawn a visible PowerShell window. The shortcut is tied to
this repository and virtual environment; reinstall it if you move the project.
If it fails silently, run `python -m friday desktop --input-device 1` in
PowerShell to see the diagnostic. Remove the shortcut to uninstall it.
There is no auto-start on Windows login and no bundled executable installer.

### Window and tray lifecycle

When the OS has a system tray, closing FRIDAY's window **hides** it without
disconnecting an active Gemini session. Use the FRIDAY tray menu to **Open FRIDAY**,
**Hide FRIDAY**, or **Quit FRIDAY**. Explicit Quit requests a clean session shutdown
before the app exits. If no system tray is available, closing the window retains
the previous graceful disconnect/exit behavior. Disconnect still closes the
voice session while leaving the application window available. There is no
always-on microphone or wake word.

### Voice reliability and manual recovery (v0.5.3)

If Gemini Live disconnects unexpectedly, the audio input stops, or the microphone
stream becomes inactive mid-turn, FRIDAY closes the affected voice session and
displays a recoverable state: **Connection lost**, **Audio unavailable**, or
**Session expired**. The button becomes **Reconnect** only after the old worker
has finished. You decide when to reconnect; FRIDAY never silently makes another
potentially paid Gemini connection. The separate desktop scheduler remains active.

After reconnecting, this is a **new Live session**, not a resumed conversation.
FRIDAY does not replay microphone data, recover a partial spoken answer, or
carry an unapproved reminder draft into the new connection. Confirmed SQLite
reminders are unaffected. If Windows changes the microphone device index after
unplug/replug, use `python -m friday devices` and relaunch with the correct
`--input-device` selection (or leave the index unspecified for the system default).

Sessions remain bounded by `FRIDAY_MAX_SESSION_SECONDS` (default 300 seconds,
maximum 3600) or the `--max-seconds` desktop override. FRIDAY warns shortly
before expiry; no new turn starts after the limit, and an already in-progress
spoken reply gets at most 30 seconds of additional completion time before
the connection closes. A recording still active at the limit is stopped rather
than sent or approved automatically. Reconnect manually to continue.

This is recovery and cleanup, **not** automatic reconnect, continuous
hands-free listening, or conversation-memory persistence. Real Windows unplug,
network-loss, and extended-session acceptance are release checks.

### Desktop scheduler (v0.5.2)

You can now run the existing scheduler **inside FRIDAY, without a separate
PowerShell window**. Open FRIDAY and click **Start scheduler**. This does not
connect the microphone or Gemini Live. Local reminders and timers use Windows
tray notifications. To publish reminders to your iPhone's Google Calendar,
explicitly check **Sync Google Calendar (iPhone view)** *before* starting.

The calendar checkbox is off by default. If you select it, use your previously
configured dedicated FRIDAY calendar and saved OAuth; the desktop never opens
a Google sign-in browser by itself. If authorization is missing or expired,
the worker stops and shows a safe setup message. The foreground CLI can still
be used for one-time setup or diagnostics.

Closing the window to the tray **keeps the scheduler running**, even if the
voice session is disconnected. **Quit FRIDAY** stops its owned scheduler and
releases the SQLite lease. The scheduler is *not* a persistent Windows service:
new reminders will not be delivered or synced while the entire app is exited,
unless you run the separate CLI worker. There is no automatic startup on login.

Do not run two workers. When an existing terminal worker owns the database,
the desktop shows **Worker running externally** and disables Start; Stop can
only control the worker launched from that desktop instance. Stop does not
delete pending reminders or modify your Google Calendar authorization.

Local alerts are best-effort OS tray notifications and can be suppressed by
Windows notification settings or Do Not Disturb. The worker waits for the
desktop to attempt a notification before acknowledging an item; if the tray
is unavailable it retains the existing bounded retry/failure behavior rather
than silently discarding a console alert. For a platform without a functioning
system tray, use the existing foreground worker:

```powershell
python -m friday schedule worker --calendar-sync
```

The desktop shows **not running**, **local-only**, **waiting**, **syncing**,
**sync OK**, or **sync failed**, with the last successful sync time when known.
Sync OK means the Google Calendar API completed; it does *not* guarantee an
iPhone refresh or push notification. SQLite remains authoritative for local
reminders. The normal CLI worker still checks due jobs every second and
attempts Google sync at startup and every 60 seconds.

Unapproved drafts are discarded on voice-session shutdown. The app saves no raw
audio or persistent conversation transcript.

## Optional Gemini Live diagnostic

Install `python -m pip install -e '.[gemini,voice,web,dev]'`. Copy `.env.example` to `.env`, enter a **new or existing** Google AI Studio key as `GEMINI_API_KEY`, and keep `.env` untracked. Verify the model ID and quota in your AI Studio project; the default is `gemini-3.8-live` as documented in Google's September 2026 Live API guide.

```bash
python -m friday live --text "Friday, introduce yourself briefly"
python -m friday live --text "Say hello" --output friday-hello.wav
```

A Gemini Live session uses network/API quota. Do not use private recordings or secrets as prompts without considering the provider's current data handling terms. Only `--output` saves generated audio; raw input audio is not implemented or stored.

## First live voice conversation (Windows PowerShell)

Use **headphones** because this version does not implement echo cancellation.

```powershell
git pull --ff-only
py -m pip install -e '.[gemini,voice,web,dev]'
py -m pytest -q
py -m friday devices
py -m friday clock
py -m friday tools
py -m friday talk
```

Press **Enter** to activate the microphone, speak, and press **Enter** again to stop recording and prompt Gemini to respond. Repeat for multiple turns. Type `/quit` and Enter to disconnect. The default session limit is 300 seconds, set by `FRIDAY_MAX_SESSION_SECONDS` in `.env` or `--max-seconds` on `talk`. The microphone starts **only** after the user presses Enter; the Gemini Live session connects when `talk` launches. To choose specific devices, use `--input-device INDEX --output-device INDEX` with indices from `devices`.

**Turn sequencing and playback (v0.3.1):** After stopping the microphone, wait for FRIDAY to finish her response. Another ordinary recording cannot begin until Gemini reports completion or interruption and the speaker buffer has drained. Extra Enter presses during playback are ignored; `/quit` remains available. A response stalls after 30 seconds without provider or actual playback progress and displays per-turn audio and event counters without logging raw audio; the configured overall session limit still applies. Long replies use bounded, lossless playback backpressure instead of discarding speech when Gemini sends audio faster than the speakers can play it. Gemini server-side VAD is disabled for `talk`; FRIDAY sends explicit activity start/end around captured PCM. The separate text `live` diagnostic still uses the default Gemini activity mode. This is still toggle-to-talk, not hands-free barge-in. Use headphones to minimize speaker-to-microphone echo; acoustic echo cancellation is not implemented.


### Voice reminder management — opt-in (v0.4.2)

**Voice language (v0.4.4):** FRIDAY is instructed to reply in American English
by default. The `talk` command now also passes an `en-US` language hint to
Gemini Live's input transcription, which biases short English utterances away
from accidental Spanish detection. This is a recognition hint, not a guarantee:
short or unclear input may still be mis-transcribed. Use `--input-language auto`
for multilingual speech recognition. Neither mode changes the model's native
AUDIO output, the separate approval gate, or the audio device selection.


Start the independent scheduler with Google Calendar sync in one terminal:

```powershell
python -m friday schedule worker --calendar-sync
```

In another terminal, explicitly opt in to voice drafting:

```powershell
python -m friday talk --input-device 1 --reminders
```

Say, for example, "Friday, remind me tomorrow at seven PM to study."
The model may call `get_local_time`, then `draft_reminder` with a validated
future ISO 8601 time including an explicit UTC offset. FRIDAY prints a
**REMINDER DRAFT** notice. It has **not** created an event or written SQLite.
Wait for the spoken answer to finish. In a **new** Enter-to-talk turn, say
exactly "Yes, create that reminder" (or simply "Yes"). A user-input
transcription must match an allowlisted whole phrase: the model cannot
approve its own draft. A successful commit prints **REMINDER CREATED** with an
ID. You may also type `/approve`, or say "Cancel reminder" or type `/reject`.
A draft expires after five minutes or disappears on exit. If speech is
mis-transcribed, the app does not save it; use `/approve` to confirm the
visible pending draft. Do not mistake the model's spoken assurance for a save:
the terminal's **REMINDER CREATED** message and `schedule list` are authoritative.

The worker publishes approved future reminders on startup and approximately
every 60 seconds. Voice mode does not directly call Google and does not need
your Google credentials. Existing `talk` sessions without `--reminders` keep
their read-only model tool surface. You can also ask "What reminders do I have?", "Move my study reminder
from 7 PM to 8 PM", "Change the text of my study reminder", or "Cancel my
study reminder". FRIDAY first lists pending reminders and selects their exact
IDs. If multiple records match, she should ask which date and time you mean.
An edit/cancel is only a **draft**, with the original reminder and proposed
change displayed in the terminal. Confirm in a **new** voice turn with "Yes"
or type `/approve`; use `/reject` to discard. An approved edit retains its
original SQLite ID and updates the **same Google Calendar event** at the next
worker sync; cancellation removes its linked event. The app refuses a stale
approval if another process edited/cancelled the reminder first. Old SQLite
files are upgraded non-destructively with revision tracking. Only pending,
future one-time reminders can be edited/cancelled in voice mode. Timers and
already-delivered reminders cannot be modified. Edits made directly in Google
Calendar do not flow back to SQLite.

You can also edit a pending reminder locally with its exact ID:

```powershell
python -m friday schedule list
python -m friday schedule edit REMINDER_ID --at "2026-09-27T20:00:00-04:00"
python -m friday schedule edit REMINDER_ID --text "Updated study topic"
python -m friday schedule cancel REMINDER_ID
```

Replace the example time and ID with the actual future time and ID. There
are still no recurring schedules, two-way mobile editing, or automatic phone
push guarantees.

### iPhone calendar view — opt-in Google Calendar sync (v0.4.0)

FRIDAY can publish **pending, future one-time reminders** to a separate calendar
named **FRIDAY** in your own Google account. Timers are not published.
SQLite remains authoritative; Google is an optional one-way view and the Google
Calendar app on your phone can show the same calendar. A published event is a
transparent 15-minute placeholder with a popup reminder at its start time.
Calendar/phone notifications depend on your Google Calendar settings; publishing
an event does not guarantee a phone push alert.

This feature is **off by default**. It never reads your primary calendar or
your other events, and it does not request a broad all-calendars scope. Its only
OAuth scope is `calendar.app.created`, limited to calendars FRIDAY creates.
Editing or deleting an event in Google does not modify the local SQLite record.
FRIDAY syncs cancellations made through `schedule cancel` by deleting linked
Google events. Already delivered reminders remain visible as past events.

Follow Google's [Calendar API Python setup](
https://developers.google.com/workspace/calendar/api/quickstart/python):
enable the Calendar API in your own Google Cloud project, configure an OAuth
consent screen (External / Testing and add your Google account as a test user
for personal testing), add the `calendar.app.created` scope, then create a
**Desktop app** OAuth client and download its JSON. Keep it private. **Do not
upload that JSON, the token, or your API keys to GitHub or this chat.**

Install dependencies in the active project virtual environment:

```powershell
python -m pip install -e '.[gemini,voice,web,schedule,calendar,dev]'
```

Authorize explicitly (the command opens your local browser):

```powershell
python -m friday schedule calendar connect --client-secrets "C:\\Path\\To\\downloaded-client.json"
python -m friday schedule calendar init
python -m friday schedule calendar status
```

Credentials stay in the FRIDAY user-data directory, normally
`%LOCALAPPDATA%\\FRIDAY\\google-token.json` on Windows. The file contains
OAuth access/refresh tokens, **not encrypted at rest**: protect your OS account
and do not share it. Reconnect if the grant is revoked or expires. Google's
External/Testing OAuth refresh tokens ordinarily expire after 7 days for this
scope; that is a Google testing-mode limitation.

Create a future reminder (replace the date, time and offset with the actual
desired value) and explicitly sync it:

```powershell
python -m friday schedule add --at "2026-09-26T19:00:00-04:00" --text "Study cybersecurity"
python -m friday schedule calendar sync
```

On your iPhone, open **Google Calendar**, signed into the same Google account,
and make sure the FRIDAY calendar is checked in the app's calendar menu. If it
does not appear in the main calendar view, check the calendar's visibility in
Google Calendar on the web. New events are not shown on your phone before the
first successful sync. Existing synced events can still be viewed when your
main PC is off, but new local changes require FRIDAY to run and sync.

To combine background alerts and opt-in periodic calendar publishing, restart
your existing scheduler worker with this command:

```powershell
python -m friday schedule worker --calendar-sync
```

It syncs at startup and every 60 seconds; errors leave local reminders intact.
The standard `schedule worker` remains local-only. Google Calendar is not a
substitute for FRIDAY's independently running reminder worker, and no Gemini
Live session is held open for synchronization. Never start two workers for the
same database. To stop the worker, press Ctrl+C.

Google event IDs are deterministic and conflicts are verified against FRIDAY's
private event marker before a link is saved. An uncertain initial calendar
creation can create a secondary calendar without recording its ID; inspect
Google Calendar before retrying `calendar init`. This release is one-way:
phone-side edits and two-way conflict resolution remain future milestones.

### Local schedules — first foundation (v0.3.9)

One-time timers and reminders now live in a separate SQLite database in your user
data folder (Windows: `%LOCALAPPDATA%\\FRIDAY\\schedules.sqlite3`). They survive
`/quit` and a FRIDAY restart. The **independent worker must be running** to emit
an alert; the initial delivery is terminal text, not an iPhone/Windows notification.
There is no new Gemini tool or calendar access in this release.

Install the optional worker dependency from your activated virtual environment:

```powershell
python -m pip install -e '.[gemini,voice,web,schedule,dev]'
```

In one PowerShell terminal, start the worker and leave it running:

```powershell
python -m friday schedule worker
```

In a second PowerShell terminal, create, inspect, or cancel schedules:

```powershell
python -m friday schedule timer --seconds 30 --text "Test timer"
python -m friday schedule add --at "2026-09-25T19:00:00-04:00" --text "Study"
python -m friday schedule list
python -m friday schedule list --all
python -m friday schedule cancel REMINDER_ID
```

Replace the example date and offset with the **actual desired future time**.
An explicit UTC offset is required; FRIDAY will not guess daylight-saving
ambiguities. `schedule worker --once` processes jobs already due and exits.
You can optionally set `--db PATH` immediately after `schedule` for isolated tests.

The worker uses APScheduler 3.x for the background dispatch tick and SQLite
as the source of truth. It enforces a single active worker lease, atomically
claims due jobs, and retries stale claims after a crash. An alert can be
repeated if the process crashes after displaying it but before acknowledgement;
**exactly-once delivery is not guaranteed**. This is a local-only milestone:
Google Calendar sync, voice-created reminders, recurring schedules and phone
notifications are later steps requiring separate user authorization.

### Weather — today and tomorrow (v0.3.8)

Weather uses Open-Meteo's geocoding and forecast APIs. No weather API key is
required for qualifying non-commercial use; the service requires attribution.
Read the [Open-Meteo terms](https://open-meteo.com/en/terms) before commercial
deployment. Weather remains a separate read-only tool even when `--web` is off.

```powershell
py -m friday weather --location "Brooklyn, New York" --day today
py -m friday weather --location "Brooklyn, New York" --day tomorrow
py -m friday talk --input-device 1
```

In voice mode, ask “Friday, what's the weather in Brooklyn, New York today?”
or “Will it rain in Brooklyn, New York tomorrow?” The location **must be
supplied explicitly**; FRIDAY must ask if you say only “What's the weather
here?” The clock is not a geographic location. Ambiguous geocoding returns
possible locations instead of silently picking one. The forecast provides
°F temperatures, mph wind, daily high/low, conditions and maximum
daily precipitation probability (where supplied). Current conditions are shown
only for today. Probabilities are forecasts, not promises; missing values
are not fabricated. The app uses two fixed HTTPS endpoints, rejects redirects,
bounds response sizes and rate-limits repeat requests after a 429. Weather
queries are not saved; HTTP request URLs containing locations are suppressed
from INFO logs. Data attribution: [Open-Meteo.com](https://open-meteo.com/en/docs).

### Local clock and tool foundation (v0.3.0)

Ask FRIDAY, “What time is it?” or “What's today's date?” She can call
`get_local_time` and speak a value from your **computer's configured local clock**.
The response includes the operating-system timezone name and UTC offset; it does
not disclose or infer a geographic location, and it is not a general world-time
service. To check the setting without consuming Gemini quota, run:

```powershell
py -m friday clock
```

The tool is read-only and has **no arguments**. In v0.3.0 it runs through a typed, deny-by-default registry; approval-required tools are not advertised or executed until FRIDAY has a real host-side approval flow. A bounded in-memory audit records only tool name, policy, and outcome, never arguments or responses. FRIDAY explicitly rejects unknown
function names or unexpected arguments, and sends the rejection back to Gemini;
she cannot run commands, change system settings, or access your files. The current
clock value and timezone are sent to Gemini only when she calls the tool in a Live
session. The terminal prompt and Enter-to-talk interaction are unchanged.

If the sounddevice/PortAudio backend cannot open a device, FRIDAY reports a local audio error and cleans up rather than silently accessing the wrong device. If Gemini sends an interruption event, pending playback is cleared; pressing Enter for a new turn also clears it. This is a push-to-talk **toggle**, not a hold-to-talk keyboard shortcut.

**Validation boundary:** Offline automated tests cover fake audio device callbacks, PCM framing, buffer limits, two-turn delivery, interruption flush, and cleanup. They do not prove that an individual Windows microphone, sound driver, or Gemini account works; test with real hardware and your own key. The `talk` command uses API quota. No local raw recordings are persisted by default.

### Opt-in Tavily web search (v0.3.7)

Tavily Basic Search is the default web-search backend. The Researcher free plan
currently offers 1,000 API credits per month without a credit card; a Basic Search
costs one credit. Review your current Tavily plan and quota before use.

Get a key at https://app.tavily.com/ and put it in the untracked `.env`
file beside `GEMINI_API_KEY`:

```dotenv
GEMINI_API_KEY=your-existing-gemini-key
TAVILY_API_KEY=your-tavily-key
FRIDAY_SEARCH_BACKEND=tavily
```

Install the optional HTTP dependency:

```powershell
py -m pip install -e '.[gemini,voice,web,dev]'
```

Test Tavily **separately, with one credit-consuming request and no microphone**:

```powershell
py -m friday web-search --query "latest Python release site:python.org"
```

Then test the voice tool handoff:

```powershell
py -m friday talk --input-device 1 --web
```

Ask: “Friday, search the web for the latest Python release and give me a source.”

The working Gemini Live voice connection advertises only the strict read-only
`search_web` function and the existing local clock. Tavily receives
the query selected by Gemini only after `--web` is enabled. FRIDAY requests
up to five search results at basic depth with raw page content, images and Tavily's
extra answer-generation disabled. It passes limited search-result excerpts and
HTTPS links back to the voice model, treating excerpts as **untrusted evidence,
not independently verified full pages**. FRIDAY never visits those links or obeys
instructions embedded in the excerpts. The terminal lists the returned source
links; Tavily provides no Google Search Suggestions HTML, so no browser preview
is expected for Tavily results. Nothing is retained as search history. The query
is shared with Tavily, so avoid sensitive requests.

If a search returns HTTP 429, FRIDAY reports it and blocks additional search
HTTP requests during that voice session. Other failed or empty responses do not
produce invented citations. A missing `TAVILY_API_KEY` is rejected before
the microphone opens. No paid Gemini text search is attempted as a fallback.

The old Gemini grounded-text search remains available only when you deliberately
set `FRIDAY_SEARCH_BACKEND=gemini` (and optionally
`FRIDAY_SEARCH_MODEL`). It can use separate Gemini quotas/billing.
`py -m friday web-check --with-clock` checks the Live function
declaration but does **not** test Tavily itself; use `web-search --query`
for that. The ordinary `talk` command and the exact
`FRIDAY [Enter: talk/stop, /quit]:` prompt remain unchanged.

## Architecture



```text
CLI / future desktop UI + opt-in PortAudio mic/speaker
       |
SessionManager (state, errors, bounded event queue)
       |
VoiceProvider protocol (send_text, send_audio, end_input, events, close)
       +-- FakeVoiceProvider (offline tests)
       +-- GeminiLiveProvider (SDK isolated here)
       +-- Future local pipeline (STT -> LLM -> TTS)
```

For the event contract and security boundaries, see `docs/ARCHITECTURE.md`; for the next development slice, see `docs/ROADMAP.md`.

## Safety defaults

- The model sees only explicitly registered read-only tools; currently the local clock. Unknown and invalid calls are rejected. Approval-required tools are not executable.
- No arbitrary shell, file, email, or network actions are exposed to the LLM.
- The single allowlisted model tool reads only OS local date/time and timezone.
- No raw audio is logged; audio event `repr` reports only byte length.
- SDK imports are confined to `src/friday/providers/gemini_live.py`.
- `.env`, `.wav`, `.pcm`, and virtual environments are ignored by Git.
- Event queue is bounded. Live provider audio waits for queue capacity rather than evicting words; urgent lifecycle/error signals can still displace queued events. Microphone input and speaker playback each have bounded buffering. Speaker output uses paced, non-dropping enqueue during normal speech, and immediate flush for an explicit interruption or shutdown.

## Running checks

```bash
python -m pytest -q
python -m compileall -q src tests
ruff check .
```

For a fake-provider smoke test: `python -m friday demo --once "Hello"`.

## Scope

v0.2 is an opt-in voice vertical slice. Real Windows microphone/speaker behavior, repeated Gemini turns, and resource cleanup are the current acceptance target; wake word, memory, and computer actions follow after stabilization.
