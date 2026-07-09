"""Seed training corpus + label spec for the embedding intent classifier.

The classifier (`src/intent_classifier.py`) is trained on these labeled
utterances so the ML head works out-of-the-box with **zero** user data — the
`scripts/train_intent.py` trainer can additionally mine the user's own
`voice_actions` history to sharpen it, but a fresh install is already useful.

Labels are FINE-GRAINED intents (e.g. `media_next`, `volume_up`), not handler
ids, because a single handler like `media_key` needs the classifier to pick the
specific key. `LABEL_SPEC` maps each label to `(handler, fixed_slot)`:
  - `fixed_slot=""`  → a slotless action; the label fully determines the args.
  - `fixed_slot=None`→ a slotted action; the slot is extracted from the body by
    `intent_classifier._extract_slot` and re-validated by `build_match`.
  - the `none` label is the abstain class — plain dictation that must NOT fire.

The `none` class deliberately includes hard negatives that *begin* with a
trigger-ish word ("remember when …", "next quarter …", "the volume of a
sphere …") so the model learns the boundary and keeps precision high.
"""
from __future__ import annotations


# label -> (handler, fixed_slot). None fixed_slot = slot is extracted from body.
LABEL_SPEC: "dict[str, tuple[str, str] | None]" = {
    "open": ("open", None),                    # url-or-app, slot extracted
    "web_search": ("web_search", None),
    "quick_note": ("quick_note", None),
    "draft_event": ("draft_event", None),
    "summarize": ("summarize_focused", ""),
    "media_playpause": ("media_key", "playpause"),
    "media_next": ("media_key", "nexttrack"),
    "media_prev": ("media_key", "prevtrack"),
    "mute": ("media_key", "volumemute"),
    "volume_up": ("volume", "up"),
    "volume_down": ("volume", "down"),
    "clipboard": ("open_clipboard_link", ""),
    "none": None,                              # abstain
}


