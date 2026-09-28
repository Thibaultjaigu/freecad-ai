"""The test suite must never write into the user's real config directory.

Outside FreeCAD the config dir falls back to ``~/.config/FreeCAD/FreeCADAI``,
which the workbench's every-launch sweep then renames to
``FreeCADAI.duplicate-cleanup-<ts>`` — one new folder per test run.
"""

import os

import freecad_ai.config as config_mod
# Imported at module level on purpose: this binds conversation's own copy of
# CONVERSATIONS_DIR before any fixture runs, as it is in the full suite.
from freecad_ai.core.conversation import Conversation


def _inside(path, root):
    return os.path.realpath(path).startswith(os.path.realpath(str(root)) + os.sep)


def test_the_session_config_dir_is_the_test_sandbox():
    sandbox = os.environ.get("FREECAD_AI_CONFIG_DIR")
    assert sandbox, "conftest must point FREECAD_AI_CONFIG_DIR at a temp dir"
    assert os.path.realpath(config_mod.CONFIG_DIR) == os.path.realpath(sandbox)
    real = os.path.join(os.path.expanduser("~"), ".config", "FreeCAD")
    assert not _inside(config_mod.CONFIG_DIR, real)


def test_ensure_dirs_stays_inside_tmp_config_dir(tmp_config_dir):
    config_mod._ensure_dirs()
    for name in ("CONFIG_DIR", "CONVERSATIONS_DIR", "SKILLS_DIR",
                 "USER_TOOLS_DIR", "HOOKS_DIR", "LOGS_DIR", "BACKUPS_DIR"):
        assert _inside(getattr(config_mod, name), tmp_config_dir), name


def test_conversation_save_stays_inside_tmp_config_dir(tmp_config_dir):
    conv = Conversation()
    conv.add_user_message("hi")
    conv.save()
    saved = [os.path.join(dp, f) for dp, _, fs in os.walk(str(tmp_config_dir))
             for f in fs if f.startswith(conv.conversation_id)]
    assert saved, "Conversation.save() wrote outside the fixture's temp dir"
