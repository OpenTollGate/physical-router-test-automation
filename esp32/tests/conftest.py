import pytest


def pytest_collection_modifyitems(config, items):
    board = config.getoption("--board")
    if not board:
        return
    skip = pytest.mark.skip(reason=f"Not selected (running board {board})")
    board_markers = {"a": "board_a", "b": "board_b", "c": "board_c"}
    selected_marker = board_markers.get(board)
    if not selected_marker:
        return
    for item in items:
        has_board_marker = any(
            m.name in board_markers.values() for m in item.iter_markers()
        )
        if has_board_marker and not any(m.name == selected_marker for m in item.iter_markers()):
            item.add_marker(skip)
