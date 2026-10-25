from dataclasses import dataclass, field
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DEEPSEEK_MODEL_ID = "deepseek-flash"
DEEPSEEK_MODEL_LABEL = "DeepSeek-V4.1-Flash-1017"


def flag(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"true", "1", "yes"}


@dataclass
class Settings:
    root: Path = ROOT
    deepseek_key: str = field(default="", repr=False)
    mcp_key: str = field(default="", repr=False)
    skills_key: str = field(default="", repr=False)
    model: str = DEEPSEEK_MODEL_ID
    reasoning_effort: str = "high"
    deepseek_url: str = "https://api.deepseek.com"
    enable_miaoxiang: bool = True
    enable_choice: bool = False
    enable_public: bool = True
    max_calls: int = 6
    max_rounds: int = 5
    max_output_tokens: int = 16384
    max_results: int = 5
    account_context_key: str = ""
    investment_style: str = "value"
    research_pool_limit: int = 150
    research_top_n: int = 5

    def __post_init__(self):
        # The official endpoint serves the 1017 snapshot through this canonical model ID.
        if self.model.lower() in {"deepseek-v4.1-flash-1017", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp"}:
            self.model = DEEPSEEK_MODEL_ID
        if self.reasoning_effort not in {"low", "high", "max"}:
            raise ValueError("DEEPSEEK_REASONING_EFFORT must be low, high or max")

    @classmethod
    def load(cls) -> "Settings":
        load_dotenv(ROOT / ".env", override=False)
        return cls(deepseek_key=os.getenv("DEEPSEEK_API_KEY", "").strip(),
                   mcp_key=os.getenv("MIAOXIANG_MCP_API_KEY", os.getenv("EM_API_KEY", "")).strip(),
                   skills_key=os.getenv("MIAOXIANG_SKILLS_API_KEY", os.getenv("EM_API_KEY", "")).strip(),
                   model=os.getenv("DEEPSEEK_MODEL", DEEPSEEK_MODEL_ID).strip(),
                   reasoning_effort=os.getenv("DEEPSEEK_REASONING_EFFORT", "high").strip().lower(),
                   deepseek_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
                   enable_miaoxiang=flag("ENABLE_MIAOXIANG", True),
                   enable_choice=flag("ENABLE_CHOICE", False),
                   enable_public=flag("ENABLE_PUBLIC_DATA", True),
                   max_calls=max(1, min(12, int(os.getenv("MAX_TOOL_CALLS_PER_REQUEST", "6")))),
                   max_rounds=max(2, min(8, int(os.getenv("MAX_MODEL_ROUNDS", "5")))),
                   max_output_tokens=max(256, min(65536, int(os.getenv("MAX_OUTPUT_TOKENS", "16384")))))
