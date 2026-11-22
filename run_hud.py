"""
Great Sage HUD - native app entry point.

Opens hud_prototype.html in its own window (via pywebview) and runs the
local WebSocket bridge (great_sage/server.py) that connects it to the
real ChatEngine + voice output, replacing the HUD's canned test-button
behavior with the actual AI.

This is additive: main.py's CLI mode (`.venv/bin/python main.py`) is
untouched and still works exactly as before. Run this instead with
`.venv/bin/python run_hud.py`.
"""

import asyncio
import logging
import os
import sys
import threading
import time

import webview

from great_sage.config import settings
from great_sage.core.chat_engine import ChatEngine
from great_sage.models.base import ModelProviderError
from great_sage.models.llama_cpp_provider import LlamaCppProvider
from great_sage.log_broadcast import HistoryLogHandler
from great_sage.server import run_server
from great_sage.voice.base import VoiceError
from main import (
    build_disabled_voice_line_patterns,
    build_memory_callback,
    build_recall,
    build_system_prompt,
    build_voice_lines,
)

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "great_sage_hud.log")

# Module-level logger. _configure_logging() binds a LOCAL `log` for its own
# use, which is not visible out here - so anything at module scope that
# logged (the overlay window controls below) raised NameError from inside
# its own except block, swallowing the real error it was trying to report.
log = logging.getLogger(__name__)


# Overlay geometry. Square because the mini visual is a 1:1 composition -
# a non-square window would letterbox it or squash the projection.
OVERLAY_SIZE = 340
OVERLAY_MARGIN = 8
FULL_SIZE = (1920, 1080)

# On Linux the window is a GTK window (pywebview's GTK backend: GTK3 +
# WebKit2) and every operation below goes through the GTK API:
# set_decorated for the frame, begin_move_drag for dragging, move() for
# placement. No Win32 style bits anywhere.


