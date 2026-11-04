from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parents[1]))
from core.task_executor import TaskExecutionInput
from models.schemas import AgentProfileCreateRequest,AgentProfile,AgentCapabilitiesResponse
from core.agent_draft_service import configuration


def test_no_optional_basic_tool_contract():
    assert 'allowed_tools' not in AgentProfileCreateRequest.model_fields
    assert 'allowed_tools' not in AgentProfile.model_fields
    assert 'tools' not in AgentCapabilitiesResponse.model_fields
    assert 'allowed_tools' not in configuration({'allowed_tools':['Bash']})
    assert 'allowed_tools' not in TaskExecutionInput.__dataclass_fields__
