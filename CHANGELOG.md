# Changelog

All notable changes are documented here. Format roughly follows
[Keep a Changelog](https://keepachangelog.com/); this project uses
[Semantic Versioning](https://semver.org/).

## Unreleased

### Added
- **Echo Flow starts Ollama when it is installed but not running.** Ollama does
  not register itself for Windows autostart and Echo Flow does, so every login
  brought the daemon up with its model backend down; the user was told to start
  Ollama and restart. The daemon now probes the port at startup and, if the
  binary is on disk, launches it. It only ever starts a backend you already
  installed and never downloads or installs anything. `cleanup.autostart_ollama`,
  on by default. The registry workaround this replaces is gone from the README
  troubleshooting table.
- **Cleanup upgrades itself when Ollama finishes booting, with no restart.**
  A cold `ollama app.exe` start measured at over 25 seconds on a real machine,
  which is far too long to block daemon startup for. So startup waits only long
  enough to catch a warm start (`cleanup.autostart_timeout_sec`, 8s) and then
  comes up in rules-only cleanup, while a background watcher polls and flips the
  provider back to `ollama` the moment it answers. `phase.decide()` runs once at
  startup and pinned the provider for the life of the process, so before this an
  Ollama that arrived thirty seconds late was ignored until the user restarted
  the daemon, which is precisely what the old toast told them to do.
- **Deterministic filler removal for the LLM-free path** (`src/fillers.py`).
  Every cleanup prompt tier instructs the model to "remove filler words: um, uh,
  like, you know", which means the users who could not run a model never got it:
  on a fresh install with no Ollama and no API key the `learned` provider has no
  patterns to apply, so text reached the cursor with every hesitation intact.
  There is now a rule-based pass on all four raw-words return paths (provider
  unreachable, learned-with-nothing-to-apply, learned-to-Ollama fallback failed,
  hallucination guard tripped). `cleanup.strip_fillers`, on by default.

  It is deliberately timid, because the failure modes are asymmetric: a filler
  left in is a blemish, a word eaten is invisible data loss. Hesitations (um,
  uh, er, erm) are removed anywhere; discourse markers (you know, I mean, sort
  of, basically) only where comma-fenced or opening a sentence; "like" only when
  fenced by commas on **both** sides, since every other position is a real verb
  or preposition. A sentence is never emptied, and the pass is purely
  subtractive, so the "we keep YOUR words" contract still holds.

### Changed
- **The degraded-mode toast says what still works.** It read "Cleanup LLM
  offline — using deterministic polish only. Start Ollama or set GROQ_API_KEY,
  then restart", which is wrong twice over now: the daemon has already tried to
  start Ollama, and a user who simply has no model needs to know the app is
  still doing something for them. It now names the cleanup they are getting
  (capitalization, punctuation, filler removal) and distinguishes "no local
  model found" from "installed but did not start". It no longer ends with "then
  restart", because the watcher above means a restart is not needed; a second
  toast confirms when full cleanup comes back.
- **README documents the no-model path.** The setup section previously said that
  without Ollama "you simply get Whisper's raw text", which undersold it. There
  is now a table of what rules-only cleanup does and does not do.

### Fixed
- **A source checkout no longer starts with cloud cleanup on.** 0.3.1 stopped
  the installers from shipping the maintainer's working `config.yaml`, but the
  seeding that replaced it was gated on `sys.frozen`, so only frozen installs
  got the factory default. Anyone following the README's from-source path, the
  one it recommends "if you want to hack on it", still got a tracked
  `config.yaml` carrying `cleanup.provider: groq` and
  `allow_cloud_cleanup: true`, two sections above the same README promising
  "Regular dictation is 100% local". Step 5 then told them to set `GROQ_API_KEY`
  for Prompt-Engineering mode, which was the exact keystroke that started
  routing every dictation to Groq.

  `config.yaml` is now gitignored, `packaging/default/config.yaml` is the only
  config in git, and `main.load_config` seeds from it on first run whether or
  not the app is frozen. A clone cannot inherit anyone's settings because it
  arrives with no config at all. Test fixtures read the shipped default instead
  of a working config, so the suite now asserts against what users get, and
  `make_default_config.py --check` verifies the tracked default's values
  directly when there is no working config to diff against, which is the case
  in CI.
- **`fillers.strip` could not eat the front of a real word.** Caught while
  building it: a hesitation pattern fenced only on its left turns "Ahmed" into
  "Med" and "Erlang" into "Lang". Both edges are fenced, and the regression is
  pinned by a test list of real words that begin with hesitation sounds.

## 0.3.1 - 2026-08-24

### Changed
- **Removed every long dash from the interface.** 141 em and en dashes across 27
  templates, plus 31 in strings the user actually reads (flash messages, the
  sound picker labels, the prompt-engineering style names, the Action Mode
  labels). Each one was repunctuated by sentence rather than swapped for a
  comma, because mechanical comma substitution produces splices and reads worse
  than the dash it replaced. The app's own AI-tell detector (`src/aitells.py`)
  now scores the rendered copy of every page at zero. All 22 routes verified
  dash-free in their rendered HTML.
- **Page titles use one separator.** They previously joined the page name and
  the app name with a long dash; they now use `Page · Echo Flow` throughout.
- **The notification bell is an inline SVG instead of an emoji.** A colour-font
  emoji ignored the theme, needed a grayscale filter to look passable, and sat
  next to an SVG hamburger in a different visual language.
- **Inline `code` has one treatment.** Its chrome was attached to three scoped
  selectors, so 21 of the 29 `<code>` elements in the templates rendered with no
  background, border or padding. `settings/vibe.html` showed both treatments on
  a single page.
- **Deleted the dead `settings/privacy.html`.** `GET /settings/privacy` has
  redirected to `/privacy` for some time, so the template was unreachable, and
  the Settings nav still pointed at it. The wipe endpoint also redirected all
  four of its outcomes, success included, to that dead URL, where the 302 to
  `/privacy` dropped the query string: wiping your history reported nothing at
  all. Those redirects now target the live page.

### Accessibility
- **Focus indicators failed WCAG 1.4.11 everywhere.** There were four different
  translucent accent rings (35%, 30%, 18%, 16%) which, being mixed toward
  transparent, measured between 1.28:1 and 1.90:1 against their own background
  against a 3:1 requirement, and the command palette's input set `outline: none`
  with no replacement at all. There is now a single `--focus-ring` token, solid
  accent at 6.87:1 on dark and 9.23:1 on light, with an inner band so the ring
  stays visible on a primary button whose fill is the accent colour itself.
- **`--muted` was tuned against one surface.** It hit exactly 4.53:1 on
  `--panel` and dropped below AA on every lighter surface, including 4.24:1 for
  the sidebar navigation labels. Both muted tokens were re-measured across the
  sidebar, panel, background, card and chip surfaces and now clear 4.5:1 on all
  five.
- **The light theme was missing half its semantic colours.** `:root` is the dark
  palette and `[data-theme="light"]` overrode only 10 of the 14 colour tokens, so
  `--warn` and `--danger` kept dark-tuned values on a near-white surface. Every
  warning and error in the light theme rendered around 2.2:1 against the 4.5:1 AA
  floor, including the two Privacy-ledger rows whose whole job is to tell you
  something IS leaving the machine. Both tokens now have light values (5.4:1 and
  6.0:1), which fixes roughly 22 call sites at once.
- **The most destructive button in the app was amber and illegible.**
  `.btn.danger` read `var(--warn, #c0392b)`, but `--warn` is always defined so
  the red fallback never applied: "Wipe dictation history" rendered in the same
  amber as warning pills, white-on-amber at 2.19:1. There is now a
  `--danger-solid` token for solid destructive fills (6.5:1 light, 7.2:1 dark),
  distinct from `--danger`, which is the text colour.
- **Notification severity badges were unreadable in light mode** (1.3:1 to 1.8:1).
  The badge text *is* the severity word, so severity was the thing being lost.
- **Onboarding step numerals** were a hardcoded near-black on `--accent`: fine on
  the dark accent, 1.90:1 on the light theme's darker green. They follow `--bg`
  now.
- **Nothing in the stylesheet had a disabled state.** The Humanize button
  disables itself for what its own copy calls "up to a minute" while keeping the
  full accent gradient and `cursor: pointer`, so it looked clickable and did
  nothing.

### Fixed
- **The knowledge graph was unusable on a phone.** Its wrapper was positioned
  against the viewport with hardcoded `220px`/`56px` literals instead of the
  `--sidebar-w`/`--topbar-h` tokens, so the sidebar-collapse media query could
  never reach it: measured 145px of a 365px viewport (40%). On desktop the same
  drift overlapped the sidebar's last 4px and left a 4px strip under the topbar.
  Now 100% of a narrow viewport, and pixel-exact on desktop.
- **The Home latency tile rendered its unit at the headline size.** The count-up
  animation wrote `el.textContent`, replacing every child, which destroyed the
  `<span class="muted small">ms</span>` beside the number. It now animates the
  number's text node and leaves sibling markup alone.
- **Three pages showed a breadcrumb that disagreed with the sidebar and the
  page heading** ("Myvoice" vs "My Voice", "Calibration" vs "Calibrate"). The
  crumb came from a hand-copied dict carrying a "keep in sync" comment; it now
  derives from `SECTIONS`, the same list the sidebar renders from.
- **Horizontal scrolling on narrow screens.** The Privacy page's DB path had no
  break opportunity and ran ~250px off a 375px screen; `.row-form` inputs could
  not shrink below their intrinsic width, pushing submit buttons off-viewport by
  60 to 115px; and the `kv-table`s exceeded their card with nothing between them
  and `<body>` that scrolls. Because the topbar and sidebar are `position: fixed`,
  scrolling sideways slid the content out from under its own header.
- **The mobile drawer scrim covered the topbar**, dimming the hamburger that
  opened it and swallowing taps on the theme toggle and the bell.
- `<mark>` in the Humanize result set only a background colour, so the UA default
  black applied: about 1.6:1 on the dark theme, on exactly the spans the feature
  exists to point at.
- "Mark read" on Notifications carried `link-danger`, a 26x26 icon box, which
  squashed its label and coloured a harmless action as destructive.

### Security
- **The installers shipped the maintainer's working config as the factory
  default.** `config.yaml` at the repo root does two incompatible jobs: it is
  what the daemon reads from a source checkout, and it was also bundled into the
  builds and copied to `%LOCALAPPDATA%\EchoFlow\config.yaml` on a frozen
  install's first run. So a clean install started with `cleanup.provider: groq`
  and `allow_cloud_cleanup: true`, meaning **every dictation of every new user
  went to a cloud API** while the README described local-by-default; with Action
  Mode and command mode live; with `command_prefix: jarvis`, so every documented
  "computer, ..." example did nothing; with the paste-in humanizer pointed at a
  model no installer pulls; and with the first-run tour already marked complete.
  In each case the code's own default was correct and only the shipped file
  diverged. The builds now bundle `packaging/default/config.yaml`, generated
  from `config.yaml` by `scripts/make_default_config.py`, which inherits every
  comment and value and overrides only the keys that are unsafe as a stranger's
  starting point. A test runs the generator's `--check` mode, so the two cannot
  drift and a newly added setting is picked up automatically.
- **The mobile bridge's code defaults were `0.0.0.0` plus mDNS-on**, the exact
  combination `docs/MOBILE_BRIDGE.md` warns against. `config.yaml` ships
  loopback with mDNS off and `dashboard/privacy.py` reports `127.0.0.1` for a
  missing key, but `load_config` does no defaults merge, so a config lacking
  those keys listened on every interface and advertised itself to the network
  while the privacy ledger said loopback with no warning. Both code defaults now
  fail safe and agree with the other two.
- **Action Mode's "not configured" replies echoed the spoken text back**, which
  defeated SEC-3 redaction: `classify`'s `^open (.+)$` catch-all puts the whole
  utterance in the slot, and the reply is persisted to `voice_actions.error` and
  the notifications table, then rendered on `/actions` and `/notifications`. Two
  existing tests asserted the leak (`assert "secret" in msg`) and now assert the
  opposite.
- **The mobile bridge fed the desktop's learned patterns.** The dictation row was
  carefully tagged `source='mobile'` for the RAG filter, then the same pair was
  recorded into `learned_patterns`, which has no source filter at read time and
  rewrites desktop dictations. Both that and the teacher-distillation spawn now
  honor `cleanup.learning.trust_mobile`.
- **`/v1/health` was an unthrottled key-guessing oracle.** It checked the shared
  key itself rather than going through `@auth_required`, so wrong-key probes were
  never counted, never logged, and never hit the M1 lockout, and a locked-out IP
  could still guess there. An absent header stays a free liveness ping.

### Fixed
- **The light theme lost its own accent.** `base.html` applies the accent
  override to both themes, so the accent shipped in `config.yaml` put the dark
  theme's `#3eaf6f` behind white button labels in light mode: 2.78:1, below the
  4.5:1 AA floor and below even the 3:1 large-text floor. The shipped value is
  now empty, meaning each theme uses its designed accent, while the key still
  exists so Settings can save one.
- **Humanize highlights were unreadable in dark mode.** `<mark>` set only a
  background, so the UA default black `MarkText` applied: the spans the feature
  exists to point at rendered at about 1.6:1.
- **Home showed "0 dictations today" beside "9 m saved today".** The count starts
  at midnight; the time-saved tile was a rolling 24 hours. They now share a
  boundary, and both exclude teacher-distillation rows, which duplicate a real
  utterance and were double-counting on both tiles.
- **The activity heatmap's first column was structurally blank.** The query
  cutoff came from the clock while the grid start was snapped back to a Monday,
  so leading cells were rendered from days never fetched: real dictations showed
  as level 0 and the peak was understated. On a Saturday that is a whole column.
- **The "filler ratio" tile counted fillers after cleanup removed them.** It read
  `cleaned_text`, so it measured the cleaner rather than the speaker and could
  only ever approach 0%. It now counts over the raw transcript.
- **Time saved counted words by counting spaces**, so newlines and tabs were not
  separators: a multi-paragraph email read about 12% short and disagreed with the
  total-words tile over the same rows. Both now use one definition.
- **The "My Voice" shadow statistic was a tautology.** Only accepted, changed
  rewrites were ever written to `humanize_shadow`, and `changed` was then counted
  over exactly those rows, so the page read "N of N would have changed" forever,
  for every user, no matter how humanize was performing. Every evaluation is now
  recorded with an outcome, so the denominator is real.
- **Dictionary terms past the 80th were re-suggested after being promoted.** The
  80-term cap is the Whisper `initial_prompt` budget; it was also being used as
  the "already known" set.
- **Shortcut icons pointed at a path PyInstaller 6 no longer creates.** Bundled
  data lives under `_internal/`, so `{app}\assets\icon.ico` never existed after
  install and both installers shipped iconless shortcuts.
- **`UNINSTALL.bat` never stopped the daemon and then claimed the venv was
  removed.** The `taskkill` filtered on a window title that a hidden launcher
  never has, and the loop under it had an empty body, so the running daemon held
  `python3xx.dll` open and the `rmdir` partially failed while still printing OK.
- **`build_nuitka.ps1` stamped every binary 0.1.0.0**, four releases stale. It
  now reads `src.__version__`.
- **The landing page still under-reported cloud egress**, the same class of bug a
  prior release fixed in the README: it named only PE mode and the teacher loop,
  omitting `cleanup.allow_cloud_cleanup`, which applies to every dictation. Its
  "measured, not promised" caption also described a hardcoded constant, and the
  README and product overview said "three paths" when there are five.
- The daemon spec and `installer/README.md` still carried a "before this build is
  useful, patch main.py" warning for work that shipped long ago.
- **Silero VAD had never actually run.** `_is_voiced` handed the model a
  2048-sample slice, but Silero v5 accepts exactly 512 samples at 16 kHz and
  raises on anything else, and the bare `except` swallowed it, so every call
  silently fell back to the crude RMS threshold while the model was still loaded
  at each startup. In toggle mode that meant a room above the RMS floor (a fan,
  an AC unit) never auto-stopped and ran to the 120 s cap, while a 1.5 s pause
  mid-sentence cut the user off. The tail is now scored in 512-sample windows,
  Silero's state is reset per window, and a genuine failure warns once instead of
  going quiet forever.
- **An unplugged mic wedged the hotkey until restart.** The toggle-mode worker
  had no `try/finally`, so a PortAudio error left `_active` stuck at `True` and
  every later hotkey press became a silent no-op.
- **The veto did not abort in toggle mode.** Holding Ctrl+Shift on the way to
  the re-paste combo logged "dictation aborted" and then transcribed and pasted
  the audio anyway, because the recording thread never consulted the cancel.
- **The tray icon lied about recording.** Every reject gate (no audio, under
  400 ms, below the RMS floor) returned with the icon still red, and an empty
  transcript left it spinning on "thinking". A sub-400 ms tap of Ctrl+Shift left
  a red mic claiming to record the user indefinitely. Calibration also set a
  bogus `"idle"` state that was never one of the four the tray accepts.
- **`Recorder.start()` leaked the device on the error path.** If `stream.start()`
  raised after the stream was opened, the handle was never closed, and
  sounddevice defines no `__del__`, so each retry orphaned another device handle
  and its callback thread for the life of the process.
- **The comma-storm heuristic ate real comma lists.** It fired at 3 commas (the
  docstring said 4) and never checked the Title-Case signature it documented, so
  "Add salt, pepper, cumin, paprika, oregano." lost its commas. The re-lowercase
  pass also ignored the proper-noun allowlist, so a genuine storm over real names
  came out as "sarah michael daniel".
- **Repeat collapsing deleted across sentence boundaries.** Punctuation was
  stripped before comparison, so "I don't know. I don't know what to do." became
  "I don't know. What to do." Whisper's actual stutter still collapses.
- **The delete-first pass flattened bulleted lists.** `trim` preserved only
  blank-line separators, so "- First item.\n- Second item." was joined into one
  run-on line before the model ever saw it, and the markdown guard downstream
  could not undo it.
- **`_normalize_dashes` swallowed paragraph breaks.** `(?m)\s*,\s*$` crossed the
  newline, merging "one,\n\ntwo" into one paragraph immediately before the guard
  that exists to reject exactly that change.
- **`i.e.` became `I.e.`** The standalone-"i" rule ran over abbreviations the
  `_ABBREV` table already protects, and over URLs like `bit.ly/i`.
- **Learned casings silently expired after 14 days.** The read path filtered at
  `count >= 1` while decay deletes at `< 0.25`, so a casing taught once went dark
  after a single half-life while still sitting in the table, and the word then
  lost its protection from the de-Title-Case pass and got actively lowercased.
- **`cleanup.casing.learn_from_edits: false` was ignored by the review queue.**
  Edit Last honored it; saving from the review queue mined casings anyway.
- **Settings → General could never save.** `dashboard.accent_color` had no key in
  `config.yaml` and a colour input always submits, so every save failed on a key
  the writer refuses to create, after already persisting the earlier fields, and
  skipping the hot-reload that applies the language.
- **The Style page could never be saved.** The two blank "add a profile" rows
  were submitted as `style=""` and rejected the whole form.
- **`config_writer` could rewrite the wrong setting.** The indent tracker read
  one level too shallow and accepted any deeper line, so `cleanup.enabled`
  resolved to `cleanup.learning.enabled` in a config that nests them that way.
  Blank lines were also treated as indent-0 entries that closed every open block.
- **The RAG backfill starved the dictation logger.** It held one write
  transaction across every row, so dictations logged during a multi-thousand-row
  backfill failed with "database is locked" and were lost. It now commits in
  batches, with a longer busy timeout.
- **`mobile.trust_for_rag` did nothing.** The documented, shipped config knob was
  never read, so phone dictations stayed filtered out of RAG whatever it was set
  to.
- **The Outcomes "Mobile" filter showed desktop and mobile combined**, identical
  to "All", because the source clause took a boolean that cannot express
  "mobile only".
- **Backlinks never worked for a long auto-titled note.** `_auto_title` marks
  truncation with "…", a character in no source text, and cuts mid-word, so
  neither the `LIKE` nor the word-boundary regex could ever match.
- **Editing a dictation that no longer exists reported success.** The `UPDATE`
  affected zero rows and the handler still redirected as if it had saved.
- **Inbox triage flagged every correctly-written Chinese, Japanese, Hindi and
  Arabic dictation.** The terminator list was ASCII-only, so a sentence closing on
  `。`, `।` or `؟` read as "looks cut off"; and because CJK has no inter-word
  spaces, `str.split()` scored a whole Chinese sentence as one word, so it read as
  "very short" as well. For those users every row landed in "Needs a look" and the
  quiet pile was permanently empty, which defeats the entire feature. The
  terminator set now covers CJK, Devanagari and Arabic stops plus closing quotes
  and guillemets (an English sentence ending in a curly quote was mis-flagged
  too), and length is measured in word-equivalents so unspaced scripts are counted
  by character. English detection is unchanged: still 37 of 41 known-bad rows.
  `,` `;` `:` are still deliberately not terminators, unlike `cleanup.py`'s
  punctuation rule, because trailing on a comma is what truncation looks like.
- **An unedited dictation could look permanently edited.** `needs_review`
  stripped `cleaned_text` but compared `original_cleaned` raw, so any transcript
  carrying outer whitespace never matched itself. Reachable today: the documented
  `cleanup.provider: none` returns the transcript verbatim, and Whisper segments
  routinely start with a space.
- **A whitespace-only result was filed under "looks fine".** The edit route writes
  form input through with no validation, so a blank save vanished from view rather
  than being surfaced as the broken row it is.
- **The two group headings had no CSS.** `.inbox-group-h` and `.inbox-quiet`
  matched no rule, so the heading fell through to the browser default (~1.5em with
  large margins) in a UI that runs at 13px, and the groups sat flush together with
  no separation.

### Changed
- **The inbox triages itself instead of asking for a verdict on every dictation.**
  Cards split into "Needs a look" and a collapsed "Looks fine", and a flagged card
  says why it was flagged rather than showing an opaque badge. Nothing is hidden:
  the quiet group keeps every action on expand.

  The starting request was to auto-approve anything that was "a complete correct
  sentence". Two things ruled that out. First, completeness does not imply
  correctness: `"Again."` and `"LinkedIn is."` are both complete sentences, both
  scored 96+, and both had been marked bad, so that rule would have auto-approved
  precisely the rows a user rejected. Second, Approve is already close to a no-op
  - `analytics.acceptance_rate` counts an untouched row and an approved row
  identically, and no learning path reads `user_rating` except the opt-in My Voice
  exemplar pool. Automating it would have automated nothing, while quietly
  arranging for bad dictations to become few-shot examples of "how you write" the
  day My Voice is switched on.

  A row is flagged when its quality is under 75 (the threshold the UI already
  treats as good), or it is three words or fewer, or it does not end in terminal
  punctuation. Measured against 975 real dictations that catches 37 of the 41 rows
  the user had marked bad. Rows already marked bad or edited stay visible so that
  acting on a card never makes it disappear. **No verdict is ever written
  automatically.**

  Words-per-second was tried as a truncation signal and rejected: convincing on a
  handful of recent bad rows, useless across the full set (median 1.89 for bad vs
  2.05 for the rest), where any threshold catching the bad rows also flagged half
  the good ones.

### Fixed
- **The Privacy page could tell you nothing was leaving the machine while every
  dictation was going to Groq.** The ledger only ever looked for cloud egress via
  the *humanize* feature, so a config with `cleanup.provider: groq` plus
  `cleanup.allow_cloud_cleanup: true` and humanize off produced an empty exception
  list, and the page then printed its reassuring default: "Echo Flow only opens
  sockets to 127.0.0.1 ... No telemetry, no cloud sync." The ledger is the app's
  own transparency tool, so understating egress there is worse than not shipping
  one. It now detects the ordinary cleanup path independently and names the host.
- **`ollama pull` in the setup docs fetched a model the app never asks for.**
  Every instruction said `qwen2.5:3b-instruct` while `config.yaml` requests
  `qwen2.5:3b-instruct-q4_K_M`. Following the documented setup left Ollama without
  the tag it was about to be asked for, and cleanup silently fell back to raw
  Whisper text. README, `INSTALL.bat`, `scripts/setup.bat`, `PRODUCT_OVERVIEW.md`
  and the docs site now all pull the tag the app actually calls.
- **`dist_nuitka/` was not ignored by git**, unlike `build/` and `dist/`, so
  running `build_nuitka.ps1` left a compiled binary tree staged for accidental
  commit to a public repo.

### Changed
- **The privacy docs now describe the third cloud path.** README and
  `PRODUCT_OVERVIEW.md` said the only paths off the machine were PE mode and the
  teacher loop; `cleanup.allow_cloud_cleanup` is a third, and unlike those two it
  applies to every dictation rather than a deliberate keystroke.
- **Documented that dictation transcripts are written to `data/wispr.log`** in
  plain text, raw and cleaned, on every dictation. The privacy section previously
  named only `history.db`. Also noted that the Wipe button and the export zip both
  cover the database only, so the log outlives a wipe.
- **Stopped calling the dashboard "zero CDN".** The knowledge-graph page pulls D3
  from `d3js.org` at render time, and the module's own docstring called its output
  "self-contained". Both now say what actually happens; vendoring `d3.v7.min.js`
  would close it properly.
- Corrected `PRODUCT_OVERVIEW.md`'s stale version (`0.1.0` → `0.3.0`) and test
  count (833 → 1502), and stopped it claiming audio is stored in `history.db`:
  audio is never written to disk.

## 0.3.0 - 2026-07-25

### Added
- **Speaker adaptation, Phase 3 — guided voice calibration.** A new **Calibrate**
  page: read ~8 known sentences aloud (with your normal dictation hotkey), and
  Echo Flow compares what Whisper *heard* to the known *target* to get ground
  truth — then **pins the names it fumbled** straight into your dictionary and
  **learns the (heard → target) corrections**, instead of waiting for the same
  errors to surface organically. It reuses the real mic + Whisper path: while a
  session is active the daemon (`_do_dictation`) routes each utterance to the
  session instead of pasting, so nothing lands in your documents. The page polls
  progress live (sentence advances as you read), then shows a per-sentence
  baseline accuracy and an **Apply** step that seeds the Phase-1 learners.
  `src/calibration.py` (`CalibrationSession`, `word_accuracy`, `apply_seeds`);
  `/calibration` routes; shares the in-process `App` so no IPC is needed. Full
  Whisper weight fine-tuning remains intentionally out of scope.
- **Speaker adaptation, Phase 2 — language selection & auto-detect.** Echo Flow
  was pinned to English. Settings → General now has a **language dropdown**
  (Auto-detect + 16 languages); picking **Auto-detect** writes `whisper.language:
  null` so Whisper detects the language per dictation (needed for non-English or
  code-switching), and pinning a language keeps the ~20ms detect-skip speed win.
  The change is **hot-applied** — `reload_config` now refreshes
  `transcriber.cfg.language` alongside the decoder bias, so it takes effect on
  your next dictation with no restart. (Per-language vocabulary filtering is
  deferred: it only helps pinned bilingual setups, and language-neutral terms
  like "Kubernetes" don't meaningfully bias other languages.)
- **Speaker adaptation, Phase 1 — Echo Flow learns your voice's recurring errors
  faster and visibly.** An accent isn't tuned at the acoustic level (Whisper is
  already accent-robust, and it's a single-user app); it shows up as the *same
  words misheard the same way*, so the win is a stronger text-level correction
  loop.
  - **Multi-word ("n-gram") substitution learning.** The pattern miner only
    learned 1↔1 word fixes; a phrase mishearing like "note to vec" → "node2vec"
    (3 tokens → 1) fell through entirely. New `learn._diff_ngram_pairs` captures
    2–3 word `replace` spans, gated by a vendored, dependency-free **phonetic
    check** (`src/phonetic.py`, Metaphone) so a genuine mishearing is learned but
    an LLM paraphrase that changed meaning is rejected — "the weather is nice" →
    "let us ship it" scores 0.12 similarity and is dropped. Stored in a sibling
    `learned_ngrams` table with a stricter confidence bar
    (`PatternMiner.confident_ngrams`), applied longest-phrase-first before the
    single-word pass (`Cleaner._apply_learned_ngrams`).
    `learned.min_ngram_confidence` / `min_ngram_total`.
  - **Low-confidence → dictionary suggestions.** Whisper now returns the words it
    was unsure about (`word_timestamps`, surfaced as `meta["low_conf_words"]`);
    content words (names / technical tokens, never plain words or already-known
    terms — `src/vocab_suggest.py`) are recorded as suggestions
    (`History.record_vocab_suggestion`, `vocab_suggestions` table).
  - **Review surface.** The Dictionary page now shows "Suggested terms" ranked by
    how often each was fumbled; one click **Pins** a term into the dictionary
    (feeding the Whisper decoder bias on the next reload) or dismisses the noise
    (`src/dashboard/suggestions.py`). `whisper.word_confidence` /
    `word_conf_floor` toggle the per-word signal.
- **Humanize hard-exclude zones — the facts are never sent to the model.** In a
  methods section the numbers, hyperparameters, splits, metrics, citations,
  quotes and code ARE the content; precision there reads "competent" to a
  reviewer, and a humanizer that "improves the flow" of "F1 of 0.79" into "an F1
  of about 0.8" is doing damage. So instead of trusting the model to be careful,
  the tool now makes it impossible for it to be careless: a new detector
  (`src/protected.py`) finds every protected span, and any **sentence** carrying
  one is held byte-for-byte and never sent to the model — only the free-prose
  runs around it are rewritten. The protection is deliberately structural rather
  than a prompt instruction: no local model reliably preserves an inline
  placeholder token (the 3B and the escalation model alike paraphrase a masked
  `⟦0⟧` into "zero"), so masking would silently corrupt the very figures it was
  meant to guard. Keeping the whole sentence is coarser but honest — the tool
  refuses to edit the facts rather than gamble on them. The result notes how many
  spans were held exact. Sentence-splitting is span-aware, so a period inside
  `et al.`, `0.001`, or a closing quote is never mistaken for a sentence end.
  `protected.find/count`; `humanize_text(protect_spans=…)`;
  `experimental.humanize_text_protect_spans` (default true).

- **Humanize delete-first pass — cut the dead sentences before rewriting.** Most
  of the de-AI win is subtraction, not rephrasing, and it has to be
  deterministic: a small local model told to "be concise" paraphrases instead of
  cutting, locking in the dead structure. So a new pass (`src/deadweight.py`)
  runs BEFORE the model and removes the sentences that do no work —
  topic-announcement openers ("X has transformed the landscape of Y"),
  empty-optimism closers ("… continues to evolve rapidly", "the future is
  bright"), and pure throat-clearing ("It is important to note that …"). It is
  conservative: a sentence with a number or two-plus proper nouns is protected,
  a lone sentence is never cut, and a paragraph is never emptied. The result
  lists exactly what was removed. On the protein-ML example, a five-sentence
  paragraph drops to the two that carry content before it's even rewritten.
  `deadweight.trim`; `humanize_text(delete_first=…)`;
  `experimental.humanize_text_delete_first` (default true).

- **Humanize diagnostic pass — "reads empty, add the specifics".** A humanizer
  can strip the machine's tics but can't invent what the writer never said, and
  the absence of concrete detail is the biggest tell. So the result now ships a
  deterministic **"specify" pass** (`src/vagueness.py`): it finds vague, abstract
  claims in the source — "significant improvements", "a variety of", "researchers
  have shown", "recently" — and turns each into a question ("By how much? Give
  the number.", "Which study or source?", "When, specifically?"). It never fills
  them in (that would be a bluff); it asks. Suppressed when a real number is
  already in the sentence, so a concrete claim isn't nagged. This turns the tool
  from a rewriter into an editor. `vagueness.find/prompts/segments/count`.

- **Humanize reads more human — dashes killed, side-by-side compare, instant
  feedback.**
  - **No more long dashes.** The AI-tell detector only counted em-dashes with
    spaces around them, so a tight `word—word` slipped through — uncounted,
    unhighlighted, and left in the output (the score even claimed "0" while
    dashes remained). Fixed: any em/en dash is now detected, and a deterministic
    pass (`Cleaner._normalize_dashes`) rewrites every long dash to human
    punctuation — a comma, or "to" for number ranges — so **no em-dash ever
    survives**, whatever the model does. Meaning-preserving; a hyphenated
    compound (`well-tested`) is left alone.
  - **Broader cleanup.** The detector (and the prompt) now also flag stiff
    comma-led connectors ("Additionally,", "However,", "Consequently,", …), so
    they get stripped too.
  - **Side-by-side compare.** The result panel shows Original vs Humanized in two
    columns, next to the existing inline "What changed" diff.
  - **Instant, in-place feedback.** Clicking Humanize now shows a "Humanizing…"
    state and drops the result into its own box below without a full-page reload
    (progressive enhancement — a plain POST still works with JS off). Fixes the
    "it did nothing" feel on longer text.

- **Prompt-Engineering techniques — Simple / Reflection / Chain-of-Thought.**
  PE mode used to do one thing: clean a dictation into a faithful, well-phrased
  request. It now has a **Technique** selector (Settings → Vibe, or
  `prompt_engineering.style`):
  - **Simple** — the original faithful rewrite (default, unchanged).
  - **Reflection** — wraps your request in Draft → Reflect → Refine steps, so the
    receiving agent drafts, critiques its own draft, then rewrites.
  - **Chain-of-Thought** — wraps it in brainstorm → methodology → score → build
    steps, so the agent reasons before answering.

  The two scaffolds deliberately *expand* the prompt (guard bypassed, as PE mode
  already is) but keep your actual task faithful — the "invent no requirements"
  rule still holds, extended to concrete specifics (budgets, counts, dates) and
  ALL-CAPS/echo artifacts a small local model tends to add. Composes with the
  existing audience (claude-code / chatgpt / generic) and provider settings.
  `build_pe_prompt(audience, provider, style)`, `PE_STYLES`,
  `normalize_pe_style()`.

- **Humanize — paste AI-written text, get a human version back**
  (`Cleaner.humanize_text`, dashboard → **My Voice → Humanize**). Paste prose a
  language model wrote and get it back reading like a person. Separate from the
  dictation "My Voice" pass, which only nudges text the user already wrote — a
  genuine de-AI rewrite deletes LLM vocabulary, dropping token overlap to ~0.15,
  far under that pass's 0.35/0.85 floors, so it declined every real rewrite.
  This is its own method, prompt, and guards; the dictation path is untouched.

  **Three selectable targets** (no writing samples required to start):
  - **A natural human** — strip the AI tells (em-dash rhythm, *delve / moreover
    / a testament to*, "it's not just X, it's Y", tricolons, hedging stacks) and
    return plain natural prose. The default; needs no setup.
  - **Me** — additionally match your writing samples. With none it falls back to
    the natural-human rewrite and says so, rather than refusing.
  - **A specific tone** — casual, professional, friendly, plain, confident, or
    concise, chosen from a dropdown.

  **It always returns a result.** A risky-but-readable rewrite (a number
  changed, meaning drifted a little) is shown *with a warning* rather than
  dropped; only genuinely broken output (a preamble, injected markdown, a merged
  or ballooned paragraph, off-topic text, or an echo of your writing samples)
  falls back to your original. The page shows a word-level diff of what changed.

  The guards came out of measuring the real local model, not theory:
  - **A paragraph at a time.** Handed a whole document, `qwen2.5:3b` merges
    paragraphs; rewriting each separately preserves structure structurally and
    lets one bad paragraph fall back without sinking the rest.
  - **Numbers are checked exactly, in both directions.** The benchmark caught
    the model turning *"caught 14 regressions before release"* into *"shows how
    solid the process is"* — fluent, close, and no longer true. A changed number
    is now surfaced as a warning on the shown rewrite.
  - **Voice-profile regurgitation is rejected** (voice mode). A small model may
    reproduce the samples' *subject matter*; detected per sentence, and a purely
    leading echo is trimmed rather than discarded.
  - **Prompt ordering is load-bearing.** With the profile appended last the
    model continued from it; the profile is delimited and the rules come last.
  - **Reasoning models are handled.** A thinking model (qwen3.5, deepseek-r1)
    spends its budget on `thinking` and returns empty `content`, which reads as
    a dead provider; this path sends `think: false`.

  `Cleaner.humanize_text` returns a `HumanizeOutcome(text, reason, warnings,
  changed, total)`. `experimental` keys: `humanize_text_model` (blank = the
  cleanup model; this pass runs on a button press, so a larger local model is an
  option — editable in **Settings → Experimental**), `humanize_text_timeout_sec`,
  `humanize_text_min_sim`, `humanize_text_max_chars`.

  Then, in the same cycle:
  - **Deterministic AI-tell detector** (`src/aitells.py`) — a pure, tested module
    that scores how much a passage still reads like a model (LLM vocabulary,
    em-dash rhythm, the "not just X" antithesis, hedging, throat-clearers). The
    page shows an **"AI tells: N → M"** score on every result and lists what
    still remains, so the rewrite is legible instead of a black box.
  - **Better output on the small model.** Few-shot before→after examples in the
    prompt, plus a budget-bounded **tell-polish second pass**: when a clean
    rewrite still scores tells, one focused "remove exactly these phrases" call
    that is kept only if it clears the same guards and strictly lowers the score.
    On the benchmark this took the previously-failing dense/technical case to a
    clean rewrite, and most cases to zero remaining tells.
  - **Auto model escalation** (`humanize_text_escalate_model`, default `"auto"`).
    When the main model mangles a paragraph, it retries once on the next-step-up
    installed model — chosen by size so it won't jump to one too big for the GPU
    — before falling back to your original. Verified live: the 3B's hardest case
    is rescued by `qwen3.5`.
  - **More control.** A **strength** selector (light / balanced / aggressive)
    that steers how far to rewrite: it scales the length budget, adds a steering
    line to the prompt, AND sets the model's sampling temperature (0.15 / 0.4 /
    0.75). Light stays stable (re-rolls barely move); aggressive samples hotter,
    so **Try again** gives a genuinely different take — measured 1/5 vs 4/5
    distinct re-rolls. The dictation pass keeps its fixed 0.2. Plus a **custom
    free-text tone** box (sanitized) alongside the presets.
  - **`scripts/eval_humanize.py`** — a committed quality benchmark (fixture
    corpus + `--check` release gate) that measures acceptance, tells-removed (via
    `aitells`), facts-kept, and voice contamination against the real model.
  - **Inline tell highlighting + broader detection.** The detector grew from ~65
    to ~130 tells (more LLM vocabulary, "plays a crucial role", "a wide range
    of", "in conclusion", "unlock the potential", "first and foremost", …). The
    result now marks any remaining tells *in place* (`aitells.segments`), and a
    "AI tells in your paste" panel shows exactly what the pass targeted — so both
    ends are legible, not just a number.

- **Local intent model — a regex-miss fallback for Action Mode**
  (`src/intent_model.py`, opt-in, **off by default**). Action Mode classifies a
  prefixed command with tight anchored regexes; that is high-precision but
  brittle to phrasing (*"launch spotify"* instead of *"open spotify"*, *"play
  some music"* instead of *"play music"*). When
  `experimental.action_intent_model` is enabled, a miss on an explicit
  (`"computer, …"`) command is retried through a local predictor that recovers
  common verb-synonym and filler phrasings. It is built around one locked
  safety invariant: **the model never fires a side effect the regex/allowlist
  wouldn't.** The predictor proposes only a handler name + a slot string, which
  is re-validated by `build_match()` through the *same* guards as the regex path
  (`_domain_to_url` / `_is_safe_url` / the `action_apps`/`action_folders`
  allowlists) — and it is *stricter* than the regex path, refusing an
  unconfigured app/folder at construction time. Today's predictor is a
  dependency-free keyword heuristic (no ML deps, no import cost); the module is
  the load-once seam where an embedding + logistic-regression head can be
  dropped in later via `set_predictor()`.
  - A **`shadow`** value for the flag logs what the model *would* have fired
    without executing it, so precision can be measured before the model is ever
    trusted to act. A new confidence floor (`action_intent_min_conf`, default
    `0.75`) and a cheap length pre-gate keep it conservative.
  - **Offline eval harness** `scripts/eval_intent.py`: scores the predictor on a
    labeled fixture set (precision / recall / F1 + a `min_conf` sweep and
    confusion of misses), and a `--check` gate that fails CI if precision or
    recall regress — the empirical basis for the `0.75` default. Covered by
    `tests/test_intent_model.py` (safety re-validation, recovery, abstain, floor,
    never-raises) and `tests/test_main_intent_model.py` (off-by-default, live
    recovery, shadow-does-not-execute, and *unconfigured-app-can't-launch*).
  - **Dashboard control.** Settings → Experimental now surfaces the fallback as
    an Off / On / Shadow select plus a confidence-floor field, so it is reachable
    without hand-editing `config.yaml`. The tri-state maps to real YAML types
    (`false` / `true` / `"shadow"`) — "off" writes a boolean, never the truthy
    string `"false"` — and the floor is validated to `0–1`.
  - **Learned model backend** (`action_intent_backend: model`). Beyond the
    keyword rules, a tiny embedding + logistic-regression head
    (`src/intent_classifier.py`) generalizes to phrasings no rule anticipated —
    *"hush"* → mute, *"make a memo that…"* → note, *"the thing I just copied"* →
    clipboard — by embedding the utterance with the app's existing local
    sentence-transformers model (`retrieval.embed`, 384-dim, CPU) and classifying
    the intent. It emits only a handler + slot, so it flows through the same
    `build_match` guards as everything else (an unconfigured app it proposes
    still resolves to nothing). No new dependencies (numpy LR, ~13 classes), and
    it trains out-of-the-box from a shipped seed corpus (`src/intent_seed.py`) —
    a fresh install works with zero user data; the artifact is cached lazily to
    `data/intent_model.npz`. `scripts/train_intent.py` (`--train` / `--eval` /
    `--probe`) builds it, measures stratified-holdout accuracy (~0.83 on the
    seed) to tune the model floor (`action_intent_model_min_conf`, default
    `0.4` — the diffuse 13-class softmax sits lower than the keyword floor), and
    can sharpen it by mining the user's own `voice_actions` history. Covered by
    `tests/test_intent_classifier.py` (LR train/serialize, slot extraction,
    predict/abstain/never-raise, backend selection, and the same
    unconfigured-app-can't-launch safety proof, all with a fast fake embedder).
- **Automated signed-release pipeline.** A new `release` GitHub Actions workflow
  (`.github/workflows/release.yml`) builds the daemon installer on a tagged
  push (`v*`): PyInstaller → Inno Setup → SHA256 → draft GitHub Release with the
  installer + checksum attached. Code signing is an **opt-in step** that
  activates automatically when a `CODESIGN_PFX_BASE64` secret is present — ship
  unsigned today, drop a cert in later with zero workflow changes. A
  `workflow_dispatch` path does a dry-run build without creating a release. The
  job also fails fast if the tag doesn't match `src/__init__.py` `__version__`.
  Runbook in [`installer/RELEASING.md`](installer/RELEASING.md).
- **winget manifest.** `packaging/winget/` ships a schema-1.6.0 manifest
  (`JOhnsonKC201.EchoFlow`) so the app can be installed with
  `winget install JOhnsonKC201.EchoFlow` once published. Per-release update and
  submission steps in [`packaging/winget/README.md`](packaging/winget/README.md).
- **Lightweight web installer** (`installer/EchoFlow-Web-Setup.iss`). A tiny
  per-user bootstrapper that downloads the daemon payload from the GitHub
  release at install time (SHA256-verified, progress bar) and extracts it,
  instead of bundling hundreds of MB. Shares its AppId and install location
  with the full installer, so both resolve to one installed product. The
  release workflow now publishes three assets: the full offline installer, the
  web installer, and the `EchoFlow-Daemon-Payload-<ver>.zip` it fetches.
- **Opt-in self-update check** (`update.check_on_startup`, default **off**).
  When enabled, the daemon makes a single anonymous GitHub Releases API call at
  launch and shows a tray toast if a newer version exists — no history, config,
  or identifiers are ever sent (`src/update_check.py`). The /privacy ledger is
  updated to report this honestly: with the check on, it no longer claims zero
  egress and names the endpoint. Fully covered by `tests/test_update_check.py`.

### Changed
- The My Voice page's "Try it" box is now the **Humanize** workspace. It
  previously previewed the light-touch dictation pass, which is not what the
  page is used for; the shadow-preview table already answers "should I trust
  this for dictation?" with real data. `POST /myvoice/preview` is removed along
  with it rather than left as an unreachable endpoint.
- Installer version is now single-sourced. Both `.iss` scripts honor an
  `iscc /DMyAppVersion=<ver>` override (CI passes the tag); the hardcoded
  `#define` is now just a local-build fallback. Fixed the stale repo URL in
  `installer/EchoFlow.iss`.

## 0.2.0 - 2026-06-17

The dashboard era: a full local web dashboard, the casing-control system,
experimental voice Action Mode, opt-in cloud cleanup, and a hardening pass
across the daemon lifecycle.

### Added
- **Casing control.** Echo now learns a word's canonical casing from a single
  Fix-dialog edit (`tiktok` → `TikTok` sticks forever) and aggressively flattens
  spurious Title-Casing where Whisper/the LLM capitalized every word. Known
  proper nouns are protected: learned casings, Dictionary terms, a bundled list
  of common brands/places/names, and `I`. View and remove learned casings on the
  Dictionary page. On first run the canon is seeded from past edits in history.
  Config under `cleanup.casing`: `flatten_titlecase`, `learn_from_edits`,
  `protect_common_nouns` (all default on).
- **Add a casing from the dashboard.** The Dictionary page now has an "Add
  casing" form, so you can teach `GitHub`/`iPhone` directly without waiting for
  a dictation to fix. Re-adding a word overrides its canonical form (doubles as
  an edit), and each entry shows its reinforcement count.
- **Cloud cleanup opt-in (`cleanup.allow_cloud_cleanup`).** You can now route
  *every* dictation's cleanup through a cloud provider (Groq / Anthropic) instead
  of local Ollama, trading the local-only guarantee for cleanup quality. Off by
  default; when on, a missing API key or a failed cloud call falls back to local
  Ollama so dictation never breaks. PE mode and the teacher loop already used the
  cloud — this extends it to regular cleanup. (Set `cleanup.provider: groq` +
  `cleanup.allow_cloud_cleanup: true`, and export `GROQ_API_KEY`.)
- **Phase 14 — Action Mode** (`experimental.action_mode`, off by default).
  Semantic voice actions behind the shared `"computer"` prefix: `open_app`
  (allowlisted `action_apps` map, no shell-from-voice), `open_url`
  (http/https/mailto only), and `web_search`. Command Mode runs first and
  falls through to Action Mode on a no-match. Every attempt is logged to the
  new `voice_actions` table.
- **Phase 14 PR 2** — the deferred Action Mode handlers plus security
  hardening of the shipped trio. New handlers: `summarize_focused` (local
  Ollama only, never a cloud call; reads the focused pdf/txt/md/docx),
  `draft_event` (writes a local `.ics` draft — never a calendar API), and
  `quick_note` (appends to the notes store). Adds the `focused_document_path()`
  Win32 injector helper. Hardening: `_is_safe_url` now rejects userinfo
  spoofing, percent-encoded control chars, IDN homographs, and `mailto:`
  header injection; `_RE_DOMAIN` is ASCII/TLD-anchored; `open_app` validates
  target shape and the `os.startfile` fallback is restricted to alias-shaped
  tokens; action args are redacted in the log unless
  `experimental.action_log_verbose` is set.
- New Echo Flow feather logo applied across the app: `assets/icon.png` +
  `assets/icon.ico` (exe / window icon), dashboard favicon, and the dashboard
  sidebar brand mark (`/static/logo.png`).
- Expanded notification-sound catalog. `sound.list_choices()` centralizes a
  curated set of Windows Media WAVs + system aliases (30+), surfaced as the
  picker in Settings → System for the start / stop / error cues with per-entry
  availability and a Test button. Users can still type any other WAV/alias.
- Whisper decoder biasing via `initial_prompt` built from custom
  vocabulary + snippet expansions + personal vocabulary.
- Polish eval harness at `tests/eval/` (30 cases) plus ASR eval stub.

### Changed
- The dashboard window now opens **maximized** (fills the screen) instead of a
  centered 1280×820 window. The saved size is kept as the restore-down size.
- Health-check route documented correctly as `/api/healthz` (README and
  PRODUCT_OVERVIEW previously said `/healthz`).
- **`reload_config` is now atomic.** All config-derived values
  (vocabulary/initial_prompt, PE block, learner trust flags, cleaner config) are
  computed before any are applied, so a failure mid-reload leaves the previous
  config fully intact instead of half-applied.
- **Local-only enforcement.** Removed Groq / Anthropic / OpenAI cleanup paths
  from the default path, removed `src/transcribe_cloud.py`, removed the Groq
  HTTPS pre-warm, and removed auto-phasing to cloud providers. Cloud API keys in
  the environment are logged and ignored unless an opt-in cloud feature is
  explicitly enabled.
- Polish LLM: `qwen3.5:latest` (~6.5 GB) → `qwen2.5:3b-instruct-q4_K_M`
  (~2 GB) for VRAM headroom on 8 GB cards. Eval score went up
  (50/60 → 56/60 with a tighter default system prompt).
- The test suite now collects on headless/dep-light machines: `sounddevice` and
  `pynput` are lazy-imported, and `tests/conftest.py` stubs leaf native shims
  when absent. Added a fast minimal-deps CI lane.
- **Dev/test dependencies split into `requirements-dev.txt`** (`pytest`,
  `pytest-mock`, `pytest-cov`, `pytest-timeout`); the runtime `requirements.txt`
  no longer carries test tooling. `scripts\setup.bat` and `scripts\run_tests.bat`
  now operate from the repo root (they previously created/activated a venv inside
  `scripts\`).

### Fixed
- **Casing now survives every fallback path (full-system audit, 2026-06-03).**
  Whisper's "Every Word Capitalized" output previously reached the user
  unflattened whenever cleanup took a raw-passthrough exit: the hallucination
  guard (model went off-track), total provider failure (all providers down),
  and the `learned` provider with `fallback_to_ollama: false`. All three now
  run the LLM-free, content-preserving casing/punctuation pass — your words are
  kept verbatim, only the casing is normalized. (Root cause of the "sometimes
  capitalized, sometimes correct" reports; note a running daemon must be
  restarted to pick up the fix.)
- **Settings pages reflect the live theme.** The five `/settings/*` panels
  captured the theme once at startup, so a light/dark toggle made elsewhere
  wasn't shown on those pages until restart. They now read the current theme on
  every render like the rest of the dashboard.
- **A failing hotkey callback no longer silently kills dictation.** If
  `recorder.start()` raised (e.g. the mic was unplugged mid-session), the
  exception escaped into pynput's listener thread and stopped *all* hotkey
  detection with no indicator. Activate/deactivate callbacks are now guarded
  and logged, so the listener survives.
- **Semantic backlinks link the right dictation.** `notes.backlinks_for` used
  the retriever's match then re-looked-up the row by `raw_text` (not unique —
  repeated utterances collide), occasionally attributing the wrong dictation.
  It now uses the matched row's real primary key.
- **Curly-apostrophe casings are learned.** `_meaningful_casing` only stripped
  the ASCII `'`, so a correction like `TikTok’s` (Whisper's U+2019) was rejected
  and the casing never stored. All apostrophe glyphs are stripped now.
- **Scratchpad-target route hardened.** The `back` form field is restricted to
  same-site relative paths (no open-redirect / protocol-relative `//evil`), and
  the flash message is URL-encoded so values with `&`/`#`/`=` can't split the
  redirect.
- **Casing robustness pass.** Deterministic polish no longer corrupts
  internal-caps brands during sentence-capping (`iOS`→`IOS`, `mRNA`→`MRNA`,
  `macOS`, `iPhone15` are preserved); acronym comma-lists (`SQL, iOS, GDPR`)
  keep their commas instead of being flattened by the comma-storm heuristic;
  the storm pass no longer splits internal-caps words (`TikTok`→`tikTok`);
  abbreviations (`U.S.`, `e.g.`) are not treated as sentence ends; curly/smart
  apostrophes (’ ‘) are handled like ASCII for possessives; sentences capitalize
  correctly through opening brackets/quotes, a leading apostrophe (`'twas`), and
  a unicode ellipsis (`…`); non-Latin scripts (`Étienne`, Cyrillic) are
  capitalized/flattened Unicode-aware; and honorifics (`Dr.`/`Mr.`/`Ms.`)
  survive the flattener while names after a title are capitalized.
- `add_casing` (dashboard) now strips a trailing possessive so `London's`
  teaches `London`, enforces an 80-char server-side cap, accepts digit-bearing
  tokens (`iOS17`), and only bumps the reinforcement count on a same-form
  re-add (a corrective edit is no longer counted as reinforcement). The
  `_flatten` possessive path also matches an ALLCAPS `'S` suffix, matching the
  canon path.
- **Possessives keep their casing.** `London's`/`Sam's` are no longer flattened
  to lowercase — the de-Title-Case pass now strips a trailing `'s`/`'` before
  the protected-word lookup. Learned casings also apply through the possessive
  (`tiktok's` → `TikTok's`).
- Successful cleanup output (LLM and fallback paths) is now casing/punctuation-
  normalized — previously only the skip-clean fast path was, so model
  Title-Casing could reach the paste buffer untouched. Raw-on-failure,
  `provider: none`, and user-defined transform outputs are left verbatim.
- **Watchdog defers relaunch when a stale PID file can't be removed.**
  Relaunching over an unremovable PID file left it on disk, where a recycled OS
  PID (Windows reuses PIDs) could later read as "alive" and mask a genuine
  crash. The watchdog now skips that tick and retries on the next poll.
- **`open_action_items` has a stable order.** It now orders by
  `(created_at DESC, id DESC)`, so action items extracted within the same second
  no longer shuffle between dashboard requests.
- **"Find similar" surfaces the closest neighbours even when none are positively
  correlated.** `similar_to_id` defaulted to a `0.0` cosine floor, silently
  dropping negatively-correlated rows and contradicting its "always surfaces the
  closest matches" contract; it now uses cosine's `-1.0` floor and lets the
  per-row similarity inform the UI.

### Removed
- Vestigial `ruvector.db` at repo root. No `src/` module referenced it;
  the active vector store is the `embedding` BLOB column on the
  `dictations` table in `data/history.db`.

## 0.1.0 - 2026-05-20

First numbered version. The day a lot happened.

### Added
- Self-grading layer (`src/grade.py`): every dictation gets a 0–100 quality score from four signals (Whisper confidence, hallucination guard, semantic coherence, pattern coverage). Stored alongside each row.
- Self-improving loops: online weight calibration via SGD against user-edited dictations + exponential pattern decay (14-day half-life) so old jargon fades.
- LLM-free `learned` cleanup provider: uses past corrections + learned token substitutions + deterministic polish. Falls back to Ollama when not confident.
- Four-phase auto-progression: Bootstrap (Groq) → Hybrid (local Whisper + Groq cleanup) → Independent (local + Ollama) → Self-Sufficient (no LLM).
- Re-paste hotkey (default Ctrl+Shift+Win): re-pastes the most recent dictation in the focused window. Fires on release to avoid modifier-key interference. Cached in RAM to beat the async DB write race.
- Snippet expansion: short codes (btw, fyi, lgtm, ttyl, ...) expand post-cleanup. Case-aware, word-boundary safe.
- A/B provider shadow testing: runs primary + alternate cleanup providers, grades both, logs the winner. Opt-in via `cleanup.ab_test.enabled`.
- Knowledge graph: D3.js force-directed visualization with Notes mode (default when notes exist), Dictations mode, Concepts mode. Tag filter chip cloud, search box, quality slider with green/amber/red rings, refresh button, time slider.
- Notes layer: pinning promotes a dictation to a long-lived knowledge object with title and description.
- Tags: three-signal auto-suggestion (cluster, similar, concept) with manual confirm. Persists to `dictation_tags`.
- Action items: regex-based extraction of TODO-style phrases. Blocklist for daily drivel (`go to bed`, `eat lunch`). Silent — only surfaces in the editor.
- Review queue: tray menu opens a worst-quality-first list of un-edited dictations.
- Pin last dictation: tray menu shortcut to promote the most recent dictation to a Note.
- Editor extensions: tag chip row with accept/reject, manual tag entry, pin button, action items checklist.

### Changed
- Embedding model: `paraphrase-multilingual-MiniLM-L12-v2` (118 MB) → `all-MiniLM-L6-v2` (22 MB, ~3x faster). Existing embeddings auto-rebackfilled on startup via `embedding_model` column check.
- Language scope: English only. Removed Spanish and Nepali style prompts, language override menu, and multilingual filler-word lists.
- Logging: critical startup events (Phase banner, Whisper backend choice, Ready event, Re-paste hotkey, per-dictation raw/cleaned) now reach `data/wispr.log` even when running silent via VBS.

### Fixed
- Race condition where Ctrl+Shift+Win pasted the previous dictation instead of the most recent one (async DB write hadn't committed). RAM cache now sourcing.
- Synthetic Ctrl+V from re-paste landed mangled by user's still-held physical modifiers. Re-paste now fires on key release with a 60 ms safety delay.
- Dictation hotkey vetoes when Win is added mid-press — recording silently aborts instead of leaving stale audio.
- TF-IDF cluster labels duplicated word stems (`Thank · Thank Thank`). Unigrams only now, with dedupe.

### Infrastructure
- 81 tests (up from 11 at the start of the day). Tests for actions, tags, notes, grading, snippet expansion, A/B logging, veto behavior, re-paste cache.
- Schema migrations are idempotent and additive. Five tables added without losing existing data.
- Folder reorg: dev/maintenance scripts moved to `scripts/`, `ruvector.db` moved to `data/`. Root has only the 5 user-clickable entry points.
- Distribution script (`scripts/prepare_for_distribution.bat`) produces a 0.21 MB clean copy with no personal data, no caches, no venv.
- Silent exception swallowers (`except Exception: pass`) in critical paths replaced with `_log.warning` calls for post-mortem visibility.