class _HudHostApi:
    """Window controls the page cannot reach on its own.

    Exposed to JS as window.pywebview.api. Every method is defensive: the
    page treats the bridge as optional (it still switches its own visuals
    when opened in a plain browser), and a failure here must never take
    the HUD down with it.
    """

    def __init__(self):
        self._window = None
        self._maximized = False
        self._overlay_proc = None

    def attach(self, window) -> None:
        self._window = window

    def _hwnd(self):
        """The native window (a GtkWindow on Linux), or None.

        pywebview's `native` IS our window - no title search needed, so
        the Windows bug (FindWindowW returning the first window with a
        matching title, i.e. a SECOND copy of the app) cannot happen.
        """
        win = self._window
        if win is None:
            return None
        return getattr(win, "native", None)

    def start_drag(self) -> bool:
        """Let GTK drag the window, as though the page were its caption.

        begin_move_drag asks the compositor to start a window move from
        the current pointer position; on Wayland it becomes an
        xdg_toplevel.move tied to the press that just happened, so it has
        to run on the GTK main thread shortly after the page's mousedown -
        the button is still physically held down when it does. This method
        is called from pywebview's JS-API worker thread, hence the
        idle_add hop. The alternative - streaming pointermove deltas into
        window.move() over the JS bridge - puts a round trip in the middle
        of every mouse movement and drags visibly behind the cursor.
        """
        win = self._window
        if win is None:
            log.warning("start_drag: no window yet")
            return False
        native = getattr(win, "native", None)
        if native is None:
            log.warning("start_drag: no native window yet")
            return False
        try:
            from gi.repository import GLib, Gdk
            win_id = native.get_window()
            if win_id is None:
                log.warning("start_drag: window not mapped yet")
                return False
            seat = Gdk.Display.get_default().get_default_seat()
            _seat, root_x, root_y = win_id.get_device_position(seat.get_pointer())

            def _move():
                # Button 1 (primary); GDK_CURRENT_TIME because the real
                # event serial is the one GDK saw at the mousedown.
                native.begin_move_drag(1, int(root_x), int(root_y),
                                       Gdk.CURRENT_TIME)
                return False
            GLib.idle_add(_move)
            log.info("start_drag dispatched at (%d,%d)", root_x, root_y)
            return True
        except Exception:
            log.exception("start_drag failed")
            return False

    def _set_frameless(self, frameless: bool) -> bool:
        """Toggle the frame at runtime.

        Unlike WebView2 on Windows, GTK honours set_decorated() after the
        window exists, so the page (WebSocket, scene, conversation) is
        preserved across the switch - the whole point of this method.
        """
        native = self._hwnd()
        if native is None:
            return False
        try:
            native.set_decorated(not frameless)
            log.info("Window frame %s", "removed" if frameless else "restored")
            return True
        except Exception:
            log.exception("Could not toggle the window frame")
            return False

    def _work_area(self, hwnd=None):
        """(left, top, right, bottom) of the primary monitor's work area.

        Work area, not the full monitor rect, so the overlay tucks under
        the taskbar rather than behind it. Wayland has no per-window
        monitor query like Win32's MonitorFromWindow, and GTK reports the
        work area of the display's primary monitor - where the HUD should
        tuck anyway.
        """
        try:
            from gi.repository import Gdk
            # Monitor 0, not the "primary": GDK3 on Wayland does not track
            # a primary monitor (get_primary_monitor() is None), and
            # monitor 0 is where a tucked-in HUD belongs anyway.
            rect = Gdk.Display.get_default().get_monitor(0).get_workarea()
            return (rect.x, rect.y, rect.x + rect.width, rect.y + rect.height)
        except Exception:
            log.exception("Could not read the monitor work area")
            return None

    def _window_size(self, hwnd=None):
        """Actual window size in pixels.

        Read rather than assumed: the compositor may have resized the
        window (tiling, maximising, a work-area clamp), and positioning
        from the REQUESTED size would sit the window off the corner.
        """
        native = self._hwnd() if hwnd is None else hwnd
        try:
            w, h = native.get_size()
            return (int(w), int(h))
        except Exception:
            log.exception("Could not read the window size")
            return (0, 0)

    def _place(self, hwnd, where: str) -> bool:
        """Move (never resize) the window to a corner or the centre.

        On Wayland, move() before the first map sets the initial position;
        afterwards the compositor honours it for floating windows (niri
        includes). Tiled windows are the compositor's to place, and there
        is nothing to place anyway.
        """
        area = self._work_area(hwnd)
        if not area:
            return False
        left, top, right, bottom = area
        w, h = self._window_size(hwnd)
        if where == "top-right":
            x = right - w - OVERLAY_MARGIN
            y = top + OVERLAY_MARGIN
        else:                                   # centre
            x = left + max(0, (right - left - w) // 2)
            y = top + max(0, (bottom - top - h) // 2)
        try:
            native = self._hwnd()
            native.move(int(x), int(y))
        except Exception:
            log.exception("Could not move the window")
            return False
        log.info("Placed window %s at (%d,%d) size %dx%d", where, x, y, w, h)
        return True

    def set_frame(self, framed: bool) -> bool:
        """Public wrapper, so startup can restore the frame."""
        return self._set_frameless(not framed)

    def fix_host_background(self) -> bool:
        """Nothing to fix on Linux.

        The white square was a WinForms-only bug: the winforms backend set
        the Form's BackColor only for OPAQUE windows, so a transparent
        window sat on the default (240,240,240). pywebview's GTK backend
        applies background_color to BOTH the window (CSS) and the
        WebKitWebView itself, so the page's own colours are what shows -
        this method stays as a no-op for the page's call, which it makes
        on every start.
        """
        return True

    def set_resizable(self, resizable: bool) -> bool:
        """Allow or forbid compositor resizing, without a title bar.

        GTK has no sizing-border style bit: set_resizable() marks the
        xdg_toplevel resizable, and the compositor (niri: Mod+RMB-drag on a
        floating window) provides the affordance. Unlike the Win32
        WS_THICKFRAME bit - which never took effect because the WebView2
        child window swallowed the frame hit-testing - this one actually
        does what it says.
        """
        native = self._hwnd()
        if native is None:
            return False
        try:
            native.set_resizable(resizable)
            log.info("Window resizable %s", "on" if resizable else "off")
            return True
        except Exception:
            log.exception("set_resizable failed")
            return False

    # ---- window controls, drawn by the HUD itself ----------------------
    # The native title bar is gone in both modes now (transparent=True
    # creates the window as a bare WS_POPUP anyway), so the page draws its
    # own minimise / maximise / close and calls these.
    def minimize(self) -> bool:
        try:
            self._window.minimize()
            return True
        except Exception:
            log.exception("minimize failed")
            return False

    def toggle_maximize(self) -> bool:
        """Maximise, or restore if already maximised.

        Tracked here rather than asked of the window: pywebview exposes
        maximize() and restore() but no "is it maximised" flag, so the
        page would otherwise have no way to know which one to call.
        """
        try:
            if self._maximized:
                self._window.restore()
            else:
                self._window.maximize()
            self._maximized = not self._maximized
            return self._maximized
        except Exception:
            log.exception("toggle_maximize failed")
            return self._maximized

    def close_app(self) -> bool:
        try:
            self._window.destroy()
            return True
        except Exception:
            log.exception("close failed")
            return False

    # ---- the transparent overlay, hosted in its own process -----------
    def _overlay_command(self):
        """How to launch the overlay host, or None if it does not exist.

        The Windows host is overlay_window.py (PySide6 + QWebEngineView +
        Win32 click-through) - not portable to Wayland, which is why
        overlay mode is the last piece of the Linux port. When the Linux
        host (a layer-shell binary, Phase 2) lands, it will sit in
        overlay/ beside this file, and this is where it gets launched.
        """
        here = os.path.dirname(os.path.abspath(__file__))
        overlay_bin = os.path.join(here, "overlay", "great_sage_overlay")
        if os.path.exists(overlay_bin):
            return [overlay_bin, "--size", str(OVERLAY_SIZE)]
        # Fall back to Python script for development
        overlay_script = os.path.join(here, "overlay_window.py")
        if os.path.exists(overlay_script):
            return [sys.executable, overlay_script, "--size", str(OVERLAY_SIZE)]
        log.info("Overlay host not found at %s - overlay mode is not "
                 "available on Linux yet (Phase 2)", overlay_bin)
        return None

    def _watch_overlay(self, proc):
        """Restore the main window when the overlay closes on its own.

        The overlay's own exit control ends its process, so without this
        the app would be left with every window hidden and no way back.
        """
        proc.wait()
        log.info("Overlay process exited (%s); restoring the main window",
                 proc.returncode)
        self._overlay_proc = None
        try:
            self._window.show()
            self._focus_window()
        except Exception:
            log.exception("Could not restore the main window")

    def _focus_window(self) -> bool:
        """Bring the main window to the front and give it focus.

        show() alone only makes it visible - it comes back behind whatever
        the user clicked while the overlay was up, which reads as the
        overlay having closed into nothing. present() is GTK's raise-and-
        focus; on Wayland the compositor may still keep focus elsewhere
        (user-caused focus is the rule), but the window is at least
        visible and raised, which is what matters here.
        """
        native = self._hwnd()
        if native is None:
            return False
        try:
            native.present()
            log.info("Main window raised and focused")
            return True
        except Exception:
            log.exception("_focus_window failed")
            return False

    def set_overlay(self, on: bool) -> bool:
        """Switch between the main window and the transparent overlay.

        The overlay stays a SEPARATE PROCESS rather than a resize of this
        window, because the overlay is not just "smaller and transparent"
        - it has to sit in a compositor LAYER (always above, click-
        through, pinned to the corner), which an xdg_toplevel cannot do.
        On Windows that was overlay_window.py (PySide6 + QWebEngineView);
        on Linux the host will be a layer-shell binary (Phase 2). Until
        it exists, _overlay_command() returns None and this method returns
        False, so the page simply stays in the main window.

        Two processes either way: it keeps the main HUD on the stack that
        already works - if the overlay host fails, the app it was
        launched from is untouched.
        """
        import subprocess
        win = self._window
        if win is None:
            return False
        try:
            if on:
                if self._overlay_proc is None:
                    cmd = self._overlay_command()
                    if cmd is None:
                        # Overlay unavailable in this build. Leave the main
                        # window up rather than hiding it behind nothing -
                        # hiding first and failing after is how the user
                        # ends up with no window at all.
                        return False
                    self._overlay_proc = subprocess.Popen(
                        cmd, cwd=os.path.dirname(os.path.abspath(__file__)))
                    threading.Thread(target=self._watch_overlay,
                                     args=(self._overlay_proc,),
                                     daemon=True).start()
                    log.info("Overlay process started (pid %s)",
                             self._overlay_proc.pid)
                win.hide()
            else:
                if self._overlay_proc is not None:
                    self._overlay_proc.terminate()
                    self._overlay_proc = None
                win.show()
            return True
        except Exception:
            log.exception("set_overlay(%s) failed", on)
            try:
                win.show()      # never leave every window hidden
            except Exception:
                pass
            return False



def _configure_logging() -> None:
    """Writes to great_sage_hud.log (readable after the fact, since this
    runs as a detached GUI window with no visible console) and echoes to
    stdout too, in case that IS being captured somewhere."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, mode="a", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
            # Buffers from here on, so the HUD's LOGS console can show
            # what happened before it was opened.
            HistoryLogHandler(),
        ],
    )


def _build_voice(provider=None):
    log = logging.getLogger(__name__)
    if not settings.VOICE_ENABLED:
        return None
    if settings.VOICE_ENGINE not in ("pocket", "f5"):
        log.warning(
            "VOICE_ENGINE=%r isn't supported by the HUD app (only 'pocket' "
            "and 'f5' stream audio to the browser) - running text-only.",
            settings.VOICE_ENGINE,
        )
        return None

    if settings.VOICE_ENGINE == "f5":
        from great_sage.voice.f5_tts_engine import F5TTSVoiceOutput

        try:
            return F5TTSVoiceOutput(
                reference_audio_path=settings.F5_REFERENCE_AUDIO_PATH,
                voice_lines=build_voice_lines(),
                disabled_voice_line_patterns=build_disabled_voice_line_patterns(),
                nfe_step=settings.F5_NFE_STEP,
                single_shot=settings.VOICE_SINGLE_SHOT,
            )
        except VoiceError as exc:
            # F5 needs a GPU and a multi-GB model; falling back to the
            # CPU-only engine keeps the app usable rather than silently
            # losing its voice entirely.
            log.warning("F5-TTS unavailable (%s) - falling back to Pocket TTS.", exc)

    from great_sage.voice.pocket_tts_engine import PocketTTSVoiceOutput

    try:
        return PocketTTSVoiceOutput(
            reference_audio_path=settings.CLONE_REFERENCE_AUDIO_PATH,
            voice_lines=build_voice_lines(),
            disabled_voice_line_patterns=build_disabled_voice_line_patterns(),
        )
    except VoiceError as exc:
        log.warning("Voice unavailable (%s) - continuing in text-only mode.", exc)
        return None


def _start_server_thread(engine, voice) -> None:
    def runner():
        asyncio.run(run_server(engine, voice))

    threading.Thread(target=runner, daemon=True).start()


def main() -> int:
    _configure_logging()
    logging.getLogger(__name__).info("Starting Great Sage HUD")

    provider = LlamaCppProvider(
        base_url=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY,
        model=settings.LLM_DEFAULT_MODEL,
        timeout=settings.REQUEST_TIMEOUT_SECONDS,
        think=settings.LLM_THINK,
    )
    try:
        provider.get_available_models()
    except ModelProviderError as exc:
        logging.getLogger(__name__).error("Startup error: %s", exc)
        return 1

    engine = ChatEngine(
        provider,
        build_system_prompt(),
        idle_reset_seconds=settings.SESSION_IDLE_RESET_MINUTES * 60,
        on_session_boundary=build_memory_callback(provider),
        recall=build_recall(provider),
    )
    voice = _build_voice(provider)

    _start_server_thread(engine, voice)

    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hud_prototype.html")
    api = _HudHostApi()
    # transparent=True gives the window an RGBA visual so that when
    # overlay mode makes html/body transparent, the desktop shows through.
    # It costs nothing in normal mode: the page paints its own opaque
    # background. On the GTK backend it is honoured at creation (and the
    # WebKitWebView background is made transparent to match), unlike the
    # WinForms backend where per-pixel alpha was unreachable at all.
    # frameless=True removes the title bar (set_decorated(False)). On GTK
    # the frame CAN be re-attached at runtime - _set_frameless does - but
    # the page's own chrome is the design, so it stays off.
    #
    # With no frame there is nothing native to drag or close by, so the
    # page MUST provide both: #win-controls draws minimise/maximise/close
    # and #win-drag is the grab strip, all wired to _HudHostApi.
    window = webview.create_window(
        "Great Sage", html_path, width=1920, height=1080,
        background_color="#030b08", js_api=api, transparent=True,
        frameless=True,
        # easy_drag defaults to TRUE for frameless windows, which makes the
        # ENTIRE window a drag surface - press anywhere on the visual and it
        # moves. Measured: a press in the dead centre moved the window by
        # (151,79). The page defines its own drag region instead (the
        # #win-drag strip, via app-region), so this must be off or it
        # overrides that and there is no way to click anything.
        easy_drag=False,
        # Native sizing borders on all edges and corners. pywebview applies
        # this at creation, unlike the WS_THICKFRAME bit set later, which
        # never took effect because the WebView2 child window covers the
        # whole client area and swallows the frame's hit-testing.
        resizable=True,
    )
    api.attach(window)

    # The HUD routes all speech through a Web Audio graph (for the
    # amplitude-reactive pulse), and WebKitGTK's autoplay policy starts
    # every page locked: no gesture yet, so a reply triggered by
    # push-to-talk, the wake word, or the startup greeting is silent AND
    # never fires 'ended'. The page side guards against the hang that
    # caused (playServerAudio races resume() against a timeout, so the
    # ack still arrives when playback is rejected) - this block is the
    # attempt to make that first clip audible without a click.
    #
    # Caveat, measured on WebKitGTK 2.54.2: the
    # media-playback-requires-user-gesture property defaults to False
    # there AND is inert - with it False, the autoplay attribute is
    # ignored, play() still rejects (NotAllowedError) and
    # AudioContext.resume() still hangs pending until a trusted gesture.
    # So on that build the first clip is skipped (silently, with a
    # prompt ack) until the first typing/click on the HUD, and only
    # then does speech unlock. Kept anyway: it is the documented
    # knob for exactly this, and a WebKit build that honours it gets
    # gesture-free startup audio for free.
    #
    # GTK only: the Windows backend (WebView2) already runs with
    # --autoplay-policy=no-user-gesture-required and has no
    # WebKitWebView to configure.
    if sys.platform == "linux":
        from gi.repository import GObject

        def _find_webkit_view(widget):
            # The WebKitWebView sits nested inside the GTK window; find
            # it by type so this does not depend on pywebview's internal
            # layout. GObject.type_name() is the only reliable accessor:
            # get_type_name() does not exist on this PyGObject's widgets
            # (it raises AttributeError), and type(w).__name__ returns
            # the wrapper-class name ("WebView"), not the GType name.
            try:
                name = GObject.type_name(widget.__gtype__)
            except Exception:
                name = ""
            if name == "WebKitWebView":
                return widget
            for child in getattr(widget, "get_children", lambda: [])():
                found = _find_webkit_view(child)
                if found is not None:
                    return found
            return None

        def _allow_web_audio_autoplay() -> None:
            # Fired by pywebview's 'shown' event, which its GTK backend
            # sets from the webview's notify::visible - the webview is
            # guaranteed to exist by then, so no retry loop is needed.
            # (A GLib idle source cannot do this job: the page runs a
            # constant rAF render loop that keeps the GTK loop busy and
            # starves idle callbacks - the old retry source managed
            # roughly eight attempts in four minutes. pywebview 6 also
            # never fires events.loaded on GTK, and its start() func hook
            # runs on a worker thread before the loop even starts.)
            native = getattr(window, "native", None)
            wv = _find_webkit_view(native) if native is not None else None
            if wv is None:
                log.warning("Autoplay policy: WebKitWebView not found - "
                            "in-window audio may need a click first")
                return
            try:
                wv.get_settings().set_media_playback_requires_user_gesture(False)
                log.info("Web audio autoplay allowed (no user gesture required)")
            except Exception:
                log.exception("Could not set the WebKit autoplay policy")

        window.events.shown += _allow_web_audio_autoplay

    # Centre the window once the page is up.
    #
    # Not cosmetic: a frameless window that opens partly off-screen cannot
    # be dragged back, because the only grab strip is inside the page and
    # the native title bar is gone. (Measured on the Windows box, the
    # first frameless build opened at -432,1287 on a 2560x1440 desktop.)
    #
    # _place() uses the monitor WORK AREA, so it also keeps clear of the
    # taskbar. On Wayland the compositor may override the initial
    # position (niri tiles new windows); the move is best-effort.
    def _centre_window():
        hwnd = api._hwnd()
        if hwnd:
            api._place(hwnd, "centre")
            # Marks the toplevel resizable, for compositors that offer a
            # floating-window resize affordance.
            api.set_resizable(True)

    try:
        window.events.loaded += _centre_window
    except Exception:
        log.exception("Could not hook the loaded event to centre the window")
    # Devtools OFF by default. debug=True enables the WebKit inspector
    # (F12 / right-click) - fine while building, wrong for anyone testing
    # the app.
    #
    # Set GREAT_SAGE_DEBUG=1 to get it back when debugging the page.
    debug = os.environ.get("GREAT_SAGE_DEBUG", "").strip() not in ("", "0")
    if debug:
        log.info("Devtools enabled (GREAT_SAGE_DEBUG is set)")
    webview.start(debug=debug)
    return 0


if __name__ == "__main__":
    sys.exit(main())
