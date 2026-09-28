"""A turn pins its truncation window and records rounds on a copy (#104)."""

from freecad_ai.core.conversation import Conversation


def _long(n=6, size=40):
    conv = Conversation()
    for i in range(n):
        conv.add_user_message(f"u{i}" + "x" * size)
        conv.add_assistant_message(f"a{i}" + "y" * size)
    return conv


def test_default_output_is_unchanged():
    conv = _long()
    assert conv.get_messages_for_api(max_chars=200) == \
        conv.get_messages_for_api(max_chars=200,
                                  start_index=conv.window_start(200))


def test_window_start_is_the_first_kept_message():
    conv = _long()
    start = conv.window_start(200)
    rendered = conv.get_messages_for_api(max_chars=200)
    assert rendered[0]["content"] == conv.messages[start]["content"]


def test_a_pinned_start_ignores_later_growth():
    conv = _long()
    start = conv.window_start(200)
    before = conv.get_messages_for_api(start_index=start)
    conv.add_user_message("z" * 500)
    after = conv.get_messages_for_api(start_index=start)
    assert after[:len(before)] == before


def test_a_pinned_start_still_opens_on_a_user_message():
    conv = _long()
    rendered = conv.get_messages_for_api(start_index=1)   # an assistant
    assert rendered[0]["role"] == "user"


def test_the_fork_shares_history_but_not_the_list():
    conv = _long(1)
    fork = conv.fork_for_turn()
    fork.add_tool_result("t1", "ok")
    assert len(conv.messages) == 2
    assert len(fork.messages) == 3
    assert fork.conversation_id == conv.conversation_id


def test_has_images_looks_from_the_start_index():
    conv = Conversation()
    conv.add_user_message("look", images=[{"type": "image", "data": "AAAA",
                                           "mime_type": "image/png"}])
    conv.add_assistant_message("seen")
    conv.add_user_message("again")
    assert conv.has_images() is True
    assert conv.has_images(start_index=2) is False
