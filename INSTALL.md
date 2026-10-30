# Installing Great Sage

Linux, under a Wayland compositor. Roughly 30 minutes, most of it
downloads. Developed and tested on CachyOS + Niri; the compositor-dependent
parts (overlay, hotkey, window awareness) use the standard wlr-/xdg-
protocols, so wlroots-family compositors should work as-is.

Three things live outside the app and cannot be bundled: **the model
server** (llama.cpp's `llama-server`, or a router such as llama-swap in
front of it), **the chat model** it loads, and the **WebKitGTK** system
library the HUD window renders through. The voice weights are a fourth -
they download themselves the first time Great Sage speaks. That is what
most of the size and most of the waiting is.

---

## Before you start

| | Why |
|---|---|
| **A Wayland compositor** | Niri, Sway, Hyprland, KDE - anything implementing the standard protocols |
| **An NVIDIA GPU** | Not strictly required, but the voice is unusably slow without one. Developed on a 3060 (12GB) and a 5060 Ti (16GB) |
| **~10 GB free** | App, chat model, and voice weights |
| **Python 3.12-3.14** | The app itself, in a venv |
| **WebKitGTK** | `libwebkit2gtk-4.1` + `gir1.2-webkit2-4.1` (or the webkitgtk-6.0 equivalents) |

---

## Step 1 - The model server and the model

Get [llama.cpp](https://github.com/ggml-org/llama.cpp/releases) (or build
it), a GGUF model, and run a server:

```bash
llama-server -m /path/to/model.gguf --jinja -ngl 99 -c 32768
```

It listens on `http://localhost:8080` by default - where the app looks.
`--jinja` is what makes tool calling and the thinking switch work.

Check it worked:

```bash
curl http://localhost:8080/v1/models
```

Your model's name should be in the JSON.

**Using a router such as llama-swap?** Point the app at it in
`great_sage/config/settings_local.py` (git-ignored):

```python
LLM_BASE_URL = "http://localhost:27906"
LLM_API_KEY = "..."                      # the apiKeys in its config
LLM_DEFAULT_MODEL = "one of its model names"
```

Leave the file out and the app runs against a bare `llama-server` on
port 8080 with the first model it reports.

---

## Step 2 - WebKitGTK

The HUD window renders through the system WebKit, and the voice plays
through its GStreamer pipeline - both are system packages:

```bash
# Arch / CachyOS:
sudo pacman -S webkit2gtk-4.1 gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-ugly gstreamer1.0-libav gstreamer1.0-pipewire
# Debian / Ubuntu:
sudo apt install libwebkit2gtk-4.1-0 gir1.2-webkit2-4.1 gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good gstreamer1.0-plugins-ugly gstreamer1.0-libav
```

Without the GStreamer plugins the window renders fine but is silent, and
the log shows `Timed out waiting for the HUD to finish playing audio`.
Audio follows the system's default output device (PipeWire/PulseAudio).

The app's startup check reports exactly what it found.

---

## Step 3 - The code

```bash
git clone https://github.com/shogunyan12/The-GREAT-SAGE.git
cd The-GREAT-SAGE
```

---

## Step 4 - The venv and PyTorch, before anything else

**The torch order matters.** Install torch on its own first, with the
CUDA build for your GPU. If you let `requirements.txt` resolve it, pip
gives you the CPU build and the voice is too slow to use.

```bash
python3 -m venv .venv
.venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cu128
```

