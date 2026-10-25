from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


def test_ui_renders_chat_and_report_without_network(tmp_path):
    async def ask(self, question, **kwargs):
        return {"report": "测试来源报告[S1]", "created_at": "2026-10-19T16:00:00+08:00", "model": "test-double",
                "data_calls": 0, "trace": [], "report_file": str(tmp_path / "example.md")}
    from investment_agent.config import Settings
    with patch("investment_agent.config.Settings.load",return_value=Settings(root=tmp_path)), patch("investment_agent.agent.ResearchAgent.ask", ask):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=20)
        assert not app.exception
        app.radio[0].set_value('研究对话').run(timeout=20)
        assert len(app.chat_input) == 1 and len(app.toggle) == 4
        assert not next(x for x in app.toggle if x.label.startswith("Choice")).value  # Choice must default off.
        app.chat_input[0].set_value("测试查询").run(timeout=20)
        from investment_agent.chat_ui import jobs
        for future in list(jobs(str(tmp_path)).futures.values()): future.result(timeout=20)
        app.run(timeout=20)
        assert not app.exception
        assert any("测试来源报告" in element.value for element in app.markdown)
