"""Isolate reviewed vendor skills and prevent credential-bearing stdout escaping."""
import asyncio
from contextlib import redirect_stdout, redirect_stderr
import importlib.util
import io
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from investment_agent.common import safe


async def run(name: str, query: str) -> dict:
    if name not in {"stock-fundamental-analysis", "mx-stocks-screener"}:
        return {"ok": False, "code": "NOT_ALLOWED", "message": "Skill 不在允许列表。"}
    path = ROOT / "mx-skills" / name / "scripts/get_data.py"
    spec = importlib.util.spec_from_file_location("vendor_runtime", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = ROOT / "data/skills" / name
    if name == "stock-fundamental-analysis":
        result = await module.analyze_stock_fundamental(query, output_dir=output, save_to_file=True)
    else:
        result = await module.query_mx_stocks_screener(query, selectType="A股", output_dir=output)
    if result.get("need_auth"):
        return {"ok": False, "code": "AUTH_REQUIRED", "message": "妙想 Skill 需要重新授权，请在官方页面处理。"}
    if result.get("error"):
        return {"ok": False, "code": "SKILL_FAILED", "message": str(result["error"])}
    if name == "stock-fundamental-analysis":
        content = result.get("content", "")
        if not content.strip() or any(x in content for x in ("暂时不支持此类场景", "不支持此类问题")):
            return {"ok": False, "code": "UNSUPPORTED", "message": "Skill 返回空内容或场景不支持。"}
        data = {"original_markdown": content, "data_markdown": result.get("data_markdown", "")}
        artifacts = [result[k] for k in ("output_path", "data_md_path") if result.get(k)]
    else:
        import pandas as pd
        p = Path(result["csv_path"]).resolve()
        if not p.is_relative_to(output.resolve()):
            raise ValueError("Unexpected output path")
        frame = pd.read_csv(p, dtype=str).fillna("")
        data = {"rows": frame.head(150).to_dict(orient="records"), "total_rows": len(frame), "description": query,
                "truncated": len(frame)>150}
        artifacts = [str(p)]
    return {"ok": True, "data": data, "artifacts": artifacts}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        # Raw vendor authorization/logging output is intentionally discarded.
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = asyncio.run(run(sys.argv[1], request["query"]))
        print(json.dumps(safe(result, (os.getenv("EM_API_KEY", ""),)), ensure_ascii=True))
    except Exception as exc:
        print(json.dumps({"ok": False, "code": "WORKER_ERROR", "message": type(exc).__name__}))


if __name__ == "__main__":
    main()
