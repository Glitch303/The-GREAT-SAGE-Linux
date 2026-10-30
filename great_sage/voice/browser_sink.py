"""
Streams synthesized/pre-recorded audio to a connected HUD browser client
instead of playing it through local speakers, so the HUD's own Web Audio
analyser (already tuned for real audio-reactive pulsing in
hud_prototype.html) drives the visuals from genuine playback - Python
doesn't need to separately compute or forward amplitude data.

speak() on the voice engine is documented to block until playback
finishes, so play()/play_file() here send the audio over the websocket
and then block on an ack ("audio_ended") that the HUD sends once its
<audio> element actually finishes playing - see great_sage/server.py's
protocol docstring for the full message shapes.
"""

import asyncio
import base64
import json
import os
import sys
import threading

from great_sage.voice.base import VoiceError
from great_sage.voice.sinks import AudioSink

# WebKitGTK (the HUD on Linux) starts every <audio> element wired into the
# Web Audio graph ~1.11s into its clip: its GStreamer pipeline prerolls
# that far before audio starts, and no element-side trick avoids it
# (seeking re-offsets, reassigning src re-prerolls, and a clip shorter
# than the preroll is eaten whole). So on Linux every WAV sent to the HUD
# gets this much silence prepended - the preroll eats silence instead of
# the first words, and the user hears ~0.2s of lead-in, which is what a
# natural utterance has anyway. Measured 1.109-1.13s of preroll on this
# machine (probe history in NOTES.md), so 1.3s covers it with margin.
# Windows (WebView2/Chromium) has no such preroll, so the padding stays
# off there.
WEBKIT_PREROLL_PREBUF_SECONDS = 1.3


def _webkit_preroll_prebuf() -> float:
    """Prebuffer length for this platform's HUD engine, in seconds."""
    if sys.platform.startswith("linux"):
        return WEBKIT_PREROLL_PREBUF_SECONDS
    return 0.0


def _prepend_silence(data: bytes, seconds: float):
    """Prepend `seconds` of silence to a WAV clip (WAV in, WAV out).

    Returns (data, seconds actually prepended).
    """
    if not seconds:
        return data, 0.0
    import io

    import numpy as np
    import soundfile as sf

    samples, rate = sf.read(io.BytesIO(data), dtype="float32")
    silence = np.zeros(int(seconds * rate), dtype="float32")
    if samples.ndim == 2:
        silence = np.repeat(silence[:, None], samples.shape[1], axis=1)
    buf = io.BytesIO()
    sf.write(buf, np.concatenate([silence, samples]), rate, format="WAV")
    return buf.getvalue(), seconds

# How long to wait for the client's "finished playing" ack BEYOND the
# clip's own length. This is a dead-client detector - a browser that
# closed, crashed, or never decoded the audio - so it only has to exceed
# normal network and decode jitter, not the audio itself.
#
# It used to be a flat 30s wall-clock cap, which silently doubled as a
# maximum spoken length: with VOICE_SINGLE_SHOT the whole reply is ONE
# clip, so any answer longer than 30s of speech timed out and was lost.
# That went unnoticed while the model's median reply was ~110 chars and
# became reachable as soon as a model that writes longer lists was used.
ACK_TIMEOUT_MARGIN_SECONDS = 30.0

# Floor for clips whose duration cannot be determined.
ACK_TIMEOUT_SECONDS = 30.0


class BrowserAudioSink(AudioSink):
    def __init__(self, websocket, loop: asyncio.AbstractEventLoop):
        self._websocket = websocket
        self._loop = loop
        self._ack_event = threading.Event()
        # Text belonging to the clip about to be sent. The HUD shows it
        # at the moment that clip starts playing, so the caption tracks
        # the voice instead of racing ahead of it. Set by the engine
        # immediately before each play(); cleared once consumed so a
        # stale line can never ride along with the next clip.
        self.pending_text = None

    def notify_audio_ended(self) -> None:
        """Called by the server's receive loop when the client reports its
        <audio> element finished - unblocks the matching play()/play_file()."""
        self._ack_event.set()

    def _send_and_wait(self, data: bytes, mime: str,
                       duration: float = None) -> None:
        """Send one clip and block until the client says it finished.

        `duration` is the clip's length in seconds when the caller knows
        it, so the ack deadline can scale with the audio instead of
        capping it. Without it the flat floor applies.
        """
        text = self.pending_text
        self.pending_text = None
        # The HUD's WebKit preroll eats the clip's head; hand it silence to
        # eat instead (no-op on platforms without the preroll).
        prebuf = 0.0
        if mime == "audio/wav":
            data, prebuf = _prepend_silence(data, _webkit_preroll_prebuf())
        payload = json.dumps({
            "type": "audio",
            "mime": mime,
            "data": base64.b64encode(data).decode("ascii"),
            "text": text or "",
        })
        self._ack_event.clear()
        future = asyncio.run_coroutine_threadsafe(self._websocket.send(payload), self._loop)
        future.result()
        # The clip has to finish PLAYING before the ack can arrive, so the
        # deadline is its length plus room for jitter - never less than the
        # floor, so a very short clip keeps a sane grace period.
        deadline = ACK_TIMEOUT_SECONDS
        if duration and duration > 0:
            deadline = max(deadline, duration + prebuf + ACK_TIMEOUT_MARGIN_SECONDS)
        if not self._ack_event.wait(timeout=deadline):
            raise VoiceError(
                "Timed out waiting for the HUD to finish playing audio "
                "(waited %.0fs for a %s clip)." % (
                    deadline,
                    "%.1fs" % (duration + prebuf) if duration
                    else "clip of unknown length"))

    def play(self, samples, samplerate: int) -> None:
        import io

        import soundfile as sf

        buf = io.BytesIO()
        sf.write(buf, samples, samplerate, format="WAV")
        duration = None
        try:
            duration = len(samples) / float(samplerate)
        except Exception:
            pass          # a length we cannot compute just falls back to the floor
        self._send_and_wait(buf.getvalue(), "audio/wav", duration)

    def play_file(self, path: str) -> None:
        with open(path, "rb") as f:
            data = f.read()
        mime = "audio/ogg" if path.lower().endswith(".ogg") else "audio/wav"
        duration = None
        try:
            import soundfile as sf
            duration = sf.info(path).duration
        except Exception:
            pass          # header we cannot read just falls back to the floor
        self._send_and_wait(data, mime, duration)

    def stop(self) -> None:
        self._ack_event.set()  # unblock any pending wait so speak() can return
