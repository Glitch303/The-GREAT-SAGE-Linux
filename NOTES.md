# Great Sage — Project Notes for Claude Code

This file summarizes everything decided and discovered while building Great
Sage in a prior chat session, so a fresh Claude Code session has full
context without re-deriving it. Read this alongside the actual code and
README.md — this file covers reasoning, environment quirks, and
troubleshooting history that aren't otherwise written down anywhere.

There's also a short `CLAUDE.md` in this folder — it's the terse,
always-applied version (core principles, environment gotchas, run/test
commands) that gets auto-loaded every session. This file is the detailed
narrative/troubleshooting history behind it; CLAUDE.md points here for
depth instead of duplicating it.

## Project

Great Sage is a local-first Windows desktop AI companion. Long-term vision:
persistent AI companion with conversation, computer assistance, memory,
voice, screen understanding, tools, coding assistance. **Current state is
still an early prototype** — text chat + voice output are working; nothing
else from the long-term vision is built yet.

## Core principles (apply to all future work on this project)

1. Local-first.
2. AI model must be replaceable — never hard-code to one provider.
3. Modular architecture; every major feature gets a clear interface/API.
4. Incremental features. Don't build ahead of what's needed.
5. Computer actions go through explicit tools, never unrestricted LLM
   access to the machine.
6. Before a major architectural decision, explain the approach first.

## Architecture as built so far

```
great_sage/                      <- outer project folder, any name is fine
  main.py                        <- entry point; wires config -> provider/voice -> engine -> UI
  requirements.txt
  README.md                      <- kept up to date with setup + "what's next"
  voice_samples/
    my_voice.wav                 <- user's own recorded voice sample for cloning
  great_sage/                    <- the actual Python package (must be named exactly this)
    config/
      settings.py                <- ALL tunable values live here
    models/
      base.py                    <- ModelProvider abstract interface (send_message, stream_response, get_available_models)
      ollama_provider.py         <- Ollama implementation (only provider so far)
    core/
      chat_engine.py             <- owns conversation history, drives a ModelProvider, provider-agnostic
    voice/
      base.py                    <- VoiceOutput abstract interface (speak, stop)
      tts_engine.py               <- pyttsx3/SAPI5 implementation (VOICE_ENGINE = "sapi5")
      pocket_tts_engine.py        <- Kyutai Pocket TTS voice-cloning implementation (VOICE_ENGINE = "pocket", current default)
      cloned_voice_engine.py      <- XTTS-v2 voice-cloning implementation (VOICE_ENGINE = "clone")
      voice_lines.py              <- shared voice-line trigger-matching logic, used by both cloning engines
    ui/
      cli.py                    <- terminal chat loop; prints + speaks replies
```

Not yet built (intentionally, per incremental principle): `memory/`,
`tools/`, `vision/`, `state/`, any GUI, voice **input** (speech-to-text).

### Interface pattern used throughout

Every swappable piece (AI provider, voice engine) is an abstract base class
in a `base.py` with a small number of methods, and concrete
implementations elsewhere. `main.py` is the only file that knows how to
build a concrete instance from `config/settings.py` — `core/` and `ui/`
only ever talk to the abstract interfaces. Follow this same pattern for
any new provider/engine/tool going forward.

## What's working right now

- Text chat with a local Ollama model (`llama3`), streamed token-by-token,
  session-only conversation history (no persistence yet).
- Voice output via Windows SAPI5 (`pyttsx3`) — default, always works,
  generic-sounding voice.
- Voice output via a **cloned voice** (the user's own, XTTS-v2) — works,
  but took significant troubleshooting to get running (see below). Set
  via `VOICE_ENGINE = "clone"` in `config/settings.py`.
- The cloned-voice engine now streams audio (plays as it's synthesized,
  not after the whole reply finishes) and caches the reference-audio
  voice conditioning once at startup instead of recomputing it every
  message — both were real latency/glitch fixes, not just tuning. See
  "Persona, language, and voice-line pipeline" below.
- A Great Sage / Raphael persona (from *That Time I Got Reincarnated as a
  Slime*) is live in `SYSTEM_PROMPT` — see that section for details and
  the current known blocker on the Japanese-speech half of it.
- Git version control — see "Version control" below.
- **Default voice engine is now `VOICE_ENGINE = "pocket"`** (Kyutai
  Pocket TTS), not XTTS-v2 — see "Pocket TTS integration" below. English
  only for now; the user decided they only need English (they can't
  understand Japanese themselves, only the specific pre-recorded voice
  lines they made). XTTS ("clone") is kept in the codebase as an
  alternative, not deleted.

## Persona, language, and voice-line pipeline

Added in a later session, on top of the original prototype above.

**Persona.** `SYSTEM_PROMPT` in `config/settings.py` now writes Great Sage
as the analytical AI from Rimuru's mind in *That Time I Got Reincarnated
as a Slime* (evolved toward its later form, Raphael): addresses the user
as "Master", opens every reply with the fixed word "Notice.", greets with
a fixed "Good morning, Master." line, stays to 1-3 sentences unless the
question genuinely needs more, and mostly refers to itself in third
person rather than "I". On-screen chat text is deliberately kept in
**English** — see next.

**English text, Japanese speech.** The user wants to read English but
hear Japanese. `CLONE_LANGUAGE = "ja"` controls what language XTTS
actually pronounces; `CLONE_TRANSLATE = True` (in `settings.py`) is the
bridge - `main.py`'s `build_translator()` wraps the *same* `ModelProvider`
already in use and makes a second, independent, history-free
`send_message()` call per reply to translate it into `CLONE_LANGUAGE`
right before speaking, without polluting the visible chat history. This
means a normal reply now costs two Ollama generations (one streamed and
visible, one for translation) - a real latency cost, not free.

**Voice-line audio triggers.** The user has (and is populating with their
own Japanese recordings) a ~949-file voice-line library at
`../sounds/` — a sibling folder *outside* this project, deliberately not
version-controlled (see "Version control"). Two files are wired up so
far: `sounds/voice/koku.ogg` ("Notice" in Japanese) and
`sounds/voice/kidou.ogg` ("Good morning, Master" in Japanese).
`config/settings.py`'s `VOICE_LINES` is a list of (regex, audio path)
pairs, matched against the model's **English** reply text (before
translation) and anchored to the start of whatever text remains to be
spoken. A match gets spliced out and played as a raw pre-recorded clip
instead of being synthesized - the remaining text still goes through
translation + XTTS normally. `VOICE_LINES_DIR` can be overridden via the
`GREAT_SAGE_VOICE_LINES_DIR` env var if the sounds folder moves.

**XTTS per-language character limits.** XTTS-v2 truncates/garbles input
past a per-language character count, and the limit is much stricter for
CJK languages than for English - the earlier "just try `ja`" test broke
specifically because of this. Japanese is 71 characters vs. English's
250. `cloned_voice_engine.py`'s `_CHAR_LIMITS` table hardcodes Coqui's
internal per-language limits (captured against `coqui-tts>=0.27` - recheck
if that version ever bumps significantly) and `_split_into_chunks()`
splits on sentence boundaries first, then hard-wraps anything still too
long, before every `inference_stream()` call.

**Known blocker: `cutlet` / `mojimoji` needs a C compiler.** Getting
`CLONE_LANGUAGE = "ja"` actually working requires the `cutlet` package
(Japanese text romanization for XTTS), which pulls in `mojimoji` - a
Cython extension with **zero precompiled wheels for any platform**
(confirmed via `pip install --only-binary=:all: mojimoji` — no matching
distribution). It must build from source, which fails on this machine
with `error: Microsoft Visual C++ 14.0 or greater is required`. Two ways
forward, presented to the user, not yet resolved as of this writing:
1. Install Microsoft C++ Build Tools ("Desktop development with C++"
   workload) — a multi-GB, admin-elevated install — then
   `pip install "cutlet>=0.4" "fugashi[unidic-lite]>=1.3" "soundfile>=0.12"`.
