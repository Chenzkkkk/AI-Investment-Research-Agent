from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


def test_ui_renders_chat_and_report_without_network(tmp_path):
    async def ask(self, question, **kwargs):
        return {"report": "测试来源报告[S1]", "created_at": "2026-10-19T16:00:00+08:00", "model": "test-double",
                "data_calls": 0, "trace": [], "report_file": str(tmp_path / "example.md")}
    with patch("investment_agent.agent.ResearchAgent.ask", ask):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=20)
        assert not app.exception
        app.radio[0].set_value('研究对话').run(timeout=20)
        assert len(app.chat_input) == 1 and len(app.toggle) == 3
        assert not app.toggle[2].value  # Choice must default off.
        app.chat_input[0].set_value("测试查询").run(timeout=20)
        assert not app.exception
        assert any("测试来源报告" in element.value for element in app.markdown)
