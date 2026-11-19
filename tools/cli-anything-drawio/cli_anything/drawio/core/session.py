"""Stateful session: the open project, modified flag and undo/redo.

Undo history survives between one-shot invocations: it is written to
``<state dir>/sessions/<sha1 of project path>.json`` together with the
SHA-256 of the project file as saved. When the project is opened again the
history is restored only if the file still has that hash, so edits made in
the GUI (or by anything else) invalidate it instead of corrupting the undo
chain.
"""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager

from cli_anything.drawio.core import project as proj
from cli_anything.drawio.utils import drawio_xml as dx
from cli_anything.drawio.utils import fileio

ENV_STATE_DIR = "CLI_ANYTHING_DRAWIO_STATE_DIR"
MAX_UNDO = 50


def _locked_save_json(path, data, **dump_kwargs) -> None:
    """Write JSON with exclusive file locking (session-locking guide)."""
    fileio.locked_save_json(path, data, **dump_kwargs)


def state_dir():
    override = os.environ.get(ENV_STATE_DIR, "").strip()
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".cli-anything-drawio")


def history_path(project_path):
    key = os.path.normcase(os.path.abspath(project_path))
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return os.path.join(state_dir(), "sessions", digest + ".json")


def _file_hash(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class Session:
    """Holds one open project and its undo/redo stacks."""

    def __init__(self):
        self.root = None
        self.project_path = None
        self.current_page = 0
        self._modified = False
        self._undo = []   # list of (label, xml)
        self._redo = []

    # ── project ──────────────────────────────────────────────────────

    def has_project(self):
        return self.root is not None

    def set_project(self, root, path=None, load_history=True):
        self.root = root
        self.project_path = os.path.abspath(path) if path else None
        self.current_page = 0
        self._modified = False
        self._undo = []
        self._redo = []
        if load_history and self.project_path:
            self._load_history()

    def get_root(self):
        if self.root is None:
            raise ValueError(
                "No project loaded. Pass --project FILE.drawio "
                "(create one with: project new -o FILE.drawio)."
            )
        return self.root

    def close(self):
        self.__init__()

    # ── undo / redo ──────────────────────────────────────────────────

    def _dump(self):
        return dx.serialize(self.root, pretty=False)

    def snapshot(self, label):
        """Record the current state before a mutation."""
        self.get_root()
        self._undo.append((label, self._dump()))
        if len(self._undo) > MAX_UNDO:
            del self._undo[: len(self._undo) - MAX_UNDO]
        self._redo = []

    def mark_modified(self):
        self._modified = True

    @contextmanager
    def transaction(self, label):
        """Snapshot, run a mutation, and roll back completely if it fails."""
        self.get_root()
        before = self._dump()
        saved_redo = list(self._redo)
        self.snapshot(label)
        try:
            yield self.root
        except Exception:
            self._undo.pop()
            self._redo = saved_redo
            self.root = dx.parse_xml(before)
            raise
        self._modified = True

    def undo(self):
        if not self._undo:
            raise ValueError("Nothing to undo.")
        label, xml = self._undo.pop()
        self._redo.append((label, self._dump()))
        self.root = dx.parse_xml(xml)
        self._clamp_page()
        self._modified = True
        return {"undone": label, "undo_levels": len(self._undo),
                "redo_levels": len(self._redo)}

    def redo(self):
        if not self._redo:
            raise ValueError("Nothing to redo.")
        label, xml = self._redo.pop()
        self._undo.append((label, self._dump()))
        self.root = dx.parse_xml(xml)
        self._clamp_page()
        self._modified = True
        return {"redone": label, "undo_levels": len(self._undo),
                "redo_levels": len(self._redo)}

    def _clamp_page(self):
        count = len(proj.get_pages(self.root))
        if self.current_page >= count:
            self.current_page = count - 1

    def history(self):
        return {
            "undo": [label for label, _ in self._undo],
            "redo": [label for label, _ in reversed(self._redo)],
            "undo_levels": len(self._undo),
            "redo_levels": len(self._redo),
            "max_levels": MAX_UNDO,
        }

    def status(self):
        info = {
            "has_project": self.has_project(),
            "project_path": self.project_path,
            "modified": self._modified,
            "current_page": self.current_page,
            "undo_levels": len(self._undo),
            "redo_levels": len(self._redo),
        }
        if self.has_project():
            pages = proj.get_pages(self.root)
            info["page_count"] = len(pages)
            info["current_page_name"] = pages[self.current_page].get("name")
        return info

    # ── persistence ──────────────────────────────────────────────────

    def save_session(self, path=None, compressed=False):
        """Save the project file and persist the undo history."""
        self.get_root()
        target = path or self.project_path
        if not target:
            raise ValueError(
                "The project has no file path yet. Use: project save -o FILE.drawio"
            )
        saved = proj.save_project(self.root, target, compressed=compressed)
        self.project_path = saved
        self._modified = False
        self._persist_history()
        return saved

    def _persist_history(self):
        if not self.project_path:
            return
        data = {
            "project_path": self.project_path,
            "head_sha256": _file_hash(self.project_path),
            "undo": [{"label": label, "xml": xml} for label, xml in self._undo],
            "redo": [{"label": label, "xml": xml} for label, xml in self._redo],
        }
        _locked_save_json(history_path(self.project_path), data)

    def _load_history(self):
        path = history_path(self.project_path)
        if not os.path.isfile(path) or not os.path.isfile(self.project_path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("head_sha256") != _file_hash(self.project_path):
                return  # file changed elsewhere — history no longer applies
            self._undo = [(e["label"], e["xml"]) for e in data.get("undo", [])]
            self._redo = [(e["label"], e["xml"]) for e in data.get("redo", [])]
        except (OSError, ValueError, KeyError, TypeError):
            self._undo, self._redo = [], []

    def clear_history(self):
        self._undo, self._redo = [], []
        if self.project_path:
            path = history_path(self.project_path)
            if os.path.isfile(path):
                os.remove(path)
