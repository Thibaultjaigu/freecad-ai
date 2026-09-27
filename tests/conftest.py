"""Shared fixtures for FreeCAD AI tests."""

import os
import sys

import pytest

# Add project root to path so `freecad_ai` package is importable
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture
def tmp_config_dir(tmp_path, monkeypatch):
    """Redirect all config paths to a temp directory."""
    import freecad_ai.config as config_mod

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    conv_dir = tmp_path / "conversations"
    conv_dir.mkdir()
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()

    monkeypatch.setattr(config_mod, "CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(config_mod, "CONFIG_FILE", str(config_dir / "config.json"))
    monkeypatch.setattr(config_mod, "CONVERSATIONS_DIR", str(conv_dir))
    monkeypatch.setattr(config_mod, "SKILLS_DIR", str(skills_dir))
    monkeypatch.setattr(config_mod, "LOGS_DIR", str(logs_dir))

    return tmp_path


@pytest.fixture(autouse=True)
def reset_config_singleton():
    """Reset the config singleton and its listeners after each test."""
    yield
    import freecad_ai.config as config_mod
    config_mod._config = None
    config_mod._config_listeners.clear()


@pytest.fixture
def mock_skills_dir(tmp_path):
    """Create a temp skills directory with sample skills."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    # Create a sample skill
    sample = skills_dir / "test-skill"
    sample.mkdir()
    (sample / "SKILL.md").write_text(
        "# Test Skill\n\nA sample skill for testing.\n\nDo something useful.\n"
    )

    # Create a skill with a handler
    handled = skills_dir / "handled-skill"
    handled.mkdir()
    (handled / "SKILL.md").write_text(
        "# Handled Skill\n\nSkill with a Python handler.\n"
    )
    (handled / "handler.py").write_text(
        'def execute(args):\n    return {"output": f"Handled: {args}"}\n'
    )

    return skills_dir
