from io import StringIO
from typing import ClassVar

import pytest
from rich.console import Console
from rich.live import Live

from ai_agent_learning.ui.terminal import TerminalUI


class FakeLive:
    instances: ClassVar[list] = []

    def __init__(self, renderable, **kwargs):
        self.renderable = renderable
        self.options = kwargs
        self.stopped = False
        self.instances.append(self)

    def start(self, **kwargs):
        pass

    def update(self, renderable, **kwargs):
        self.renderable = renderable

    def stop(self):
        self.stopped = True


@pytest.fixture
def terminal(monkeypatch):
    monkeypatch.setenv("TERM", "xterm-256color")
    FakeLive.instances = []
    monkeypatch.setattr("ai_agent_learning.ui.terminal.Live", FakeLive)
    output = StringIO()
    console = Console(
        file=output, force_terminal=True, color_system=None, width=60, height=18
    )
    return TerminalUI(console), output


def render(renderable):
    output = StringIO()
    Console(file=output, width=60).print(renderable)
    return output.getvalue()


def test_long_answer_is_not_expanded_when_stopping_preview(monkeypatch):
    # 使用真实 Rich；FakeLive 不会复现 stop() 时的最后一次刷新。
    monkeypatch.setenv("TERM", "xterm-256color")
    output = StringIO()
    console = Console(
        file=output, force_terminal=True, color_system=None,
        width=60, height=8, legacy_windows=False,
    )

    def manual_live(*args, **kwargs):
        return Live(*args, **kwargs, auto_refresh=False)

    monkeypatch.setattr("ai_agent_learning.ui.terminal.Live", manual_live)
    ui = TerminalUI(console)
    answer = "\n\n".join(f"Paragraph {index}" for index in range(20))
    ui.text_delta(answer + "\n\nEND_OF_ANSWER")
    assert "END_OF_ANSWER" in output.getvalue()
    boundary = len(output.getvalue())
    ui.finish_response()
    ui.close_turn()

    committed = output.getvalue()[boundary:]
    assert committed.count("END_OF_ANSWER") == 1
    assert committed.count("Paragraph 0") == 1


def test_thinking_is_transient_and_replaced_by_streaming_answer(terminal):
    ui, output = terminal
    ui.begin_turn()
    ui.reasoning_delta("先核对问题，再整理答案。")
    live = FakeLive.instances[-1]
    assert live.options["transient"] is True
    assert live.options["vertical_overflow"] == "crop"
    assert "先核对问题" in render(live.renderable)
    assert output.getvalue() == ""

    ui.text_delta("**最终")
    assert "先核对问题" not in render(live.renderable)
    assert "最终" in render(live.renderable)
    ui.text_delta("答案**：42")
    with ui.console.capture() as capture:
        ui.console.print(live.renderable)
    preview = capture.get()
    assert "**" not in preview
    assert "╭" not in preview
    ui.finish_response()
    ui.close_turn()
    assert live.stopped
    assert all(item.stopped for item in FakeLive.instances)
    assert "先核对问题" not in output.getvalue()
    assert output.getvalue().count("最终答案") == 1
    assert "**" not in output.getvalue()
    assert preview == output.getvalue()


def test_thinking_only_tool_round_is_cleared_before_next_round(terminal):
    ui, output = terminal
    ui.reasoning_delta("第一轮思考")
    old_live = FakeLive.instances[-1]
    ui.finish_response()
    assert old_live.stopped
    assert output.getvalue() == ""
    ui.reasoning_delta("第二轮思考")
    assert "第一轮思考" not in render(FakeLive.instances[-1].renderable)
    ui.text_delta("正文")
    ui.finish_response()
    ui.close_turn()
    assert "思考" not in output.getvalue()
    assert output.getvalue().count("正文") == 1


def test_interruption_clears_thinking_and_preserves_partial_answer(terminal):
    ui, output = terminal
    ui.reasoning_delta("临时内容")
    ui.close_turn()
    assert output.getvalue() == ""
    ui.text_delta("部分答案")
    ui.close_turn()
    ui.close_turn()
    assert all(item.stopped for item in FakeLive.instances)
    assert "回复未完成" in output.getvalue()
    assert output.getvalue().count("部分答案") == 1


def test_long_chinese_reasoning_stays_inside_viewport(terminal):
    ui, _ = terminal
    ui.console.size = (24, 10)
    ui.reasoning_delta("很长的中文思考内容" * 500)
    live = FakeLive.instances[-1]
    rows = ui.console.render_lines(live.renderable)
    assert len(rows) <= 10
    ui.close_turn()


def test_long_answer_preview_follows_latest_text(terminal):
    ui, output = terminal
    ui.console.size = (30, 8)
    ui.text_delta("\n".join(f"旧内容 {index}" for index in range(12)))
    ui.text_delta("\n最新内容")
    live = FakeLive.instances[-1]
    preview = render(live.renderable)
    assert "最新内容" in preview
    assert "旧内容 0" not in preview
    ui.finish_response()
    assert "旧内容 0" in output.getvalue()
    assert output.getvalue().count("最新内容") == 1


def test_redirected_output_streams_text_without_reasoning_or_control_codes():
    output = StringIO()
    ui = TerminalUI(Console(file=output, force_terminal=False))
    ui.begin_turn()
    ui.reasoning_delta("不应写入重定向文件的思考")
    assert output.getvalue() == ""
    ui.text_delta("第一块")
    assert output.getvalue() == "助手: 第一块"
    ui.text_delta("第二块")
    ui.finish_response()
    ui.close_turn()
    assert output.getvalue() == "助手: 第一块第二块\n"


def test_first_setup_collects_provider_key_and_model(monkeypatch, terminal):
    ui, output = terminal
    answers = iter(["2", "custom-deepseek"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(
        "ai_agent_learning.ui.terminal.getpass", lambda prompt: "secret-key",
    )

    config = ui.setup_first_model()

    assert config is not None
    assert config.provider == "deepseek"
    assert config.api_key == "secret-key"
    assert config.model == "custom-deepseek"
    assert config.base_url == "https://api.deepseek.com"
    assert "首次启动" in output.getvalue()