SEED: "list[tuple[str, str]]" = [
    # --- open / launch an app or a site -------------------------------------
    ("open spotify", "open"),
    ("launch spotify", "open"),
    ("start up notepad", "open"),
    ("fire up chrome", "open"),
    ("boot up the browser", "open"),
    ("load the calculator", "open"),
    ("open github.com", "open"),
    ("go to github.com", "open"),
    ("navigate to docs.python.org", "open"),
    ("take me to reddit.com", "open"),
    ("visit wikipedia.org", "open"),
    ("pull up my email", "open"),
    ("bring up the calculator", "open"),
    ("can you open spotify", "open"),
    ("please launch the terminal", "open"),
    ("open up the notes app", "open"),
    ("get me to stackoverflow.com", "open"),
    ("start the music player", "open"),

    # --- web search ---------------------------------------------------------
    ("search the web for pizza", "web_search"),
    ("google the best restaurants nearby", "web_search"),
    ("look up the weather forecast", "web_search"),
    ("search for python tutorials", "web_search"),
    ("find me a good pasta recipe", "web_search"),
    ("look up how to tie a tie", "web_search"),
    ("google directions to the airport", "web_search"),
    ("search google for cheap flights", "web_search"),
    ("look up the definition of ephemeral", "web_search"),
    ("find the nearest coffee shop", "web_search"),
    ("search for cat videos", "web_search"),
    ("google what time it is in tokyo", "web_search"),

    # --- quick note ---------------------------------------------------------
    ("take a note that the build is green", "quick_note"),
    ("jot down buy milk and eggs", "quick_note"),
    ("note that i need to call mom", "quick_note"),
    ("remember to water the plants", "quick_note"),
    ("write down the meeting is at three", "quick_note"),
    ("make a note to review the pull request", "quick_note"),
    ("add a note about the login bug", "quick_note"),
    ("note to self order more coffee", "quick_note"),
    ("remember the client wants blue not green", "quick_note"),
    ("quick note the printer is out of toner", "quick_note"),
    ("jot this down the wifi password is guest", "quick_note"),
    ("take down that we ship on friday", "quick_note"),

    # --- draft calendar event ----------------------------------------------
    ("create an event lunch with sam tomorrow", "draft_event"),
    ("schedule a meeting for monday at ten", "draft_event"),
    ("add a calendar event dentist on friday", "draft_event"),
    ("make an appointment with the doctor next week", "draft_event"),
    ("set up a meeting called sprint review", "draft_event"),
    ("create an event titled team offsite", "draft_event"),
    ("schedule a call with the client thursday", "draft_event"),
    ("add an event birthday party on saturday", "draft_event"),
    ("book a meeting for two pm", "draft_event"),
    ("put the product demo on my calendar for friday", "draft_event"),

    # --- summarize the focused document (slotless) --------------------------
    ("summarize this", "summarize"),
    ("summarize this pdf", "summarize"),
    ("summarize the document", "summarize"),
    ("give me the gist of this", "summarize"),
    ("tldr this page", "summarize"),
    ("sum up this file", "summarize"),
    ("give me a summary of this document", "summarize"),
    ("condense this for me", "summarize"),
    ("what does this document say", "summarize"),
    ("break down this document for me", "summarize"),

    # --- media: play / pause (slotless) -------------------------------------
    ("play", "media_playpause"),
    ("pause", "media_playpause"),
    ("play the music", "media_playpause"),
    ("pause the music", "media_playpause"),
    ("play some music", "media_playpause"),
    ("resume playback", "media_playpause"),
    ("pause it", "media_playpause"),
    ("hit play", "media_playpause"),
    ("play something", "media_playpause"),
    ("resume the song", "media_playpause"),

    # --- media: next --------------------------------------------------------
    ("next track", "media_next"),
    ("skip this song", "media_next"),
    ("next", "media_next"),
    ("skip", "media_next"),
    ("play the next song", "media_next"),
    ("skip this one", "media_next"),
    ("go to the next track", "media_next"),
    ("next song please", "media_next"),
    ("skip forward", "media_next"),

    # --- media: previous ----------------------------------------------------
    ("previous track", "media_prev"),
    ("go back a song", "media_prev"),
    ("last track", "media_prev"),
    ("previous song", "media_prev"),
    ("play the previous track", "media_prev"),
    ("go back one song", "media_prev"),
    ("the song before this one", "media_prev"),
    ("previous", "media_prev"),

    # --- mute ---------------------------------------------------------------
    ("mute", "mute"),
    ("mute the sound", "mute"),
    ("unmute", "mute"),
    ("silence", "mute"),
    ("mute it", "mute"),
    ("mute the volume", "mute"),
    ("turn off the sound", "mute"),
    ("silence the audio", "mute"),

    # --- volume up ----------------------------------------------------------
    ("volume up", "volume_up"),
    ("turn it up", "volume_up"),
    ("louder", "volume_up"),
    ("turn up the volume", "volume_up"),
    ("make it louder", "volume_up"),
    ("crank it up", "volume_up"),
    ("raise the volume", "volume_up"),
    ("pump up the volume", "volume_up"),
    ("increase the volume", "volume_up"),
    ("crank the tunes", "volume_up"),

    # --- volume down --------------------------------------------------------
    ("volume down", "volume_down"),
    ("turn it down", "volume_down"),
    ("quieter", "volume_down"),
    ("turn down the volume", "volume_down"),
    ("make it quieter", "volume_down"),
    ("lower the volume", "volume_down"),
    ("softer", "volume_down"),
    ("reduce the volume", "volume_down"),
    ("bring the volume down", "volume_down"),
    ("it is too loud turn it down", "volume_down"),

    # --- clipboard link (slotless) ------------------------------------------
    ("open the clipboard link", "clipboard"),
    ("open the link in my clipboard", "clipboard"),
    ("open clipboard url", "clipboard"),
    ("open the url from my clipboard", "clipboard"),
    ("open whatever is in my clipboard", "clipboard"),
    ("open the copied link", "clipboard"),
    ("go to the link i copied", "clipboard"),

    # --- none: plain dictation (must abstain) -------------------------------
    ("the meeting went really well today", "none"),
    ("i think we should refactor the parser", "none"),
    ("hello there how are you doing", "none"),
    ("my favorite color is a deep ocean blue", "none"),
    ("let me know what you think about the proposal", "none"),
    ("the quarterly numbers look strong this year", "none"),
    ("i had a great weekend at the lake", "none"),
    ("please review the attached document when you can", "none"),
    ("the weather is beautiful this afternoon", "none"),
    ("we need to talk about the budget soon", "none"),
    ("thanks so much for your help yesterday", "none"),
    ("the cat knocked over the plant again", "none"),
    ("i will send you the report by end of day", "none"),
    ("our team hit all of the sprint goals", "none"),
    ("the recipe calls for two cups of flour", "none"),
    ("i disagree with the conclusion in section three", "none"),
    # hard negatives — begin with a trigger-ish word but are plain dictation
    ("remember when we visited paris last spring", "none"),
    ("playing tennis on saturday sounds like fun", "none"),
    ("searching for meaning in life is hard", "none"),
    ("opening night was a huge success", "none"),
    ("the volume of a sphere is four thirds pi r cubed", "none"),
    ("next quarter we are planning to expand the team", "none"),
    ("i really love this song so much", "none"),
    ("she noted that the deadline had moved", "none"),
    ("find your own path in this world", "none"),
    ("turn the page when you are ready", "none"),
]


def labels() -> "list[str]":
    """Distinct labels present in the seed, in a stable order."""
    seen: dict[str, None] = {}
    for _, lbl in SEED:
        seen.setdefault(lbl, None)
    return list(seen)