2. Fall back: set `CLONE_LANGUAGE = "en"` and `CLONE_TRANSLATE = False` in
   `config/settings.py` to keep speaking English until willing to install
   the build tools. The two Japanese trigger clips still work either way
   since they're raw audio, unaffected by this.

**Known caveat, unverified by ear:** pre-recorded clips play via
`sounddevice.play()`/`wait()`, while streamed TTS chunks are written into
a separately-held `sounddevice.OutputStream`. These are two different
playback mechanisms; whether switching between them mid-reply (e.g. a
clip immediately followed by translated speech) produces an audible gap
or glitch has not been confirmed either way. This only applies to the
XTTS ("clone") engine - Pocket TTS (the current default) sidesteps it
entirely, see below.

## F5-TTS integration (current default engine)

Pocket TTS (below) and a Qwen3-TTS experiment were both replaced by
[F5-TTS](https://github.com/SWivid/F5-TTS) on 2026-09-02, after the user
asked for ElevenLabs-grade cloning with at most ~1s of delay.

**Why it won.** Qwen3-TTS cloned beautifully but was unusably slow, and
the cause turned out to be architectural rather than tuning: it decodes
autoregressively, one token at a time, so it's memory-bandwidth-bound
and its cost scales with output length. Dropping 1.7B -> 0.6B changed
nothing, and the GPU sat at 94% util drawing only 99W of 170W - the
signature of a bandwidth wall, not a compute one. F5 is a
non-autoregressive flow-matching model, so it generates the whole
utterance at once. Measured head-to-head on this machine's RTX 3060,
same reference clip and lines, both warmed up:

| line   | Qwen3-TTS 1.7B | F5-TTS (NFE 8) |
|--------|----------------|----------------|
| short  | 7.62s          | 1.15s          |
| medium | 15.10s         | 1.49s          |
| long   | 40.34s         | 2.27s          |

That's ~11.5x faster on average, and F5 gets *relatively* faster on
longer text (up to 4.07x realtime) where Qwen stayed pinned at 0.27x.

**NFE is the speed/quality dial** and has no autoregressive equivalent:
8 -> 2.16s, 16 -> 3.94s, 32 -> 8.19s on one 9.5s line. The user A/B'd
all three and heard no difference, so `F5_NFE_STEP = 8`.

**Japanese was dropped.** The base F5 checkpoint mangles Japanese, and
the workaround (a blank `ref_text` so F5 self-transcribes) fails
because the bundled Whisper is English-only and transcribes a Japanese
clip into English, mismatching the vocab. Rather than carry a second
JA-specific checkpoint, the Japanese voice candidate and the whole
text/spoken language-switching UI were removed - `set_reply_language`
on ChatEngine, the two `set_*_language` websocket handlers, and the
LANGUAGE settings section all went with it.

**Also removed:** `great_sage/voice/qwen_tts_engine.py`,
`voice_samples/qwen/`, and `run_hud.py`'s `_build_speech_translator`.
The four candidate previews were re-rendered with F5 so the settings
picker plays what the engine will actually sound like.

See `great_sage/voice/f5_tts_engine.py`'s docstring for the reference
caching (`preprocess_ref_audio_text` split out of `infer_process` so
conditioning is computed once at startup, not per reply) and the
producer/consumer threading that overlaps generating chunk N+1 with
playing chunk N.

## Pocket TTS integration (previous default engine; still the fallback)

The user found [Kyutai's Pocket TTS](https://kyutai.org/blog/2026-01-13-pocket-tts/)
and asked to integrate it as an alternative to XTTS-v2. Confirmed via
their GitHub repo/HF model card: 100M parameters, ~150-200MB, **MIT
licensed** (the base repo), **CPU-only** (no CUDA/GPU install needed),
clones a voice from **~5-10s** of reference audio (much shorter than
XTTS's 15-30s), first audio in ~200ms, **English/French/German/Spanish/
Portuguese/Italian only - no Japanese**. Given the user only needs
English (see above), this was a clean fit. Package: `pip install
pocket-tts` (installed clean, no compiler/CUDA fuss at all - a nice
contrast to the XTTS dependency chain below).

**Architecture:** new `great_sage/voice/pocket_tts_engine.py`
(`PocketTTSVoiceOutput`), same `VoiceOutput` interface as the XTTS
engine. The voice-line trigger-matching logic (`_split_voice_lines`) was
factored out of `cloned_voice_engine.py` into a shared
`great_sage/voice/voice_lines.py` (`split_voice_lines()`), since both
engines now need it - this was a deliberate, justified refactor (real
duplication, not speculative). `main.py`'s `build_voice()` picks the
engine class based on `VOICE_ENGINE` ("sapi5" / "pocket" / "clone").

**API notes (verified by inspecting the installed package directly,
not just docs - see the pattern of getting burned by guessed APIs
earlier this session):**
- `TTSModel.load_model()` → model; `model.get_state_for_audio_prompt(path)`
  → a conditioning state dict; `model.generate_audio(state, text)` →
  one complete `torch.Tensor` (not a generator).
- There IS a real streaming generator, `generate_audio_stream()` - but
  `generate_audio()` already loops over it internally and
  `torch.cat()`s the result, so text is never truncated by length
  regardless of which one you call. Deliberately used the *blocking*
  `generate_audio()`, not manual chunk-streaming, specifically to avoid
  reintroducing the buffer-underrun choppiness XTTS's chunk-by-chunk
  `inference_stream()` caused. Each segment (whether a voice-line clip
  or generated speech) plays via a single `sd.play()`/`wait()` call -
  one playback mechanism for everything, unlike XTTS's split design.

**Voice-cloning weights are separately gated on Hugging Face.** The base
(non-cloning, stock-voice) model downloads freely; the voice-cloning
checkpoint requires accepting terms at
`huggingface.co/kyutai/pocket-tts` (a responsible-use agreement - no
cloning others' voices without consent, etc. - license is CC-BY-4.0,
permissive) and authenticating locally. **The modern CLI is `hf`, not
the deprecated `huggingface-cli`** - install location often isn't on
PATH, so call it by full path or persist login via
`hf auth login --token <token>` (non-interactive, avoids a masked-input
paste-corruption issue hit during setup - the interactive prompt failed
with real tokens twice before switching to `--token`). Login persists to
`~/.cache/huggingface/token`, so it's available process-wide, not just
one shell session.

**Reference-clip length matters a lot more than for XTTS - this was the
actual bug behind two rounds of "it sounds like gibberish."** Fed a
40-57 second reference clip (matching XTTS's 15-30s+ comfort zone) and
got screechy, garbled output both times, regardless of clip language.
Kyutai's docs say Pocket TTS wants **~5-10 seconds**. Confirmed
empirically: `get_state_for_audio_prompt()` took 7.5-12.6s to process
the 40-57s clips vs. **0.9-0.7s** for an 8s and a 6.5s clip - it's
processing the whole clip, not sampling a short snippet internally, and
the small 100M-param model isn't robust to a much-longer-than-expected
reference. Trimming to 8s fixed most of it; a fresh, cleanly-recorded
6.5s clip (`voice_samples/my_voice_clean.wav`, now the active
`CLONE_REFERENCE_AUDIO_PATH`) fixed it completely. **Takeaway: if
Pocket TTS ever sounds garbled again, check reference clip length
first, before anything else.** The original two backup recordings
(`voice_samples/my_voice.wav`, `voice_samples/my_voice_en.mp3`) are kept
around unused, in case they're wanted again for XTTS.

## Gotcha: the HUD's Web Audio graph vs. Chromium's autoplay policy

Cost a long debugging session on 2026-09-02, and the symptom is
misleading enough to be worth writing down.

**Symptom:** no speech at all, and `great_sage_hud.log` full of
`VoiceError: Timed out waiting for the HUD to finish playing audio`
after a 30s stall. TTS itself was fine - a websocket probe confirmed the
server was sending a valid, correctly-decoding WAV.

**Cause:** `hud_prototype.html` routes `voiceLineAudio` through
`createMediaElementSource(...) -> analyser -> audioCtx.destination` so
the ring pulses to real amplitude. Once an element is wired into a Web
Audio graph, its sound reaches the speakers ONLY through that graph.
Chromium starts every `AudioContext` **suspended** until a user gesture,
and a suspended context doesn't merely mute the element - the element
stops progressing, so **`ended` never fires either**. Hence the double
failure: silence AND a hung ack.

`audioCtx.resume()` was only being called from the two test buttons and
`sendChatMessage()`, so *typing* worked while **push-to-talk, the wake
word, and the startup greeting were all silent**. That asymmetry is what
made it look intermittent.

**Fixes (all three, they cover different gaps):**
1. `playServerAudio()` resumes the context before `play()` - the single
   choke point every audio path funnels through.
2. `run_hud.py` sets `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=
   --autoplay-policy=no-user-gesture-required` before `webview.start()`.
   Required for the startup greeting, which happens before any gesture
   can exist. Must be set before the WebView2 environment is created.
3. A first-`pointerdown`/`keydown` listener resumes the context as a
   backstop.

**Related lesson:** the original `play().catch(() => {})` swallowed the
rejection entirely, which is *why* this presented as "no sound, no
clue". That path now logs the reason and sends the ack anyway, so a bad
clip costs one clip instead of stalling the queue - and the ack is
latched, since a duplicate would unblock the NEXT clip early (Python
clears its wait flag before each send) and truncate it.

## Version control

Git was initialized at this folder (`GREAT SAGE/`) specifically, **not**
the outer `Project Sage` folder - `GREAT SAGE/` is the actual project
root (see the architecture diagram above), and rooting the repo here
naturally keeps the sibling `sounds/` voice-line library (949 files, not
project code) out of version control entirely, rather than needing to
gitignore it after the fact. `.gitignore` excludes `__pycache__/`,
virtualenvs, `.env` files, and `voice_samples/*.wav` specifically (a
`.gitkeep` there is tracked instead - the personal reference recording is
sensitive and machine-specific, so the folder structure is versioned but
the audio itself isn't). Commit identity (`user.name`/`user.email`) is
configured locally for this repo only, per the user's explicit request -
never set globally.

## User's environment (Windows specifics — relevant for any future debugging)

- Windows, PowerShell as primary shell.
- Path used during setup: `C:\Users\shogu\Desktop\Project Sage\GREAT SAGE`
  (the outer folder can be named anything; must contain `main.py` and a
  `great_sage/` package folder as siblings).
- Python 3.14.7, installed such that the bare `python` command hits the
  Microsoft Store stub and fails ("Python was not found..."). **Use `py`
  instead of `python` for all commands on this machine.**
- NVIDIA GPU. `torch==2.13.0+cu126` (CUDA 12.6) confirmed working with
  `torch.cuda.is_available() == True`.
- Ollama model is pulled as `llama3`, which Ollama internally lists as
  `llama3:latest`. `main.py`'s exact-match check against
  `settings.OLLAMA_DEFAULT_MODEL` doesn't account for the `:latest` tag,
  so it prints a harmless `[Warning] 'llama3' was not found...` on every
  startup even though it works fine. **Known cosmetic bug, not yet
  fixed** — worth fixing the match logic in `main.py` to strip/ignore the
  tag suffix.

## Voice cloning setup — dependency chain that had to be solved (XTTS only)

Everything in this section is specific to `VOICE_ENGINE = "clone"`
(XTTS-v2). The current default, `VOICE_ENGINE = "pocket"` (Pocket TTS),
has none of this - `pip install pocket-tts` and it just works, no CUDA
install order, no FFmpeg hunt, no license prompt. See "Pocket TTS
integration" above for its own (much shorter) setup notes.

Getting `VOICE_ENGINE = "clone"` working required resolving several
stacked issues, in this order. If voice cloning breaks again (e.g. after
a fresh install), check these in order:

1. **PyTorch must be installed with CUDA support *before* other
   requirements**, or `pip install -r requirements.txt` will pull in a
   CPU-only torch and voice cloning will be extremely slow.
   ```
   pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu126
   ```
   Verify: `py -c "import torch; print(torch.cuda.is_available())"` must
   print `True`.

2. **`coqui-tts` (PyPI package name, not `TTS`) conflicts with
   `transformers` 5.x** — it imports `isin_mps_friendly` from
   `transformers.pytorch_utils`, which was removed in transformers 5.
   Fix: pin `transformers>=4.57,<5`. This is already in
   `requirements.txt` with a comment explaining why — **don't remove
   that pin** without checking if upstream coqui-tts has fixed it.

3. **`coqui-tts` needs the `[codec]` extra** for audio I/O via
   `torchcodec`:
   ```
   pip install coqui-tts[codec]
   ```

4. **The Coqui CPML license prompt** appears once, the first time
   speech is actually synthesized (not at import time). It's a
   `[y/n]` terminal prompt agreeing to non-commercial terms. Answer
   `y` for personal use. Only prompts once per install.

5. **FFmpeg must be a specific kind of build**, or `torchcodec` (used by
   `coqui-tts` for audio decoding) fails with a long
   `Could not load libtorchcodec` traceback probing FFmpeg versions 4-9.
   Two separate gotchas stacked here:
   - Must be a **shared** build (ships separate `avcodec-XX.dll` /
     `avformat-XX.dll` / etc. files), not a **static** build (bundles
     everything into `ffmpeg.exe` alone, no DLLs). Gyan.dev's plain
     "full_build" is static; you need the one explicitly labeled
     "full-shared" / "-shared".
   - Must be a **stable release** in the 4-7 major version range — torchcodec
     does not support FFmpeg 8+ on Windows (only Mac/Linux). A "git
     master" nightly build (e.g. one reporting `avcodec 63`) is too new
     and its DLL filenames won't match what torchcodec's precompiled
     `libtorchcodec_coreN.dll` binaries expect.
   - Known-good direct download used during setup:
     `https://github.com/GyanD/codexffmpeg/releases/download/7.1.1/ffmpeg-7.1.1-full_build-shared.zip`
   - **Separately**: even with the DLLs present and FFmpeg's `bin`
     folder added to the Windows `Path` environment variable, Windows
     Python 3.8+ does **not** consult `PATH` when resolving a native
     library's DLL dependencies (this is a real, documented Python/Windows
     behavior change, not a mistake the user made). The only reliable
     fixes are `os.add_dll_directory(path)` called before the import, or
     placing the DLLs directly next to the loading `.dll` in
     site-packages.
   - **Status: user reported "i fixed it" but did not confirm the exact
     mechanism.** A partial code fix (adding
     `_register_ffmpeg_dll_directory()` to
     `great_sage/voice/cloned_voice_engine.py`, calling
     `os.add_dll_directory()` on the FFmpeg bin folder before importing
     `torch`/`TTS`) was in progress but may not have been finished or
     shipped in the last zip the user downloaded. **First thing to check
     if voice cloning breaks on a fresh machine/install: whether this
     fix made it into the user's actual working copy of
     `cloned_voice_engine.py`, and if not, finish adding it** (the
     partial version, reference to what it should look like, is
     reasoning above — a function that calls
     `os.add_dll_directory()` on FFmpeg's bin directory, using
     `shutil.which("ffmpeg")` to auto-detect it, called at the top of
     `XTTSClonedVoiceOutput.__init__` before the `torch`/`TTS` import).

## Reference audio

Active cloned-voice reference clip is `voice_samples/my_voice_clean.wav`
(~6.5s), configured via `CLONE_REFERENCE_AUDIO_PATH` in
`config/settings.py`. **Ideal length depends on the engine** - see
"Pocket TTS integration" above for why this matters more than expected:
Pocket TTS (current default) wants ~5-10s; XTTS-v2 ("clone" engine)
wants 15-30s. Two older recordings are kept as unused backups:
`voice_samples/my_voice.wav` (original, longer) and
`voice_samples/my_voice_en.mp3` (57s - too long for Pocket TTS, caused
garbled output).

## Explicitly declined / out of scope

- Cloning a specific named anime voice actor's voice was explicitly
  declined (real person's voice without consent) — the user pivoted to
  cloning **their own** voice instead, which is what's implemented.

## Discussed next steps (not yet started)

**Before any of the roadmap below**: a stabilization pass was agreed on,
since the voice pipeline changed a lot in one session and picked up real
technical debt (see "Persona, language, and voice-line pipeline" above) -
building voice input on top of it now would be building on sand.
Concretely: resolve the cutlet/Build-Tools decision, add a `pytest` suite
for the pure logic that already broke twice (`_split_voice_lines`,
`_split_into_chunks`, the char-limit table), and keep NOTES.md/README.md
in sync going forward instead of letting them drift again.

Once that's done, in rough priority order, per the original spec's roadmap:

1. **Voice input (speech-to-text)** — the user's actual next goal
   discussed in chat: wants to talk instead of type. Should follow the
   same interface-based pattern (new abstract base in `voice/` or a
   sibling, feeding transcribed text into the existing
   `ChatEngine.send_streaming()` — no changes needed to `core/` or
   `models/`).
2. Persistent memory (SQLite) — `memory/` module reserved but empty.
3. A second model provider (OpenAI/Anthropic) to prove out the
   `ModelProvider` abstraction.
4. Minimal desktop UI / system tray instead of terminal.
5. A first constrained tool (e.g. read a file) to start `tools/` on the
   right foundation — explicit, not open-ended computer access, per
   core principle 5.
6. Fix the cosmetic `llama3` vs `llama3:latest` startup warning in
   `main.py`.

## Working style notes from the prior session

- Explain proposed architecture before building it, especially for
  anything that adds a new module or a new heavy dependency.
- Test what's testable (interface contracts, error paths) even without
  the full runtime available; be upfront about what couldn't be verified
  end-to-end and needs the user to confirm.
- Keep `README.md` in sync with any new setup steps or config options —
  it's the source of truth for "how do I run this."

---

## Packaging: two bundles, two Pythons

`py build.py` produces `dist/GreatSage/`, and it builds **twice**, with
**two different interpreters**:

| bundle | spec | interpreter | contents |
|---|---|---|---|
| `GreatSage.exe` | `great_sage.spec` | Python 3.14 (default) | HUD, Ollama client, F5-TTS, Whisper, torch/CUDA |
| `overlay/GreatSageOverlay.exe` | `overlay.spec` | Python 3.11 (`.overlay-venv`) | the transparent overlay, PySide6 6.4.3 |

### Why it cannot be one bundle

The overlay needs **PySide6 6.4.3**. Everything newer flickers: Qt moved
QtWebEngine onto the ANGLE backend in 6.5.1, and translucent windows have
flickered on it ever since. But PySide6 only gained Python 3.14 support in
6.10, so **6.4.3 cannot be installed next to the app's interpreter at
all**, and the app is staying on 3.14 where its CUDA torch already works.

So the split is not a packaging preference — it is forced, and it mirrors
exactly how the app already runs from source, where `run_hud.py` launches
the overlay under `.overlay-venv`'s Python while the app stays on 3.14.

Migrating everything down to 3.11 was checked and would work — all twelve
dependencies have 3.11 wheels — but it buys nothing the split does not
already give, at the cost of reinstalling the whole CUDA torch stack.

### The trap this replaced

`great_sage.spec` used to list `overlay_window` and the PySide6 modules in
`hiddenimports`. That could never have worked: built on 3.14 it picks up
PySide6 6.10.1, so the packaged overlay would **flicker** — and only after
being watched for a while, long past the point anyone would look at the
spec. PySide6 is now in that spec's `excludes` (not merely omitted —
`preflight.py` imports it, and that alone pulls in all of Qt), which also
takes ~450 MB out of the app bundle.

### Consequences elsewhere

- `run_hud._overlay_command()` frozen → `overlay/GreatSageOverlay.exe`
  beside the main exe. It must **not** fall back to re-invoking
  `sys.executable --overlay`; that only works when both halves share an
  interpreter, which they now never do.
- `overlay_window._panel_command()` frozen → the overlay exe re-invoked
  with `--panel`, since spawning the `.py` has no interpreter to run it.
- `preflight.check_overlay_host()` has three paths: frozen (does the
  overlay exe exist), source-with-venv (ask **that** Python its Qt
  version), and plain source. Before this it imported PySide6 in-process
  and cheerfully reported 6.10.1 — a Qt the overlay never actually runs.

`build.py verify()` checks the shipped layout rather than the build log:
both exes, the HUD page and vendored three.js, voice assets, the Qt
WebEngine helper *and* its `.pak` resources (absent, the overlay starts and
dies with no window), plus the two that prove the split held — no Qt in the
app bundle, no torch in the overlay's.

### Two bundle-only failures, and why neither showed up in testing

Both were invisible from source and invisible in the build log. Each took
a full 5-minute rebuild to observe, so both now have a `verify()` check.

**1. Missing package METADATA (not modules) — the mute build.**
`torchcodec`'s module was bundled, so transformers' `find_spec` reported
it available; reading its version then raised `PackageNotFoundError`,
which transformers re-raised as `ModuleNotFoundError: Could not import
module 'pipeline'`. f5_tts's `from transformers import pipeline` failed,
the engine fell back to Pocket TTS (excluded), and the app ran **mute in
text-only mode with nothing on screen saying why**.

Bundling a module does not bundle its `.dist-info`. Fixed with
`copy_metadata(..., recursive=True)` over the voice stack.

Three wrong theories were chased first (`huggingface_hub`, `filelock`,
`transformers.models.auto` all "missing") — every one disproved by
reading the bundle's embedded `PYZ.pyz`, which holds 12,280 modules. The
loose files in `_internal/` are only half the bundle; anything concluded
from listing that folder alone is wrong.

**2. `torch.jit.script` needs .py SOURCE at runtime.**
`x_transformers/attend.py` decorates `softclamp` with `@torch.jit.script`
at import time, and scripting reads the function's source back via
`inspect.getsourcelines()`. A bundle carries `.pyc` only, so:
`OSError: could not get source code`. That is not an `ImportError`, so
the voice engine's guard did not catch it and **the whole app failed to
start** — worse than the mute build. Fixed with
`collect_data_files(pkg, include_py_files=True)` for the packages that
actually script something (`x_transformers`, `einops`; verified by grep).

**Diagnosing this class of bug:** the voice engine's import guard now
reports the whole `__cause__` chain (`_describe_cause_chain`). It used to
print only the outermost exception, which named `pipeline` — a module
that was present and not the problem. The real cause was three links
down. Without the chain, every theory is a guess.

### Two more bundle-only failures: no sound, and deafness

**3. Native libraries are not bundled with their package.**
`torchaudio.load()` routes through torchcodec, which dlopens
`libtorchcodec_core*.dll` / `_custom_ops*.dll` / `_image*.dll` from its
own directory. Only the `.dist-info` from fix #1 had been added, so first
synthesis died with `ImportError: No spec found for libtorchcodec_image`
and every reply arrived with **no audio**. Fixed with
`collect_dynamic_libs`, and `binaries` is now a real list in the spec
rather than a literal `[]` passed to Analysis.

**4. Non-Python data assets.** `faster_whisper/assets/silero_vad_v6.onnx`
was absent, and `transcribe(vad_filter=True)` needs it. Push-to-talk
recorded and ran, and Sage never heard a word. Fixed with
`collect_data_files('faster_whisper')`.

**Why #4 took so long to see: two threads were swallowing exceptions.**
`server.py` ran `ptt_recorder.stop` as a bare `threading.Thread` target,
and `WakeWordListener._loop` re-raised inside its own thread. An unhandled
exception in a thread goes to stderr, and a **windowed PyInstaller build
has no stderr** - so it went nowhere. The log showed faster_whisper
processing audio and then simply nothing. Both now log. `transcribe()`
also logs its result, including the empty case, which was previously
indistinguishable from silence.

**Build hygiene:** `_check_locked` now asks `tasklist` whether the exe is
running instead of probing the file - renaming a running .exe to its own
name SUCCEEDS on Windows, so the old check passed and the build then died
mid-way with `PermissionError` on an unrelated file in `_internal`.

### torchcodec, round two - and how the test lied twice

`collect_dynamic_libs('torchcodec')` collected 14 `.dll` files and MISSED
`libtorchcodec_pybind_ops.pyd`. PyInstaller treats `.pyd` as an importable
extension module, but torchcodec never imports it - it locates every
library by path:

    FileFinder(Path(__file__).parent).find_spec(lib_name)
    over EXTENSION_SUFFIXES + ['.dll', '.pyd']

So each binary must sit in the torchcodec package folder under its own
name. The fix copies every `.dll` and `.pyd` from torchcodec's directory
explicitly (25 files, including the image codecs jpeg8 / libwebp* / zlib /
libzstd, which are also loaded by name). The spec now FAILS the build if
that glob comes back empty, and `verify()` names the three files rather
than checking that "some libtorchcodec_* exists" - the vague check passed
while the `.pyd` was missing and the build shipped mute a second time.

**The testing trap, which cost two false "voice works" claims.**
An end-to-end test that sends a chat and counts returned audio bytes
proves nothing on its own: short replies match `VOICE_LINE_SETS` patterns
and play a PRE-RECORDED clip, bypassing F5 entirely. "Good morning,
Master." and "Not yet acquired." are both canned lines, and both returned
healthy-looking audio from a build whose synthesis was broken.

A real synthesis test must check the reply text against every pattern in
`VOICE_LINE_SETS` and require a miss. Ask for a long, specific sentence -
the lighthouse prompt gives ~18 words and ~326KB of audio, versus ~25KB
for a canned clip.

---

## Model selection (spec Phase 2, decided)

**Primary model: `qwen3.5:4b`.** Measured here, RTX 3060 12.9GB, with
F5-TTS resident at 0.8GB:

| model | VRAM | load | warm turn | spec S34 role test |
|---|---|---|---|---|
| qwen2.5:3b | 2.2GB | 4.2s | 1.2s | FAILS - "I will proceed with configuring" |
| qwen2.5:7b | 4.7GB | 11.7s | 1.4s | correct but terse |
| **qwen3.5:4b** | **3.1GB** | **4.2s** | **1.3s** | correct + volunteers the capability is absent |
| llama3 | 5.0GB | - | - | worse than 3b (ordering 1/5, sheep 3/5) |
| qwen3-vl:8b | 6.1GB | - | - | redundant; 4b already does vision |
| qwen3.5:27b | 17GB | - | - | does NOT fit this card |

`qwen3.5:4b` is multimodal - verified by handing it a real screenshot,
which it read correctly (named the application, a URL, and filenames). So
spec S41 screen awareness needs no second model and no extra VRAM.

3.1 + 0.8 = 3.9GB, leaving ~9GB free. That headroom was the actual
requirement: gaming and calls run alongside Great Sage.

**Do not switch to llama3.** It was recommended once on a single sample
and that was wrong - over 5 trials it lost to the 3B it was meant to
replace, at more than double the VRAM.

### The prompt mattered as much as the model

The reported symptom - Great Sage "seeming dumb", needing the same point
rephrased eight times - was reproduced and traced to the 7,208-character
system prompt, not only to model size. Same model, same three turns:

    long prompt  -> "I will proceed with configuring and testing to
                     fulfill your request"        WRONG - Master builds it
    compact      -> "you will implement web access into Great Sage
                     yourself"                    correct

The replacement is 2,925 characters and is NOT spec S13's generic text.
That would have silently deleted five load-bearing things: the trigger
phrases `voice_lines.py` matches to fire the recorded clips, the "as an
AI" ban, the instruction-integrity hardening, non-disclosure, and the
Daikenja identity. What was cut instead: the BAD/GOOD example pairs, the
expanded VOICE section, and rules restated three ways.

Verified equal or better, not merely shorter: identity leaks 0/6 versus
the legacy prompt's 1/6, role test 0/3 for both, trigger phrases still
firing. `SYSTEM_PROMPT_LEGACY` is kept - `set GREAT_SAGE_PROMPT=legacy`.

### Testing note: a chat test can lie

Counting returned audio bytes does NOT prove synthesis works. Short
replies match `VOICE_LINE_SETS` and play a PRE-RECORDED clip instead.
"Good morning, Master." and "Not yet acquired." are both canned, and both
returned healthy-looking audio from a build whose F5 path was broken.
A real test must send a SECOND turn (the first always triggers session
start) and check the reply against every pattern in `VOICE_LINE_SETS`.
Real synthesis is ~380KB for a 20-word sentence; a clip is ~25KB.

---

## Phase 3 started: chats persist, and user data left the bundle

`core/chat_store.py` + `save_chats`/`chats` over the WebSocket. The HUD
still owns the list; the store only writes it down, atomically (temp file
then `os.replace`), validating the payload because it arrives from the
page. The existing `chatTopics` system was extended, not replaced.

Restoring the sidebar deliberately does **not** select a chat: the
model's context starts empty on launch, so marking one active would show
a transcript the AI cannot actually remember.

Spec S7's three-dot menu replaces the per-row `X` - Rename, Summarize,
Remember this chat, Delete, with Delete asking for confirmation. An `X`
is a one-click destructive control sitting pixels from the row you click
to *select* a chat.

### The bug this uncovered: user data lived inside the app

`app.py` chdir's to `_MEIPASS`, so every relative path in `settings.py`
resolved **inside the bundle**. Memories, chats, HUD settings and
voice-line preferences were all being written to
`dist/GreatSage/_internal/`.

Every rebuild overwrites `dist/`. An installer update would have wiped
the user's memory and every conversation, silently.

    frozen  -> %LOCALAPPDATA%\GreatSage
    source  -> the repo folder (development unchanged)
    either  -> GREAT_SAGE_DATA_DIR overrides both

Only USER data moved. Application assets - the reference clip,
`voice_lines/`, `vendor/` - stay relative to the bundle; they ship with
the code and are not the user's to keep.

That override is the same hook spec S64 wants for a portable identity:
point it at an external drive and the memory follows the drive, not the
machine. Groundwork only - no sync implemented.

Verified on the packaged exe: chats written to
`%LOCALAPPDATA%\GreatSage\chats.json`, nothing left in `_internal/`.

### Editing note

Several edits to `settings.py`, `server.py` and `hud_prototype.html` were
corrupted by writing `\n` inside a bash heredoc - the escape is eaten and
the string literal breaks across lines. Build the text with `chr(10)` and
emit it via `json.dumps`, or splice by line index. Also: reading a file
through `sed 's/^/  /'` adds two spaces, so anchors copied from that
output will never match.

---

## Measured footprint and system requirements

All measured on this machine (RTX 3060 12GB, Ryzen 9 7900X, 32GB RAM),
not estimated.

### VRAM, attributed by measuring the delta each piece adds

| | VRAM |
|---|---|
| Great Sage app: HUD WebGL + F5-TTS + Whisper | **~1.1 GB** |
| qwen3.5:4b while loaded | **~3.8 GB** |
| **Total while answering** | **~4.9 GB** |
| Free on a 12GB card | **~7 GB** |

Ollama UNLOADS the model between requests, so the 3.8GB is returned while
Great Sage sits idle and only the ~1.1GB app footprint remains. That is
why gaming alongside it works: the heavy part is not resident unless a
reply is actually being generated.

### Disk

| | size |
|---|---|
| Great Sage application folder | 5.55 GB |
| ...of which torch | 4.08 GB |
| ...of which the overlay bundle | 0.36 GB |
| Ollama runtime | 1.58 GB |
| Models (qwen3.5:4b + qwen2.5:3b fallback) | 5.32 GB |
| Voice weights, downloaded on first use | 3.61 GB |
| **Total installed** | **~16 GB** |

torch is 74% of the application folder and is not reducible without
losing CUDA. `bitsandbytes` (~120MB) was excluded after verifying it is
never imported - importing the whole voice stack leaves it absent from
`sys.modules`, while pyarrow, llvmlite, numba, datasets, torchvision,
pandas, scipy and matplotlib all appear and were left alone. Excluding
matplotlib on the same hunch once shipped a build with no voice.

### Requirements

**Minimum**
- Windows 10/11
- NVIDIA GPU, 6GB VRAM (4GB works with a smaller model, but voice becomes
  the bottleneck)
- 16GB RAM
- 20GB free disk
- Any modern quad-core CPU
- WebView2 runtime (ships with Windows 11)

**Recommended**
- RTX 3060 12GB or better - this is what everything above was tuned on
- 32GB RAM
- SSD, 25GB free
- A GPU with headroom left over is what makes gaming alongside it
  comfortable

Without a CUDA GPU the app still runs and still talks, but F5-TTS on CPU
takes far longer to speak a sentence than the sentence takes to say.

---

## Phases 5-7 done: tools, vision, web

Great Sage has 11 tools now, all behind the validation layer in
`core/tools.py`:

| tool | what it does |
|---|---|
| get_time, get_system_status | reads the clock and the machine |
| list_running_apps, get_focused_window | what is open, what is focused |
| open_url, open_application, open_folder | acts on the desktop |
| search_files | finds files in the usual folders |
| **look_at_screen** | screenshots and READS the screen |
| **web_search, web_fetch** | searches and reads pages (permission-gated) |

Verified in the PACKAGED build, every one firing and answering, with zero
errors in the log.

### The lesson that repeated three times

A tool that is registered, validated and working will still never be
called if the SYSTEM PROMPT does not say Great Sage can do it. It
happened with open_url ("That authority has not been granted"), then
look_at_screen (it invented an answer), then get_focused_window (it
invented an application name).

**When a tool never fires, check the prompt before checking the tool.**

The trigger list in `might_need_tools` is the second place to look. "apps"
did not match "what app am I in", so the gate blocked the turn entirely
and the model made something up. A near miss there is the difference
between reading the answer and inventing one.

### Context size, which broke vision silently

Ollama defaults to `num_ctx` 4096. The persona prompt, recalled memory,
the tool schema and the conversation already fill most of it, and ONE
image pushes it over:

    request (4956 tokens) exceeds the available context size (4096)

It surfaced as a bare HTTP 400 because the provider discarded the
response body - the same code passed from source and failed in the build,
purely on how long the conversation happened to be. `num_ctx` is now
sized per request (8192 normally, 16384 with an image) at a measured cost
of +214MB VRAM, and the provider reports what Ollama actually said.

### Screen capture plumbing

A tool returns TEXT, but a screenshot must reach the model as an IMAGE.
The capture goes into a one-shot buffer in `tools.py`; the tool's text
result only says what was captured; `ChatEngine.send_with_tools` drains
the buffer and attaches the picture to the follow-up call. Every other
tool stays a plain name -> string.

One shot on purpose: a pending screenshot would otherwise be re-sent with
a later, unrelated question.

---

## Global hotkey, and what can and cannot reduce VRAM

### Ctrl+Alt+S from any window

`core/global_hotkey.py`. Press to start listening, press again to send.
Works while a game or another application has focus, which is the point -
a keybind that needs the window focused first is no use for talking to
Great Sage mid-game.

A TOGGLE rather than hold-to-talk, and that follows from the mechanism:
`RegisterHotKey` reports the press but not the release. The alternative,
a `WH_KEYBOARD_LL` hook, would give both - and would also mean a callback
on every keystroke the machine receives, system-wide, which is a
performance tax and indistinguishable from a keylogger to any anti-cheat.
Krazaa games. Not worth it for hold-to-talk.

Verified by focusing a different window and sending the combination at
the OS level: the app logged listening -> stop -> transcription without
ever being focused.

### VRAM: what was measured, and what is left

Model residency, by context size:

| num_ctx | VRAM |
|---|---|
| 2048 | 3059 MB |
| 4096 | 3128 MB |
| 8192 | 3342 MB (what normal replies use) |
| 16384 | 3627 MB (only when an image is attached) |

So the context is worth a few hundred MB and the WEIGHTS are the other
three gigabytes. The model is already `Q4_K_M` at 4.7B parameters -
quantising further is the only way to shrink the weights, and it costs
quality.

What was done instead:

- `OLLAMA_KEEP_ALIVE_SECONDS = 120`, down from Ollama's default of 300.
  Replies within two minutes of each other pay nothing; the card comes
  back much sooner once the conversation stops.
- GAMING and SLEEP set it to 0 and unload immediately - measured at 3954
  to 4189 MB handed back on the switch.

**Still available, but it is an Ollama SERVER setting rather than
anything this app controls.** Flash attention and a quantised KV cache
cut attention memory further:

    setx OLLAMA_FLASH_ATTENTION 1
    setx OLLAMA_KV_CACHE_TYPE q8_0

Then restart Ollama. Deliberately NOT applied automatically: it changes
Ollama's behaviour for every application on the machine, not just Great
Sage, and that is the user's decision to make.

---

# Linux port + llama.cpp (started 2026-10-29)

The project is being ported from Windows to Linux (Wayland; the user
runs Niri), and the model backend is moving from Ollama to llama.cpp.
Recorded here because it changes several load-bearing assumptions above.

## Why llama.cpp, and the exact shape of it

The user's local server is **llama-swap** (a router in front of a fleet
of `llama-server` instances), listening on `http://localhost:27906`.
Probed directly:

- It speaks the **OpenAI-compatible** API: `/v1/chat/completions`,
  `/v1/models`. There is NO Ollama-compatible surface (`/api/tags`
  404s), so the provider was rewritten against `/v1`, not re-pointed.
- It requires an API key (the `apiKeys` list in its config) even on
  localhost.
- `chat_template_kwargs: {"enable_thinking": false}` is how thinking is
  switched off: llama.cpp passes it into the jinja chat template.
  Verified on Qwen3.8-Orca-27B: thinking off -> clean fast content, no
  reasoning leak. This replaces Ollama's `think` field.
- Tool calling works and returns `tool_calls` with `arguments` as a JSON
  **string** (OpenAI shape), not a dict (Ollama shape). The existing
  `openai_provider.py` already absorbed exactly this difference, so the
  new local provider is a thin subclass of it, not a third
  reimplementation.

## What changed in the code

- `models/ollama_provider.py` deleted. `models/llama_cpp_provider.py`
  is `LlamaCppProvider(OpenAIProvider)`: base_url defaults to
  llama-server's own default port (8080), optional key, an empty model
  name auto-resolves to the first model the server reports (so a fresh
  checkout works against whatever GGUF is loaded), and `unload()` is a
  documented no-op (llama.cpp has no unload endpoint; llama-swap's
  `globalTTL` does the evicting, a bare server is the model).
- `models/openai_provider.py` generalised: `base_url` param (server
  ROOT, it appends `/v1` itself), optional key, `think` ->
  `chat_template_kwargs`, `_model()` seam. Still serves the "openai"
  online menu entry unchanged.
- `config/settings.py`: `OLLAMA_HOST` / `OLLAMA_DEFAULT_MODEL` /
  `OLLAMA_THINK` / `OLLAMA_KEEP_ALIVE_SECONDS` -> `LLM_BASE_URL` /
  `LLM_DEFAULT_MODEL` / `LLM_THINK`. `ACTIVE_PROVIDER = "llama_cpp"`.
  The per-request `num_ctx` sizing is gone - context is fixed at server
  start with `-c`, so the whole juggling was Ollama-specific.
- **`config/settings_local.py` (git-ignored)** is how the local machine
  differs from the committed default without committing machine state.
  Loaded LAST by `settings.py`, which copies its UPPER-CASE constants
  over the module globals. Two gotchas cost a debugging cycle, so they
  are written down:
  1. The file was first named `settings.local.py` - the dot makes it
     unimportable as `settings_local`. Renamed.
  2. `from package import submodule` binds the MODULE name only; the
     constants must be copied over or every `settings.X` read still sees
     the committed default.
- `preflight.py`: the two Ollama checks (installed / running) collapsed
  into one HTTP check on `/v1/models` (401 is the interesting state:
  server up, key wrong), and the WebView2 registry check became a
  WebKitGTK import probe (that is what pywebview actually does on
  Linux).

## Verified

`_smoke_provider.py` (throwaway) ran the full provider surface against
the live llama-swap: model list (22), send_message, stream_response
(10 chunks), chat_raw + tools (real `get_time` call, args parsed), and
auto model-resolve. All pass. First send_message took 16s because
llama-swap had to spawn + load a fresh 27B instance; warm calls are
sub-second-to-3s.

## Environment (this machine, for the next session)

- Python 3.14.8, project venv at `.venv/`. torch **2.11.0+cu128**
  (cu121 has no cp314 wheel; the RTX 5060 Ti is Blackwell and needs
  cu128 anyway). `torch.cuda.is_available()` is True.
- GPUs: RTX 5060 Ti (16GB) + RTX A4000 (16GB). The llama.cpp fleet
  mostly pins `--main-gpu 1` (the A4000).
- WebKitGTK 4.1 present (pywebview's Linux backend).
- The app's default model here is `Qwen3.8-Orca-27B-Instruct` (the
  `--reasoning off` twin of the Orca 27B the user normally runs) - set
  in `settings_local.py`, so a fresh checkout's committed default
  stays "first model the server reports".

## WebKit autoplay: the first clip is silent (2026-10-30)

The Windows twin of this gotcha is the Chromium section above; this is
the Linux half, and it cost a full debugging session too.

**Symptom:** replies triggered by PTT, the wake word, or the startup
greeting produce no speech, and `great_sage.log` fills with
`VoiceError: Timed out waiting for the HUD to finish playing audio`
after a 30s stall. TTS is fine (the server sends a valid WAV over the
bridge) - the page simply never plays it.

**Mechanism:** the page routes `voiceLineAudio` through
`createMediaElementSource -> analyser -> audioCtx.destination` so the
pulse reacts to real amplitude. WebKitGTK's autoplay policy starts
every page locked (`audioCtx.state === 'suspended'`) until a *trusted*
gesture (keypress, click, PTT) in the window, and the two engines then
diverge on exactly the line that was supposed to unlock it:

- Chromium: `resume()` **rejects** without a gesture, so `play()` runs,
  rejects, and the catch acks. No hang.
- WebKitGTK 2.54.2 (measured): `resume()` stays **pending** until a
  gesture arrives. The old chain `resume().then(play)` never reached
  `play()` - no `'ended'`, no rejection - so the server sat out its
  full 30s ack timeout in silence.

**Fix (hud_prototype.html, `playServerAudio`):** race `resume()`
against a 400ms timeout so `play()` always runs; with no gesture yet it
rejects (`NotAllowedError`) and the catch acks immediately instead of
stalling 30s. The first real gesture after startup (PTT keypress,
typing, a click) unlocks the context for everything after.

**Python side (run_hud.py, GTK only):** on pywebview's `shown` event,
find the nested WebKitWebView and set
`set_media_playback_requires_user_gesture(False)`. Three things only
show up by trying:

- Finding the view: `GObject.type_name(widget.__gtype__)` is the only
  reliable name accessor - `get_type_name()` raises AttributeError on
  this PyGObject, and `type(w).__name__` is the wrapper name
  ("WebView"), not the GType ("WebKitWebView").
- Timing: fire on `shown` (the GTK backend sets it from the webview's
  `notify::visible`, so the view is guaranteed to exist). A GLib idle
  source cannot do this job: the page's constant rAF render loop
  starves idle callbacks (the old retry source managed about eight
  attempts in four minutes). pywebview 6 also never fires
  `events.loaded` on GTK, and its `start()` func hook runs on a worker
  thread before the loop even starts.
- **The property is inert on this build (measured, WebKitGTK 2.54.2):**
  it defaults to False, and with it False the `autoplay` attribute is
  ignored - `play()` still rejects and `resume()` still hangs pending.
  So the first clip is skipped *silently, with a prompt ack*, and
  speech unlocks on the first typing/click on the HUD. The call is kept
  anyway: it is the documented knob, and a WebKit build that honours it
  gets gesture-free startup audio for free.

**Resulting behaviour (documented, not a bug):** on the Linux build the
first reply after startup is silent; after the user's first
interaction with the HUD, all speech is audible.

**Test note:** feeding the wake word without a human voice is awkward
on this machine. The only usable loopback (the iBasso monitor source,
`alsa_output.usb-ibasso_...analog-stereo.monitor`) is fixed at 48kHz
and PortAudio's Pulse backend won't open it at the listener's 16kHz
(probed: 16k/24k fail with `Invalid sample rate`, 48k OK). Say the
phrase into the REXUS mic instead.

## WebKit preroll: every clip starts ~1.3s late (2026-10-30)

A second WebKitGTK-only audio gate, found right after the autoplay one
above, with a subtler symptom: the first clip of a reply sounds
truncated (missing its first word), and on a multi-clip reply the
damage looks random.

**Cause:** a `<audio>` element wired into the Web Audio graph
(`createMediaElementSource`) is fed by a GStreamer pipeline, and that
pipeline **prerolls** before audio flows. Measured on this machine
(WebKitGTK 2.54.2, WebKitGTK 4.1): the first **1.109-1.13s** of every
clip is eaten before playback begins. No element-side trick avoids it
- seeking re-offsets the preroll, reassigning `src` re-prerolls, and a
clip shorter than the preroll is eaten whole. Windows
(WebView2/Chromium) has no such preroll, so this is Linux-only, like
the autoplay gate.

**Fix (browser_sink.py):** on Linux, every WAV sent to the HUD gets
1.3s of silence prepended (`WEBKIT_PREROLL_PREBUF_SECONDS`, selected
by `sys.platform`) so the preroll eats silence instead of the first
words; the user hears ~0.2s of lead-in, which is what a natural
utterance has anyway. The ack deadline is lengthened by the same
amount. The padding is off on Windows, where the preroll doesn't
exist.

**Second bug found while verifying:** `audio_fx.py` reset its effect
chain only on the FIRST buffer of a reply. Each buffer is a separate
clip (its own `<audio>` element, a websocket round trip between clips)
- there is no continuous stream for state to carry across. Carrying it
broke the stateful stages: with the WSOLA pitch shift in the chain,
clip 2 or 3 of a reply came back at half level or completely silent
(measured: 0.000/0.069 peaks on 0.79s clips that should have been
full). Fix: `reset=True` on EVERY buffer. Ring-out no longer bleeds
between clips either, because every synthesized clip already carries a
`TAIL_PAD_SECONDS` silence tail that soaks the reverb/delay inside its
own clip.

**Verified end-to-end** (websocket probe, 27B model, 4-clip reply with
two voice-line clips + two F5 clips): all four clips carried content
(peaks 0.13-0.96, no silent clip), each started after its 1.3s
prebuf, and the ack flow completed in 18s with no 30s timeout.

## Jinja 500: system message must be at the beginning (2026-10-30)

**Symptom:** every chat reply failed with `ModelProviderError: the
llama.cpp server returned HTTP 500`, and the llama-swap journal showed
the real cause: `Jinja Exception: System message must be at the
beginning` (a `raise_exception` inside the Qwen chat template). Hit BOTH
models (Qwen3.5-9B and Qwen3.8-Orca-27B) - it is a request-shape
problem, not a model problem.

**Root cause:** the Qwen templates run through llama.cpp with `--jinja`
require any `system` message to be the FIRST message in the array.
`chat_engine._build_outgoing` injected two per-turn `system` messages
AFTER the conversation (the memory-recall block and the `state_hint`
pitch line) right before the user's turn, and `send_with_tools` injected
a third (the preroute tool-results note). The requests were fine while
recall and the hint were empty - which is why early tests passed and
the failures started only once memory had accumulated facts. Ollama
tolerated mid-conversation system messages, so the shape never showed
up there.

**Fix:** `chat_engine.py` now merges the recall block and the state
hint INTO the leading system message (the copy sent, never
`self.history`), and the preroute note in `send_with_tools` is merged
the same way. The per-turn property is preserved: recall is still
re-selected fresh every turn and nothing is stored in history.

**Verified:** throwaway replay of the exact app request shape (stream +
`enable_thinking: False`) at llama-swap - old shape 500 on both models,
new shape 200 on both. `check_routing.py` 49/49. Live app: the
previously-failing `write an essay about life in 100 words` completed in
15.5s.

**Rule for future code:** anything that goes to a Qwen model via
llama.cpp `--jinja` may carry exactly one system message, at index 0.
Do not add mid-conversation system turns (or, for a new provider,
check its template's system-message constraint before reusing this
engine's shapes).

## Global voice key on Linux: the niri keybind path (2026-10-30)

**Symptom:** the settings "voice key" rebind reported every combination
as already in use - `alt+enter`, `alt+n`, `alt+b`, `alt+v`, `alt+x`,
`alt+z`, `alt+capslock`, `alt+f1`, `f2`, `f3`, all "could not be
registered" - while voice input still worked, via the wake word.

**Root cause:** `GlobalHotkey` is Windows-only (`RegisterHotKey`). On
Wayland no key can be claimed, and two things made that invisible:
(1) `rebind()` reports success for an *unchanged* combo, so saving a
setting printed "Voice key is now alt+1 (works from any window)" with
nothing actually registered, and (2) every *different* combo failed
against the previous one and was logged as "already in use" - a
misleading message, because nothing was in use.

**Fix (what actually works on niri now):** the key lives in the
compositor, not the app.

```kdl
# ~/.config/niri/dms/binds.kdl
Alt+F5 hotkey-overlay-title="Great Sage: voice" { spawn "<repo>/gs_hotkey.py" "ptt_toggle"; }
```

- `gs_hotkey.py` (repo root, venv shebang): connects to
  `ws://localhost:8765` and sends `{"type": "ptt_toggle"}` (or
  `ptt_start`/`ptt_stop`). Near-instant on purpose - it does NOT import
  `great_sage.server` just for the port (that drags in torch).
- `ptt_toggle` handler in `server.py`: niri binds fire on the PRESS
  only (the key-up never reaches the app), so the Linux key is a
  toggle - press once to listen, press again to stop + transcribe.
  It deliberately does NOT claim the voice route, unlike `ptt_start`:
  the sender is a short-lived spawned connection, and letting it claim
  would steal the reply destination from the HUD. Results go out on the
  route the HUD's `hello` already claimed - the same path the wake word
  uses.
- `PushToTalkRecorder.running` property added for the toggle state
  (clears on stop before transcription, so a fast re-press starts a
  fresh recording without waiting for the previous text).
- The "Voice key is now X (works from any window)" log now only claims
  that when `_hotkey.active` is actually true.

More key combos = more `binds` entries spawning `gs_hotkey.py`. Niri
accepts three-button combos (`Ctrl+Alt+Shift+X`), so the two-button
space being taken is not a limit.

**Verified:** `check_routing.py` 49/49 (plus js/shaders); live smoke -
two `gs_hotkey.py` pokes logged "Voice key down: listening (toggle)"
-> 2.6s recorded -> "Voice key up: transcribing (toggle)" ->
faster-whisper ran; niri reloaded the config without errors.

**Also stale, fixed in passing:** the "Ctrl+Alt+S from any window"
section above says the Windows key is a TOGGLE. That was true briefly;
Krazaa asked for the hold back (see `global_hotkey.py` - press from
RegisterHotKey, release from GetAsyncKeyState polled only while the key
is down). Windows = hold to talk; Linux = toggle, by mechanism.

## Still to do (the port is NOT finished)

- **Phase 1 (current):** `run_hud.py`'s Win32 window ops (frameless
  toggle, `WM_NCLBUTTONDOWN` drag, `FindWindowW`, work-area placement)
  -> GTK/WebKitGTK equivalents; `app.py`'s `MessageBoxW`. (The
  autoplay half is done - see the WebKit section above: the
  `media-playback-requires-user-gesture` setting is inert on
  WebKitGTK 2.54.2, and the page now acks the first silent clip
  promptly.)
- **Phase 2:** the transparent overlay as a **wlr-layer-shell surface**
  (Niri implements it; there is NO native always-on-top for ordinary
  xdg toplevels - niri-wm/niri#932). Click-through via
  `xdg_toplevel.set_input_region` (the rectangular "core" hit area), not
  the polled `WS_EX_TRANSPARENT`. Global hotkey via a niri `binds`
  entry. Window awareness (tools, game-watch) via
  wlr-foreign-toplevel-management.
- **Phase 3:** packaging (single PyInstaller build on Linux, `.desktop`
  file, no WebView2/shortcut steps).
- Known Niri gotcha for the HUD: enable `prefer-no-csd`, otherwise the
  compositor draws focus rings/borders *behind* the window and they
  show through the translucent HUD.
