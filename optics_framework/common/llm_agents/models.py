"""
Pydantic models for the LLM agent integration.
"""
import os
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field, field_validator
import yaml


class LLMAgentConfig(BaseModel):
    """Configuration for an LLM agent."""
    enabled: bool = False
    url: Optional[str] = None
    capabilities: Dict[str, Any] = Field(default_factory=dict)
    trigger: str = "Keyword_fail"  # Can be any entity_type + "_" + status
    system_prompt: Optional[str] = None
    user_prompt: Optional[str] = None
    max_tokens: Optional[int] = 1000
    temperature: float = 0.7
    prompt_file: Optional[str] = None  # Will be auto-populated if not specified
    # Add additional field to store temporary discovered prompt file
    prompt_source_file: Optional[str] = Field(default=None, exclude=True)

    @field_validator('prompt_file')
    def validate_prompt_file(cls, v: Optional[str], info):
        if v and not os.path.exists(v):
            # Don't raise an error - the file might be a relative path
            # that will be resolved later
            pass
        return v


class AgentPrompt(BaseModel):
    """Model for agent prompts loaded from a file."""
    system_prompt: Optional[str] = None
    user_prompt: Optional[str] = None
    max_tokens: Optional[int] = None


class Tool(BaseModel):
    """A tool that can be used by the LLM agent."""
    name: str
    description: str
    parameters: Dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """The result of a tool execution."""
    tool_name: str
    success: bool
    result: Any
    error: Optional[str] = None


class AgentAction(BaseModel):
    """An action requested by the LLM agent."""
    tool_name: str
    parameters: Dict[str, Any] = Field(default_factory=dict)


class AgentResponse(BaseModel):
    """Response from an LLM agent."""
    message: str
    actions: List[AgentAction] = Field(default_factory=list)
    requires_human_input: bool = False


class AgentContext(BaseModel):
    """Context information passed to the LLM agent."""
    event_type: str
    entity_type: str
    entity_id: str
    entity_name: str
    status: str
    message: str
    session_id: str
    screenshot_path: Optional[str] = None
    available_tools: List[Tool] = Field(default_factory=list)
    execution_history: List[Dict[str, Any]] = Field(default_factory=list)


def load_prompt_from_file(file_path: str, agent_name: str) -> AgentPrompt:
    """Load agent prompts from a YAML file.

    Args:
        file_path: Path to the YAML file containing prompts
        agent_name: Name of the agent to load prompts for

    Returns:
        AgentPrompt instance with the loaded prompts
    """
    if not file_path or not os.path.exists(file_path):
        raise FileNotFoundError(f"Prompt file not found: {file_path}")

    if not agent_name:
        raise ValueError("Agent name is required for loading prompts from file")

    with open(file_path, 'r', encoding='utf-8') as file:
        data = yaml.safe_load(file) or {}

    # Check for new multi-agent format (preferred)
    if 'agents' in data and agent_name in data['agents']:
        agent_data = data['agents'][agent_name]
        return AgentPrompt(**agent_data)

    # Handle legacy llm_agent_prompt format
    if 'llm_agent_prompt' in data and agent_name in data['llm_agent_prompt']:
        return AgentPrompt(**data['llm_agent_prompt'][agent_name])

    # If we got here and couldn't find the agent in the file, raise an error
    raise ValueError(f"Agent '{agent_name}' not found in prompt file {file_path}")
