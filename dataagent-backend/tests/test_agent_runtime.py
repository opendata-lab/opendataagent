from pathlib import Path
from types import SimpleNamespace
import sys
BACKEND_ROOT = Path(__file__).parents[1]
sys.path.insert(0,str(BACKEND_ROOT))
from core import agent_runtime
from config import Settings

_EXAMPLE_MCP = {'web': {'type': 'http', 'url': 'http://example-mcp:8801/mcp/', 'headers': {'X-Web-MCP-Token': 'web-token'}}}

def test_settings_defaults_query_result_limit_to_backend_max(monkeypatch):
    monkeypatch.delenv("QUERY_RESULT_LIMIT", raising=False)

    assert Settings(_env_file=None).query_result_limit == 1000

def test_build_mcp_servers_returns_empty_when_registry_row_is_disabled_or_missing(monkeypatch):
    class FakeRegistry:
        rows = []

        def list_mcp_servers(self):
            return list(self.rows)

    registry = FakeRegistry()
    monkeypatch.setattr("core.mcp_admin_service.get_runtime_registry_store", lambda: registry)
    cfg = SimpleNamespace(
        dataagent_example_mcp_enabled=True,
        dataagent_example_mcp_base_url="http://environment-must-not-fallback:8801/mcp",
        dataagent_example_mcp_token="environment-token",
    )

    assert agent_runtime._build_mcp_servers(cfg) == {}

    registry.rows = [
        {
            "server_id": "web",
            "transport": "http",
            "url": "http://registry-web:8801/mcp",
            "headers": {"X-Web-MCP-Token": "registry-token"},
            "enabled": False,
        }
    ]
    assert agent_runtime._build_mcp_servers(cfg) == {}

def test_system_prompt_template_is_markdown_file():
    prompt_path = BACKEND_ROOT / "prompts" / "data_agent_system_prompt.md"

    assert prompt_path.exists()
    assert prompt_path.suffix == ".md"
    assert "你是企业级智能问数 Data Agent" in prompt_path.read_text(encoding="utf-8")

def test_build_system_prompt_reads_markdown_and_appends_runtime_context():
    prompt = agent_runtime._build_system_prompt(
        "example",
        {"enabled_folders": ["example-business-knowledge", "example-platform-tools"]},
    )

    assert "你是企业级智能问数 Data Agent" in prompt
    assert "你不是单纯的 SQL 生成器" in prompt
    assert "# 一、硬性约束" in prompt
    assert "# 二、SQL 前确认清单" in prompt
    assert "# 三、图表输出" in prompt
    assert "# 运行时上下文" in prompt
    assert "当前已启用：example-business-knowledge、example-platform-tools" in prompt
    assert "用户显式提供的 database hint: example" in prompt
    assert "dataagent-nl2sql" not in prompt
    assert "通用 SQL skill" not in prompt
    assert "run_sql.py" not in prompt
    assert "/mnt/results/" in prompt
    assert "validate_sql.py" not in prompt
    assert "get_lineage.py" not in prompt
    assert "mcp__web" not in prompt
    assert "DATAAGENT_ALLOW_LINEAGE_SQL_FALLBACK=1" not in prompt
    assert "workflow_publish_record" not in prompt

def test_build_system_prompt_includes_methodology_and_non_negotiables():
    prompt = agent_runtime._build_system_prompt(None, {"enabled_folders": ["example-business-knowledge"]})

    required_fragments = [
        "企业级智能问数 Data Agent",
        "无论用户使用何种语言提问，必须始终使用简体中文回复",
        "不编造表、字段、指标、口径、数据结果",
        "不执行有副作用的操作",
        "文件读写只在当前会话工作区目录、以及「运行时上下文」声明的可写临时目录内进行",
        "最终交付文件必须写入工作区 `output/`",
        "不伪造成功结果",
        "先提出最小澄清问题",
        "必须显式说明默认假设",
        "已启用 Skills",
    ]
    for fragment in required_fragments:
        assert fragment in prompt

def test_build_system_prompt_omits_generic_model_known_methodology():
    prompt = agent_runtime._build_system_prompt(
        None,
        {"enabled_folders": ["example-business-knowledge", "example-platform-tools"]},
    )

    removed_fragments = [
        "任务路由",
        "标准工作流",
        "澄清规则",
        "归因分析原则",
        "失败处理",
        "先理解问题，再决定是否查语义、查元数据、生成 SQL、做分析",
        "提取指标、维度、对象、过滤条件、时间范围、比较关系、输出形式",
        "先给结论",
    ]
    for fragment in removed_fragments:
        assert fragment not in prompt
    assert "SQL 验证层" not in prompt
    assert "run_sql.py --database" not in prompt

def test_legacy_lf_system_prompt_template_is_removed():
    assert not (BACKEND_ROOT / "prompts" / "system_prompt.py").exists()


def test_dataagent_timezone_defaults_and_validates(monkeypatch):
    from pydantic import ValidationError
    import pytest

    monkeypatch.delenv("DATAAGENT_TIMEZONE", raising=False)
    assert Settings(_env_file=None).dataagent_timezone == "Asia/Shanghai"

    monkeypatch.setenv("DATAAGENT_TIMEZONE", " America/Los_Angeles ")
    assert Settings(_env_file=None).dataagent_timezone == "America/Los_Angeles"

    for invalid in ("", "   ", "Invalid/Zone", "Mars/Olympus", "UTC+8"):
        monkeypatch.setenv("DATAAGENT_TIMEZONE", invalid)
        with pytest.raises(ValidationError):
            Settings(_env_file=None)


def test_build_system_prompt_includes_business_date_and_timezone_without_clock_time(monkeypatch):
    from datetime import datetime, timezone

    # 2026-11-26T17:30:00Z is 2026-11-27 in Asia/Shanghai and 2026-11-26 in America/Los_Angeles.
    fixed_utc = datetime(2026, 11, 26, 17, 30, 45, tzinfo=timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_utc.astimezone(tz) if tz is not None else fixed_utc.replace(tzinfo=None)

    monkeypatch.setattr(agent_runtime, "datetime", FixedDateTime)

    monkeypatch.setattr(agent_runtime, "get_settings", lambda: SimpleNamespace(dataagent_timezone="Asia/Shanghai"))
    prompt_shanghai = agent_runtime._build_system_prompt(None, {"enabled_folders": ["example"]})
    assert "- 当前日期：2026-11-27（时区：Asia/Shanghai）" in prompt_shanghai
    assert "01:30" not in prompt_shanghai

    monkeypatch.setattr(agent_runtime, "get_settings", lambda: SimpleNamespace(dataagent_timezone="America/Los_Angeles"))
    prompt_la = agent_runtime._build_system_prompt(None, {"enabled_folders": ["example"]})
    assert "- 当前日期：2026-11-26（时区：America/Los_Angeles）" in prompt_la
    assert "09:30" not in prompt_la
