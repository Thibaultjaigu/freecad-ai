"""Widget side of #104: one start path, Stop that frees the input."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

try:
    import PySide6  # noqa: F401
except ImportError:
    try:
        import PySide2  # noqa: F401
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from freecad_ai.core.conversation import Conversation  # noqa: E402
from freecad_ai.ui import chat_widget as cw  # noqa: E402

W = cw.ChatDockWidget


def _running_worker():
    worker = MagicMock()
    worker.isRunning.return_value = True
    worker._full_response = "partial"
    return worker


def _detach_fake(worker):
    fake = SimpleNamespace(
        _worker=worker, _detached_workers=[],
        _store_tool_results=MagicMock(), conversation=MagicMock(),
        chat_display=MagicMock(), _set_loading=MagicMock(),
        _append_html=MagicMock(), _WORKER_SLOTS=W._WORKER_SLOTS)
    fake._release_detached = lambda w: W._release_detached(fake, w)
    return fake


def test_stop_interrupts_and_schedules_a_detach():
    worker = _running_worker()
    fake = SimpleNamespace(_worker=worker, _schedule_detach=MagicMock())
    W._send_message(fake)
    worker.requestInterruption.assert_called_once()
    fake._schedule_detach.assert_called_once_with(worker)


def test_a_stuck_worker_is_detached():
    worker = _running_worker()
    fake = _detach_fake(worker)
    W._detach_if_stuck(fake, worker)
    worker.detach.assert_called_once()
    worker.token_received.disconnect.assert_called()
    worker.fallback_note.disconnect.assert_called()
    fake._store_tool_results.assert_called_once_with("partial")
    fake.conversation.save.assert_called_once()
    assert fake._worker is None
    fake._set_loading.assert_called_once_with(False)
    assert fake._detached_workers == [worker]
    assert "⏹ Stopped" in fake._append_html.call_args[0][0]


def test_a_worker_that_finished_is_left_alone():
    worker = _running_worker()
    worker.isRunning.return_value = False
    fake = _detach_fake(worker)
    W._detach_if_stuck(fake, worker)
    worker.detach.assert_not_called()
    assert fake._worker is worker


def test_a_newer_worker_is_left_alone():
    old, new = _running_worker(), _running_worker()
    fake = _detach_fake(new)
    W._detach_if_stuck(fake, old)
    old.detach.assert_not_called()
    assert fake._worker is new


def test_a_detached_worker_is_released_when_it_ends():
    worker = _running_worker()
    fake = _detach_fake(worker)
    W._detach_if_stuck(fake, worker)
    finished_slot = worker.finished.connect.call_args[0][0]
    finished_slot()
    assert fake._detached_workers == []
    worker.deleteLater.assert_called_once()


def test_the_fallback_note_is_escaped():
    fake = SimpleNamespace(_append_html=MagicMock())
    W._on_fallback_note(fake, "⚠ a<b couldn't be reached — answered by c")
    html = fake._append_html.call_args[0][0]
    assert "a&lt;b" in html
    assert "color: #888" in html


def test_start_worker_connects_every_slot(monkeypatch):
    made = {}

    class _Fake:
        def __init__(self, conversation, system_prompt, **kw):
            made.update(kw, conversation=conversation, system=system_prompt)
            self.connected = []
            for signal, _ in W._WORKER_SLOTS:
                setattr(self, signal, SimpleNamespace(
                    connect=lambda slot, s=signal: self.connected.append(s)))

        def start(self):
            made["started"] = True

    monkeypatch.setattr(cw, "_LLMWorker", _Fake)
    fake = SimpleNamespace(_set_loading=MagicMock(), _append_html=MagicMock())
    for _, slot in W._WORKER_SLOTS:
        setattr(fake, slot, MagicMock())
    conv = Conversation()
    W._start_worker(fake, conv, "S", start_index=4)
    assert made["conversation"] is conv
    assert made["start_index"] == 4
    assert made["parent"] is fake
    assert made["started"] is True
    assert fake._worker.connected == [s for s, _ in W._WORKER_SLOTS]
    assert (fake._in_thinking, fake._tool_results_stored,
            fake._summary_rendered) == (False, False, False)


def test_the_retry_path_hands_over_the_conversation(monkeypatch, tmp_config_dir):
    """Bug 2: the retry rendered OpenAI style from cfg.provider.model."""
    monkeypatch.setattr("freecad_ai.core.system_prompt.build_system_prompt",
                        lambda **k: "SYS")
    from freecad_ai.config import get_config
    get_config().optimize_prompt_caching = False
    conv = Conversation(messages=[{"role": "user", "content": "go"}])
    started = {}
    fake = SimpleNamespace(
        _retry_count=0, conversation=conv, _capture_mode_override="off",
        _append_html=MagicMock(),
        mode_combo=SimpleNamespace(currentIndex=lambda: 1),
        _start_worker=lambda c, s, **kw: started.update(kw, conv=c, system=s))
    W._handle_execution_error(fake, SimpleNamespace(stderr="boom"))
    assert started["conv"] is conv
    assert started["system"] == "SYS"
    assert started["start_index"] == conv.window_start()
    assert "api_style" not in started


def test_needs_vision_comes_from_the_rendered_history():
    conv = Conversation()
    conv.add_user_message("look", images=[{"type": "image", "data": "AA",
                                           "mime_type": "image/png"}])
    conv.add_assistant_message("seen")
    conv.add_user_message("more")
    assert W._needs_vision(SimpleNamespace(supports_vision=True), conv, 0) is True
    assert W._needs_vision(SimpleNamespace(supports_vision=True), conv, 2) is False
    assert W._needs_vision(SimpleNamespace(supports_vision=False), conv, 0) is False


def test_the_session_log_carries_the_attempts(monkeypatch, tmp_config_dir):
    import json
    import os
    from freecad_ai import config as config_mod
    worker = SimpleNamespace(
        _tool_results=[{"assistant_text": "", "tool_calls": [], "results": []}],
        fallback_attempts=[{"round": 0, "profile": "a", "outcome": "failed",
                            "error": "HTTP 503"}])
    fake = SimpleNamespace(_worker=worker, conversation=Conversation(),
                           _append_html=MagicMock())
    # tmp_config_dir already points config_mod.LOGS_DIR at a temp dir;
    # chat_widget imported the real path by value, so follow it.
    monkeypatch.setattr(cw, "LOGS_DIR", config_mod.LOGS_DIR)
    W._save_session_log(fake)
    [name] = os.listdir(config_mod.LOGS_DIR)
    with open(os.path.join(config_mod.LOGS_DIR, name)) as f:
        assert json.load(f)["fallback_attempts"][0]["error"] == "HTTP 503"
