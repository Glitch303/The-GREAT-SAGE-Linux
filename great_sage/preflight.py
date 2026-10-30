"""What is missing before Great Sage can run, and how to get it.

Detection only - nothing here installs, downloads, or changes anything, so
it is safe to call on every startup and from the installer UI alike. The
installer decides what to act on; this module only reports.

Three of Great Sage's dependencies cannot travel inside the application:

  the model server  llama.cpp's llama-server (or a router such as
                    llama-swap in front of it), run as a background process
  the chat model    a GGUF the server loads, in its own model store
  WebKitGTK         the system web engine the HUD window renders through

A fourth, the voice model weights, CAN be bundled but usually should not
be: ~3.6GB that F5-TTS and Whisper fetch on first use anyway.

Each check returns a Requirement, and every one of them is expected to
fail on a clean machine - that is the normal first-run state, not an
error. `blocking` marks the ones without which the app genuinely cannot
work, as opposed to those that only cost speed or polish.
"""

import importlib
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import List, Optional

from great_sage.config import settings

LLAMA_DOWNLOAD = "https://github.com/ggml-org/llama.cpp/releases"


@dataclass
class Requirement:
    key: str
    label: str
    ok: bool
    detail: str = ""
    blocking: bool = True
    # A shell command the installer can run to fix it, when fixing is just
    # a command. None means the fix needs a download or a human.
    fix_command: Optional[List[str]] = None
    fix_url: Optional[str] = None
    fix_hint: str = ""
    size_hint: str = ""


def _run(cmd, timeout=10):
    """Run a command, returning (rc, stdout). Never raises."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as exc:
        return 1, str(exc)


def _server_url() -> str:
    return settings.LLM_BASE_URL.rstrip("/")


def _server_headers() -> dict:
    return ({"Authorization": "Bearer " + settings.LLM_API_KEY}
            if settings.LLM_API_KEY else {})


def check_llama_server() -> Requirement:
    """Is the local model server answering?

    One HTTP check covers both "installed" and "running" - over the wire
    the two states are indistinguishable, and the fix for both is the
    same: start the server. A 401 is the interesting state: the server
    IS up, and the key in the settings is what is wrong."""
    base = _server_url()
    try:
        import requests
        r = requests.get(base + "/v1/models", headers=_server_headers(),
                         timeout=3)
        if r.status_code == 401:
            return Requirement(
                key="llama_server", label="llama.cpp server", ok=False,
                detail=f"up at {base}, but it rejected the API key",
                fix_hint="Check LLM_API_KEY in settings.local.py against "
                         "the apiKeys in the server's config.",
            )
        reachable = r.status_code == 200
        detail = f"responding ({base})" if reachable else f"HTTP {r.status_code}"
    except Exception as exc:
        reachable, detail = False, f"{type(exc).__name__} at {base}"
    return Requirement(
        key="llama_server", label="llama.cpp server", ok=reachable,
        detail=detail, fix_url=LLAMA_DOWNLOAD,
        fix_hint="Install llama.cpp and run `llama-server -m <model.gguf>` "
                 "(it listens on port 8080 by default), or start your "
                 "router (llama-swap) and point LLM_BASE_URL at it.",
    )


def check_model() -> Requirement:
    """Is the configured chat model available on the server?

    An empty LLM_DEFAULT_MODEL means "the first model the server
    reports", so any non-empty list satisfies the check."""
    want = settings.LLM_DEFAULT_MODEL
    found, detail = False, "could not query the server"
    try:
        import requests
        r = requests.get(_server_url() + "/v1/models",
                         headers=_server_headers(), timeout=5)
        if r.status_code == 200:
            names = [m.get("id", "") for m in r.json().get("data", [])]
            names = [n for n in names if n]
            found = bool(names) and (not want or want in names)
            detail = ("installed" if found
                      else ("models on server: " + (", ".join(names) or "none")))
    except Exception as exc:
        detail = f"{type(exc).__name__}"
    return Requirement(
        key="model",
        label=f"Chat model ({want or 'first reported'})",
        ok=found, detail=detail,
        fix_hint="Load a GGUF into the server (llama-server -m ...), or "
                 "set LLM_DEFAULT_MODEL in settings.local.py to one of the "
                 "names above.",
    )


