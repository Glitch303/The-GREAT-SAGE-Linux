# Great Sage

A local-first Linux desktop AI companion (Wayland), built around a 3D HUD
rather than a chat window. Everything runs on your machine: the model,
the speech recognition, and the voice.

- **Talk to it.** Hold one key from anywhere - including inside a game -
  say what you want, let go. Transcribed locally with faster-whisper.
- **It answers in a cloned voice**, synthesised locally with F5-TTS.
- **It does things.** Opens pages, launches applications, opens folders,
  searches the web and YouTube, reads your screen, reports free VRAM and
  disk, sets reminders.
- **A HUD, not a text box.** A Three.js scene that reacts to speech and
  to what it is doing, with a compact always-on-top overlay mode that
  clicks straight through to whatever is behind it.
- **Two looks.** Great Sage, and Raphael - a second scene with its own
  bloom, glare and water, switched from a dropdown. Each keeps its own
  settings.

Named for the skill in *That Time I Got Reincarnated as a Slime*, and it
addresses you as Master.

## What you need

| | |
|---|---|
| Linux | any recent distro, under a Wayland compositor (developed on CachyOS + Niri) |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | a running `llama-server` (or a router such as llama-swap in front of it), with a GGUF loaded |
| Python 3.12-3.14 | the app itself, in a venv |
| WebKitGTK | the system web engine the HUD renders through (`libwebkit2gtk-4.1` + GIR) |
| NVIDIA GPU | not required, but F5-TTS is too slow to speak in real time on CPU. Developed on a 3060 (12GB) and a 5060 Ti (16GB) |

One venv is enough: the Windows build needed a second Python for the
overlay's PySide6 pin, which was a Windows ANGLE issue. See `NOTES.md`.

## Install

**[Full step-by-step guide, with troubleshooting -> INSTALL.md](INSTALL.md)**
Start there if anything goes wrong, particularly if it replies in text but
never speaks.

The short version:

```bash
git clone https://github.com/shogunyan12/The-GREAT-SAGE.git
cd The-GREAT-SAGE

# torch FIRST, with the CUDA build for your GPU - otherwise pip resolves
# the CPU build and the voice is unusably slow. cu128 covers everything
# up to and including Blackwell (RTX 50); older cards can use their own
# cu index. Check yours with nvidia-smi.
python3 -m venv .venv
.venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cu128
.venv/bin/pip install -r requirements.txt

# Then run a model server, e.g.:
llama-server -m /path/to/model.gguf --jinja -ngl 99 -c 32768
# it listens on http://localhost:8080 by default - where the app looks.
```

If your server is not on port 8080 - or sits behind a router such as
llama-swap that wants an API key - create
`great_sage/config/settings_local.py` (git-ignored; the committed
defaults stay the stock llama.cpp setup) with your
`LLM_BASE_URL` / `LLM_API_KEY` / `LLM_DEFAULT_MODEL`.

## Run

```bash
.venv/bin/python app.py
```

First launch downloads the F5-TTS and faster-whisper models (a few GB,
once). After that it is fully offline apart from web search and anything
you ask it to fetch.

## Use

| | |
|---|---|
| **Hold Ctrl+Alt+S** | Windows: hold to talk - key down opens the mic, key up sends. Change it in settings |
| **Alt+F5** (niri) | Linux: global voice key, works from any window - press once to listen, press again to send (a niri `binds` entry spawning `gs_hotkey.py`; see INSTALL.md) |
| **Click the core** | opens the radial menu: settings, chat, logs, overlay |
| **F1** | shows and hides the developer chrome |
| **Esc** | closes whatever is open |

Say things like *"what time is it"*, *"open spotify"*, *"search the web
for..."*, *"search on youtube for X and play the first video"*, *"look at
my screen"*. Requests to DO something are carried out and not narrated -
it opens the thing and says nothing.

## Themes

**Settings -> Theme** switches between **Great Sage** and **Raphael**.

Only the home visuals and the overlay change. The interface, the voice,
the chats and the keybinds are shared, and each theme keeps its own copy
of every visual slider - move one on Raphael and Great Sage is untouched,
switch back and your settings are still there.

Raphael is a different scene rather than a different palette: a
twelve-sided core, rings of turning script, and a screen-space bloom
stage that Great Sage never runs. That stage costs about six points of
GPU on a 3060 at 1904x1041, and only while Raphael is the active theme.

## Build a standalone exe

The standalone build is still the Windows two-bundle flow; Linux
packaging (a single PyInstaller build) comes with the rest of the port.

```bash
py build.py
```

Produces `dist/GreatSage/` (about 5.5 GB - it carries torch, F5-TTS and
Whisper) and puts a shortcut on your Desktop. Two bundles are built and
merged: the app on 3.14 and the overlay on 3.11.

The build runs three checks first and refuses to package if any fail:

```bash
py check_js.py         # the inline HUD script parses at all
py check_shaders.py    # GLSL template literals are balanced
py check_routing.py    # asking it to do something actually does it
```

`check_routing.py` is the important one. The failure it guards against is
not a crash - it is Great Sage confidently answering "I cannot do that"
to something it can do. Several cases in it are transcripts of real
requests that were refused.

## Configuration

`great_sage/config/settings.py` holds everything tunable: the model, the
system prompt, voice engine and speed, the global hotkey. Most of it is
also reachable from the settings panel in the app, which is the better
place to change it.

Your data - conversations, memories, API keys, settings - is written
beside the exe (or in the project folder when run from source) and is
never committed.

## Project layout

```
app.py                  entry point; checks prerequisites, then the HUD
run_hud.py              the native window and the WebSocket bridge
overlay_window.py       the transparent overlay + settings/history windows
hud_prototype.html      the entire HUD: Three.js scene, chat, settings
build.py                two-bundle PyInstaller build
check_*.py              the gates the build will not ship without

great_sage/
  config/settings.py    every tunable value
  models/               ModelProvider interface + llama.cpp, OpenAI, Anthropic
  core/
    chat_engine.py      conversation, tool loop
    tools.py            the 16 tools, and the deterministic pre-routing
    memory*.py          long-term memory
    state.py            internal state that shapes replies
    autonomy.py         scheduled tasks and folder watching
  voice/
    f5_tts_engine.py    cloned-voice synthesis
    speech_to_text.py   faster-whisper
assets/sfx/             interface sounds
voice_samples/          the voices Great Sage speaks with
```

`NOTES.md` has the history and the environment quirks. Read it before
touching `voice/` or `config/settings.py` - most of what looks like an
odd choice in there is load-bearing and the reason is written down.

## Known limitations

- **Wayland-specific features are compositor-dependent.** The
  always-on-top overlay needs a compositor that implements
  wlr-layer-shell (Niri, Sway, Hyprland do), and the global hotkey and
  window awareness use the wlr- / xdg- protocols most wlroots-family
  compositors provide. Developed and tested on Niri.
- **The model is your choice now** (any GGUF via llama.cpp). The default
  resolves to the first model the server reports. Small models are not
  reliable at deciding to use a tool, which is why the requests that
  matter are pre-routed deterministically rather than left to it.
- **First launch is slow** and needs the network, for the model
  downloads.
- **No test suite for the Python core.** The three checks above cover the
  HUD script and the routing table; the rest is verified by running it.
- **Image generation is not implemented**, deliberately.

## Credits

The pre-recorded voice lines under `voice_lines/` are audio from *That
Time I Got Reincarnated as a Slime*, used here for a personal companion
project. They are not mine and are included for that purpose only.
