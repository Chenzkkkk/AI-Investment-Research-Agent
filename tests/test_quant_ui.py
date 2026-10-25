from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from investment_agent.config import Settings
from investment_agent.quant_data import DatasetStore


@pytest.mark.parametrize("page",["数据与技能","因子与回测","模拟组合","日报与复盘"])
def test_phase2_pages_and_backtest_render_without_network(tmp_path,page):
    rows=[]
    for i,d in enumerate(pd.bdate_range("2025-01-02",periods=45)):
        rows.append({"date":str(d.date()),"symbol":"000001.SZ","open":10+i*.01,"close":10+i*.01,
            "high":11+i*.01,"low":9+i*.01,"volume":1e7,"tradable":True,"can_buy":True,"can_sell":True})
    DatasetStore(tmp_path).save(pd.DataFrame(rows),source="synthetic-ui-test",adjustment="raw",synthetic=True)
    settings=Settings(root=tmp_path,enable_choice=False,enable_miaoxiang=False,enable_public=False)
    with patch("investment_agent.config.Settings.load",return_value=settings):
        app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/"app.py")).run(timeout=20)
        next(w for w in app.selectbox if w.label=='专业工作区').set_value(page).run(timeout=20)
        assert not app.exception
        assert not app.toggle[2].value
        if page=="因子与回测":
            next(b for b in app.button if b.label=="运行回测与样本外验证").click().run(timeout=30)
            assert not app.exception and len(app.metric)==4
            assert any("成交笔数" in m.label for m in app.metric)
        if page=="日报与复盘":
            next(b for b in app.button if b.label=="生成快照研究日报").click().run(timeout=20)
            assert not app.exception
            assert any("合成示例" in m.value for m in app.markdown)