`cu128` covers every NVIDIA card up to and including Blackwell (RTX 50).
Check which CUDA version your driver supports with `nvidia-smi` (top
right of the output) and adjust the index if you need a different one -
[pytorch.org/get-started/locally](https://pytorch.org/get-started/locally)
gives the exact command.

Then verify, before going further:

```bash
.venv/bin/python -c "import torch; print(torch.cuda.is_available())"
```

**This must print `True`.** If it prints `False`, stop and fix it here -
everything else will install fine and the voice will not work. See
troubleshooting below.

---

## Step 5 - Everything else

```bash
.venv/bin/pip install -r requirements.txt
```

---

## Step 6 - Run it

```bash
.venv/bin/python app.py
```

If something is missing, the startup check says what and how to fix it
(`.venv/bin/python -m great_sage.preflight` runs the same checks from a
terminal).

**The first reply will be slow.** Great Sage downloads the F5-TTS voice
model and the Vocos vocoder the first time it speaks - about 3.6 GB. It
is not frozen. After that it is local and fast.

---

## Using it

| | |
|---|---|
| **Hold Ctrl+Alt+S** | Windows: hold to talk - the key going down opens the microphone, the key going up sends. Change it in settings |
| **Alt+F5** (niri) | Linux: the global voice key, works from any window - press once to listen, press again to send. It is a niri `binds` entry, not an app key - see below |
| **Click the core** | the radial menu: settings, chat, logs, overlay |
| **F1** | show/hide the developer chrome |
| **Esc** | close whatever is open |

### Global voice key on Linux (niri)

The settings "voice key" button re-registers the key with `RegisterHotKey` - a Windows API that cannot claim any key on Wayland. So on Linux every rebind is reported as "already in use" while nothing is actually registered. On Linux the key lives in the compositor's config instead:

```kdl
# ~/.config/niri/dms/binds.kdl
Alt+F5 hotkey-overlay-title="Great Sage: voice" { spawn "/path/to/Great-Sage/gs_hotkey.py" "ptt_toggle"; }
```

niri binds fire on the key PRESS - the key-up never reaches the app - so the key is a toggle: press once to start listening, press again to stop and transcribe. The reply lands in the main HUD, the same route the wake word uses. Add as many more combos as you want: just add more `binds` entries spawning `gs_hotkey.py` (niri accepts three-button combos like `Ctrl+Alt+Shift+X` too, so the two-button space is not exhausted). `gs_hotkey.py` also takes `ptt_start` / `ptt_stop` if you prefer two separate keys.

---

# Troubleshooting

## No voice - it replies in text but never speaks

Almost always one of three things, in this order of likelihood.

**1. torch is the CPU build.**

```bash
.venv/bin/python -c "import torch; print(torch.cuda.is_available())"
```

If this prints `False`, that is the problem. Reinstall:

```bash
.venv/bin/pip uninstall torch
.venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cu128
```

**2. The voice weights could not download.**

Look in `great_sage.log` (next to the exe, or in the project folder when
run from source) for lines mentioning `huggingface.co`. If you see
`CERTIFICATE_VERIFY_FAILED` or `Hostname mismatch`, something on the
network is intercepting HTTPS - usually antivirus with HTTPS scanning
enabled, or a corporate proxy. Turn off HTTPS/SSL scanning for
`huggingface.co` and restart, or install on a different network.

This one is quiet: the app starts normally and simply never speaks.

**3. No NVIDIA GPU.** F5-TTS on CPU is too slow to speak in real time.
Great Sage falls back to a lighter engine and then to text-only rather
than hanging, so a machine with no GPU still works - just without the
cloned voice.

**If none of those:** `great_sage.log` records why the voice engine
failed on startup. Search it for `Voice` and the reason will be there in
plain English.

## The window never appears at all

If nothing opens, the most likely cause is a missing WebKitGTK (step 2)
- it is the thing that draws the window. Install the system packages,
re-run, and the startup check will confirm what it found.

## The window appears but is silent

The voice engine is working (the log shows audio being generated) but
nothing comes out of the speakers, and `great_sage.log` ends with
`Timed out waiting for the HUD to finish playing audio`. That is the
missing GStreamer plugins from step 2 - WebKitGTK plays the in-window
audio through them. Install the packages, restart, and it speaks.

## "Could not reach the llama.cpp server" / replies never arrive

```bash
curl http://localhost:8080/v1/models
```

- **Connection refused** - the server is not running. Start
  `llama-server -m <model.gguf> --jinja`, or your router.
- **401 unauthorized** - a router (llama-swap) is up but the key is wrong:
  check `LLM_API_KEY` in `great_sage/config/settings_local.py` against
  the `apiKeys` in its config.
- **200 but your model is not in the list** - set `LLM_DEFAULT_MODEL` in
  `settings_local.py` to one of the names the list contains.

## Push-to-talk does nothing

The global hotkey is registered through the compositor, not by the app
grabbing a key. On niri it is a `binds` entry (see `core/global_hotkey.py`
for the exact config line) - if another bind claims the same keys, the
compositor decides, not the app. Pick a different key in the settings.

Also check the **Microphone** setting is the right device.

## Overlay mode does nothing

The transparent overlay is the last piece of the Linux port (it needs the
compositor's layer-shell protocol). Until it lands, the main window works
fully and overlay mode is unavailable.

## It answers but will not open or search anything

Check `great_sage.log` for `Pre-routed tool`. If those lines are there
and it still did nothing, say so in an issue with the line - that is a
routing bug and there is a test file for exactly this
(`check_routing.py`).

---

## Where your data lives

Conversations, memories, settings and API keys are written **in the
project folder** (or next to the binary in a packaged build). Nothing is
sent anywhere. Deleting those files resets Great Sage to a fresh install.
