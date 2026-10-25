# Architecture

Streamlit presents three primary destinations: discovery, research and intelligence. Advanced tools contain local history, detailed research, factors and simulation. The discovery page reads only `examples/technology-research.json`; it never chooses the newest personal report.

Research routes a question into a sector scan or explicitly named securities. Public financial statements and quotes feed deterministic Python filters. Up to 150 candidates are considered, then up to five receive fundamental, technical, event, bullish, bearish and risk-review analyses. Native DeepSeek calls consume evidence packets; an external TradingAgents deployment is not required. Reasoning defaults to high and can be changed in the UI. Displayed sources link to original evidence.

Technology themes map to disclosed industry categories as a discovery proxy. The capped pool is selected round-robin by industry and by security code within industry, not by estimated returns. Business relevance still needs filing verification. Value screening emphasizes positive earnings, operating cash flow and peer-relative valuation. Model review may reject every screened candidate.

Frontier intelligence uses httpx, feedparser and Trafilatura with SQLite WAL. It retains article provenance, event versions, source failures, follows and bounded model budgets. Search, event detail and topic brief tools are read-only. Running `python -m investment_agent.intelligence.worker` performs a bounded due-task cycle. Scheduling is opt-in and never starts merely by opening a web page. Historical prices retain their existing DuckDB cache.

Choices, statements and prices are dated. Missing data stays visible. Scores are not return predictions; causal price claims require separate evidence. Trading and paid account activation are not part of the default workflow.
