"""draw.io backend — runs the real draw.io desktop app for export.

The desktop app (Electron) has a command-line export mode implemented in
``drawio-desktop/src/main/electron.js``::

    draw.io --export --format png --output out.png input.drawio

Facts verified against draw.io 24.7.17 that this module relies on:

* The process exits with code 0 even when the export failed; the only
  signals are ``Error: ...`` lines and a missing output file.
* stderr carries Chromium noise on successful runs, so stderr text alone is
  not an error.
* An existing output file is overwritten without warning, so callers export
  to a fresh path and move the result themselves.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import time

ENV_PATH = "DRAWIO_PATH"
ENV_EXTRA_ARGS = "CLI_ANYTHING_DRAWIO_ARGS"
EXECUTABLE_NAMES = ("draw.io", "drawio", "draw.io.exe")

INSTALL_HELP = (
    "draw.io desktop app is not installed (or could not be found). Install it:\n"
    "  Windows: winget install JGraph.Draw\n"
    "  macOS:   brew install --cask drawio\n"
    "  Linux:   snap install drawio   (or the .deb/.AppImage from "
    "https://github.com/jgraph/drawio-desktop/releases)\n"
    f"If it is installed in a non-standard location, set {ENV_PATH} to the "
    "full path of the executable."
)


def default_locations():
    """Well-known install paths that are usually not on PATH."""
    paths = []
    if sys.platform.startswith("win"):
        for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
            base = os.environ.get(var)
            if base:
                paths.append(os.path.join(base, "draw.io", "draw.io.exe"))
        local = os.environ.get("LOCALAPPDATA")
        if local:
            paths.append(os.path.join(local, "Programs", "draw.io", "draw.io.exe"))
    elif sys.platform == "darwin":
        paths.append("/Applications/draw.io.app/Contents/MacOS/draw.io")
        paths.append(
            os.path.expanduser("~/Applications/draw.io.app/Contents/MacOS/draw.io")
        )
    else:
        paths.extend(
            ["/usr/bin/drawio", "/usr/local/bin/drawio", "/opt/drawio/drawio",
             "/snap/bin/drawio"]
        )
    seen = []
    for path in paths:
        if path not in seen:
            seen.append(path)
    return seen


def find_drawio():
    """Return the draw.io executable path. Raises RuntimeError if not found."""
    override = os.environ.get(ENV_PATH, "").strip().strip('"')
    if override:
        if os.path.isfile(override):
            return override
        raise RuntimeError(
            f"{ENV_PATH} is set to '{override}' but that file does not exist."
        )
    for name in EXECUTABLE_NAMES:
        path = shutil.which(name)
        if path:
            return path
    for path in default_locations():
        if os.path.isfile(path):
            return path
    raise RuntimeError(INSTALL_HELP)


def _extra_args():
    args = []
    raw = os.environ.get(ENV_EXTRA_ARGS, "").strip()
    if raw:
        args.extend(shlex.split(raw, posix=(os.name != "nt")))
    is_root = hasattr(os, "geteuid") and os.geteuid() == 0
    if sys.platform.startswith("linux") and is_root and "--no-sandbox" not in args:
        args.append("--no-sandbox")
    return args


def run_drawio(args, timeout=120):
    """Run draw.io with ``args``; returns stdout/stderr/returncode/elapsed."""
    exe = find_drawio()
    cmd = [exe] + list(args) + _extra_args()
    started = time.time()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"draw.io timed out after {timeout}s") from exc
    except OSError as exc:
        raise RuntimeError(f"Could not start draw.io at {exe}: {exc}") from exc
    return {
        "command": cmd,
        "returncode": proc.returncode,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
        "elapsed": round(time.time() - started, 3),
    }


def error_lines(text):
    """draw.io's own error messages (ignores Chromium log noise)."""
    return [
        line.strip() for line in (text or "").splitlines()
        if line.strip().startswith("Error")
    ]


def get_drawio_version():
    """Version string of the installed app (e.g. ``24.7.17``)."""
    result = run_drawio(["--version"], timeout=30)
    for line in result["stdout"].splitlines():
        line = line.strip()
        if line and line[0].isdigit():
            return line
    return "unknown"


def backend_info():
    """Describe the backend without raising when it is missing."""
    try:
        exe = find_drawio()
    except RuntimeError as exc:
        return {"found": False, "executable": None, "version": None,
                "help": str(exc)}
    try:
        version = get_drawio_version()
    except RuntimeError:
        version = "unknown"
    return {"found": True, "executable": exe, "version": version}


def export_file(input_path, output_path, fmt, flags=None, timeout=120):
    """Export ``input_path`` to ``output_path`` with the real draw.io app.

    ``output_path`` must not exist yet. Raises RuntimeError when draw.io
    reports an error or produces no output (the exit code is not trusted).
    """
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")
    if os.path.exists(output_path):
        raise FileExistsError(f"Export target already exists: {output_path}")
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    args = ["--export", "--format", fmt, "--output", output_path]
    args.extend(flags or [])
    args.append(input_path)
    result = run_drawio(args, timeout=timeout)

    errors = error_lines(result["stderr"]) + error_lines(result["stdout"])
    produced = os.path.isfile(output_path) and os.path.getsize(output_path) > 0
    if not produced:
        detail = "; ".join(errors)
        if not detail:
            tail = (result["stderr"].strip() or result["stdout"].strip())[-400:]
            detail = tail or "draw.io produced no output file"
        raise RuntimeError(
            f"draw.io export failed (exit code {result['returncode']}): {detail}"
        )
    return {
        "output": os.path.abspath(output_path),
        "format": fmt,
        "method": "draw.io-cli",
        "file_size": os.path.getsize(output_path),
        "elapsed_seconds": result["elapsed"],
        "warnings": errors,
    }
