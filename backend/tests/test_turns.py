import threading

import turns


def test_turn_keeps_running_in_the_background_and_reports_its_result():
    release = threading.Event()
    seen = []

    def work(turn):
        turn.on_status("מחשב...")
        turn.on_step()
        release.wait(5)
        seen.append("done")

    turn = turns.start("t-bg", work)
    assert turns.start("t-bg", lambda t: seen.append("second")) is turn      # תור שרץ לא מופעל פעמיים
    assert not turn.done and turns.finish("t-bg") is None                   # עוד לא הסתיים
    release.set()
    for _ in range(100):
        if turn.done:
            break
        threading.Event().wait(0.05)
    assert turn.done and turn.status == "מחשב..." and turn.steps == 1 and seen == ["done"]
    assert turns.finish("t-bg") is turn and turns.get("t-bg") is None


def test_turn_error_is_handed_to_the_screen():
    def boom(turn):
        raise RuntimeError("שירות לא זמין")

    turn = turns.start("t-err", boom)
    for _ in range(100):
        if turn.done:
            break
        threading.Event().wait(0.05)
    assert isinstance(turns.finish("t-err").error, RuntimeError)
