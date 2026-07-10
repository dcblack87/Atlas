"""Copy support: StreamLog retains content; copy keys reach the clipboard."""

from atlas.app import AtlasApp
from atlas.tui.widgets.stream_log import StreamLog


async def test_stream_log_retains_history_across_modes() -> None:
    app = AtlasApp(None, demo=True)
    async with app.run_test():
        log = StreamLog()
        await app.screen.mount(log)
        log.push("live line")  # unbuffered mode
        log.set_flush_period(5.0)
        log.push("buffered line")  # buffered mode, not yet flushed
        assert log.text == "live line\nbuffered line"
        log.finish()
        assert log.text == "live line\nbuffered line"
        log.clear()
        assert log.text == ""


async def test_copy_key_on_logs_screen() -> None:
    copied: list[str] = []
    app = AtlasApp(None, demo=True)
    app.copy_to_clipboard = copied.append  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("l")
        log = app.screen.query_one("#log", StreamLog)
        log.push("some log output")
        await pilot.press("c")
        assert copied == ["some log output"]