def check_webview() -> Requirement:
    """The web engine the HUD window renders through.

    On Linux pywebview hosts WebKitGTK (a system package, not a pip one),
    and without it the window never appears - which looks like the app
    silently doing nothing. The import probe is the honest check: it is
    exactly what pywebview does when the window is created."""
    present, detail = False, "not detected"
    try:
        import gi
        # WebKitGTK 6.0 (GTK4) uses the WebKit namespace; 4.x, the one
        # most distros still ship, uses WebKit2.
        for ns, ver in (("WebKit", "6.0"), ("WebKit2", "4.1"),
                        ("WebKit2", "4.0")):
            try:
                gi.require_version(ns, ver)
                mod = importlib.import_module("gi.repository." + ns)
                present = True
                detail = f"{ns} {ver} ({getattr(mod, '_version', ver)})"
                break
            except Exception:
                continue
    except Exception as exc:
        detail = f"python3-gi missing ({type(exc).__name__})"
    return Requirement(
        key="webview", label="WebKitGTK (HUD renderer)", ok=present,
        detail=detail,
        fix_hint="Install the system packages: libwebkit2gtk-4.1-0 and "
                 "gir1.2-webkit2-4.1 (or the webkitgtk-6.0 equivalents), "
                 "plus python3-gi.",
        size_hint="~200 MB",
    )


_gpu_cache = None


def check_gpu() -> Requirement:
    """CUDA availability. NOT blocking - the app runs on CPU, just slowly.

    Cached after the first call. Importing torch costs several seconds,
    and the installer re-runs every check after each fix - without the
    cache the window sat on "checking..." long enough to look hung, which
    is exactly what the screenshot of the first build showed.

    Safe to cache: a GPU does not appear or vanish mid-session.
    """
    global _gpu_cache
    if _gpu_cache is not None:
        return _gpu_cache
    ok, detail = False, "no CUDA GPU detected - voice will be very slow"
    try:
        import torch
        ok = bool(torch.cuda.is_available())
        detail = (torch.cuda.get_device_name(0) if ok
                  else "torch present, but no CUDA device")
    except Exception as exc:
        detail = f"torch unavailable ({type(exc).__name__})"
    _gpu_cache = Requirement(
        key="gpu", label="CUDA GPU (for voice)", ok=ok, detail=detail,
        blocking=False,
        fix_hint="Without a GPU the app still runs, but speech takes far "
                 "longer than it does to listen to.",
    )
    return _gpu_cache


def check_voice_weights() -> Requirement:
    """F5-TTS / Whisper weights in the HuggingFace cache.

    Not blocking: they download themselves on first use. Reported so a
    tester on a slow connection knows the first reply will be delayed
    rather than broken.
    """
    hf = os.environ.get("HF_HOME") or os.path.expanduser("~/.cache/huggingface")
    hub = os.path.join(hf, "hub")
    have = os.path.isdir(hub) and any(
        "F5-TTS" in d or "vocos" in d.lower() for d in os.listdir(hub))
    return Requirement(
        key="weights", label="Voice model weights", ok=have,
        detail=("cached" if have else "will download on first use"),
        blocking=False, size_hint="~3.6 GB",
        fix_hint="Downloads automatically the first time Great Sage speaks.",
    )


