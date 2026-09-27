"""SettingsPage: load a baseline, write back only what changed.

apply_to() writes onto the config it is given, the live singleton at save
time, and only the fields that differ from what load() showed. FreeCAD
calls saveSettings() on every Preferences page in turn, so a page that
wrote all its fields would undo the edits a page before it just saved.
The same rule keeps a hand-edited value a combo cannot show (say
thinking: "max") from being overwritten by an unrelated OK.
"""

import copy

from ..compat import QtCore, QtWidgets


class SettingsPage(QtWidgets.QWidget):
    # True = save, then close the host window; False = discard and close.
    # Raised by the editor prompt (ToolsPage), since FreeCAD's docked script
    # editor is unreachable behind a modal window.
    closeHostRequested = QtCore.Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        # None, not {}: "no successful load" — apply_to writes nothing (#99).
        self._baseline = None

    def load(self, cfg, label=None):
        """Show ``cfg`` and record the baseline. Raises if showing fails,
        leaving the baseline None so the page writes nothing."""
        self._baseline = None
        self._show(cfg, label)
        self._baseline = copy.deepcopy(self._values())

    def is_dirty(self) -> bool:
        return self._baseline is not None and self._values() != self._baseline

    def apply_to(self, cfg) -> None:
        if self._baseline is None:
            return
        for key, value in self._values().items():
            if self._baseline.get(key) != value:
                setattr(cfg, key, copy.deepcopy(value))

    def after_save(self, cfg) -> None:
        """Side effects that must follow a save in either window."""

    def view_label(self):
        """What to keep showing across a re-baseline (ProviderPage only)."""
        return None

    def _show(self, cfg, label) -> None:
        raise NotImplementedError

    def _values(self) -> dict:
        return {}