def check_overlay_host() -> Requirement:
    """The host the transparent overlay runs in.

    On Linux that is the layer-shell binary in overlay/ (Phase 2); on
    Windows it is PySide6, which overlay_window.py runs on.

    NOT blocking: the main window is pywebview and works without it. Only
    overlay mode is lost - and without this check that loss is silent,
    because the launcher starts the overlay process, the import fails, the
    process exits, and the watcher quietly restores the main window. It
    looks exactly like the button doing nothing.
    """
    ok, detail = False, "not installed - overlay mode unavailable"
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    if sys.platform != "win32":
        # Linux: the overlay host is the layer-shell binary that will sit
        # in overlay/ (Phase 2 of the port - it needs the compositor's
        # layer-shell protocol, which an xdg_toplevel window cannot reach).
        # Until it is built, overlay mode is unavailable and this reports
        # it, so the setup window and the log agree.
        exe = os.path.join(here, "overlay", "great_sage_overlay")
        ok = os.path.exists(exe)
        detail = ("found" if ok
                  else "not built yet - overlay mode unavailable (Phase 2)")
        return Requirement(
            key="overlay_host", label="Overlay host", ok=ok, detail=detail,
            blocking=False,
            fix_hint="Only needed for the transparent desktop overlay.")

    if getattr(sys, "frozen", False):
        # In a build there is no PySide6 to import HERE - it lives in the
        # overlay's own bundle, built on a different Python (see
        # overlay.spec). Importing it in this process would always fail
        # and report overlay mode as broken on a build where it works.
        exe = os.path.join(os.path.dirname(sys.executable),
                           "overlay", "GreatSageOverlay.exe")
        ok = os.path.exists(exe)
        detail = exe if ok else f"missing: {exe}"
        return Requirement(
            key="overlay_host", label="Overlay host", ok=ok, detail=detail,
            blocking=False,
            fix_hint="Part of the application files - a missing overlay "
                     "folder means the extraction was incomplete.")

    # From source, the overlay runs under .overlay-venv when that exists -
    # run_hud._overlay_command prefers it - so ask THAT interpreter. This
    # process has a different, newer PySide6, and reporting its version
    # would name a Qt the overlay never uses: 6.10.1 here, 6.4.3 in the
    # venv that actually runs, and only 6.4.x is free of the flicker.
    venv_py = os.path.join(here, ".overlay-venv", "Scripts", "python.exe")
    if os.path.exists(venv_py):
        rc, out = _run([venv_py, "-c",
                        "from PySide6 import QtCore;print(QtCore.qVersion())"],
                       timeout=20)
        ok = rc == 0 and out.strip()[:1].isdigit()
        ver = out.strip().splitlines()[-1] if ok else ""
        detail = (f"Qt {ver} (.overlay-venv)" if ok
                  else "overlay venv present but PySide6 did not import")
        if ok and not ver.startswith("6.4"):
            detail += " - 6.4.x recommended; newer Qt flickers"
        return Requirement(
            key="overlay_host", label="Overlay host (PySide6)", ok=ok,
            detail=detail, blocking=False,
            fix_command=[venv_py, "-m", "pip", "install", "PySide6==6.4.3"],
            fix_hint="Only needed for the transparent desktop overlay.",
            size_hint="~150 MB")

    try:
        import PySide6                                     # noqa: F401
        from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
        from PySide6 import QtCore
        ok = True
        detail = f"PySide6 / Qt {QtCore.qVersion()}"
    except Exception as exc:
        detail = f"{type(exc).__name__}: overlay mode unavailable"
    return Requirement(
        key="overlay_host", label="Overlay host (PySide6)", ok=ok,
        detail=detail, blocking=False,
        fix_command=[sys.executable, "-m", "pip", "install", "PySide6", "qtpy"],
        fix_hint="Only needed for the transparent desktop overlay; the main "
                 "window does not use it.",
        size_hint="~150 MB",
    )


def check_reference_voice() -> Requirement:
    """The clip F5 clones. Ships with the app, so this failing means a
    broken or incomplete copy rather than a missing prerequisite."""
    p = settings.F5_REFERENCE_AUDIO_PATH
    if not os.path.isabs(p):
        p = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), p)
    ok = os.path.exists(p)
    return Requirement(
        key="voice_ref", label="Reference voice clip", ok=ok,
        detail=p if ok else f"missing: {p}",
        fix_hint="Part of the application files - a missing one means the "
                 "download or extraction was incomplete.",
    )


ALL_CHECKS = (check_llama_server, check_model, check_webview,
              check_gpu, check_voice_weights, check_overlay_host,
              check_reference_voice)


def run_all() -> List[Requirement]:
    results = []
    for fn in ALL_CHECKS:
        try:
            results.append(fn())
        except Exception as exc:
            results.append(Requirement(
                key=getattr(fn, "__name__", "?"), label=fn.__name__,
                ok=False, detail=f"check itself failed: {exc}"[:120]))
    return results


def blocking_failures(results=None) -> List[Requirement]:
    """The ones that actually stop the app from working."""
    return [r for r in (results or run_all()) if r.blocking and not r.ok]


if __name__ == "__main__":
    print(f"{'':2} {'requirement':30} {'':4} detail")
    print("-" * 78)
    for r in run_all():
        mark = "OK" if r.ok else ("!!" if r.blocking else "..")
        size = f" [{r.size_hint}]" if (not r.ok and r.size_hint) else ""
        print(f"{mark:2} {r.label:30} {'':4} {r.detail}{size}")
    bad = blocking_failures()
    print("\n" + ("Ready to run." if not bad else
                  f"{len(bad)} blocking item(s): "
                  + ", ".join(r.label for r in bad)))
