"""
Client for interacting with Pydantic AI services.
"""
import json
import os
import httpx
from typing import Dict, Any, List, Callable
from pydantic import ValidationError

# Import Pydantic AI components
from pydantic_ai import Agent

from optics_framework.common.logging_config import internal_logger
from optics_framework.common.llm_agents.models import (
    AgentContext, AgentResponse, LLMAgentConfig, AgentPrompt,
    load_prompt_from_file
)


class PydanticAIClient:
    """Client for interacting with Pydantic AI services."""

    def __init__(self, config: LLMAgentConfig):
        """Initialize the Pydantic AI client with the given configuration.

        Args:
            config: Configuration for the LLM agent
        """
        self.config = config
        self.url = config.url
        self.prompt = self._load_prompts()
        self.agent = None  # Will be created when tools are registered
        self.tools = {}
        self.client = httpx.AsyncClient(timeout=30.0)  # 30 second timeout

    def _load_prompts(self) -> AgentPrompt:
        """Load prompts from file or use the ones in config."""
        # Extract agent name from the client to use with multi-agent prompt files
        agent_name = getattr(self, 'agent_name', None)

        if self.config.prompt_file:
            try:
                return load_prompt_from_file(self.config.prompt_file, agent_name)
            except (FileNotFoundError, ValidationError) as e:
                internal_logger.error(f"Failed to load prompt file: {e}")

        # Fall back to prompts in config
        return AgentPrompt(
            system_prompt=self.config.system_prompt,
            user_prompt=self.config.user_prompt,
            max_tokens=self.config.max_tokens
        )

    def register_tools(self, tools: Dict[str, Callable]) -> None:
        """Register tools with the Pydantic AI agent.

        Args:
            tools: A dictionary mapping tool names to their implementations
        """
        # Store tools for reference
        for name, func in tools.items():
            if name.startswith('_'):
                continue  # Skip private methods

            self.tools[name] = func

        # Process the model name and configuration
        try:
            # Get the model name from capabilities or use default
            model_name = self.config.capabilities.get("model", "gpt-3.5-turbo")

            # Process any environment variables in the capabilities
            processed_capabilities = {}
            for key, value in self.config.capabilities.items():
                if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
                    # It's an environment variable reference
                    env_var = value[2:-1]
                    env_value = os.environ.get(env_var)
                    if env_value:
                        processed_capabilities[key] = env_value
                    else:
                        internal_logger.warning(f"Environment variable {env_var} not found")
                else:
                    processed_capabilities[key] = value

            # Add API base URL if specified
            if self.url:
                processed_capabilities["api_base"] = self.url

            # Get system prompt
            system_prompt = self.prompt.system_prompt or "You are an AI assistant helping with test automation."

            # Create the Agent with model name and config options
            # Pydantic AI will handle the provider and authentication automatically
            agent_kwargs = {
                "system_prompt": system_prompt,
            }

            # Add optional configuration parameters if present
            if self.config.temperature is not None:
                agent_kwargs["temperature"] = self.config.temperature

            if self.prompt.max_tokens is not None:
                agent_kwargs["max_tokens"] = self.prompt.max_tokens

            # Add any API keys or other provider-specific configurations
            for key, value in processed_capabilities.items():
                if key != "model":  # Model is passed separately
                    agent_kwargs[key] = value

            # Create the agent with the Pydantic AI library
            self.agent = Agent(model_name, **agent_kwargs)

            # Register tools with the agent
            for name, func in self.tools.items():
                # Register function as a tool
                self.agent.tool(func)

            internal_logger.info(f"Registered {len(self.tools)} tools with Pydantic AI agent for model {model_name}")

        except Exception as e:
            internal_logger.error(f"Failed to create Pydantic AI agent: {str(e)}", exc_info=True)
            raise

    async def prepare_request(self, context: AgentContext) -> Dict[str, Any]:
        """Prepare the request payload for the LLM API.

        Args:
            context: The context for the agent request

        Returns:
            The prepared request payload
        """
        system_prompt = self.prompt.system_prompt or (
            "You are an AI assistant helping with test automation tasks. "
            "You have access to various tools to help with your tasks."
        )

        user_prompt = self.prompt.user_prompt or (
            "The test automation has encountered an event that requires your attention. "
            "Review the information provided and take appropriate action using available tools."
        )

        # Enhance user prompt with context information
        context_str = (
            f"Event: {context.event_type}\n"
            f"Entity: {context.entity_type} - {context.entity_name}\n"
            f"Status: {context.status}\n"
            f"Message: {context.message}\n"
        )

        if context.screenshot_path:
            context_str += f"Screenshot is available at: {context.screenshot_path}\n"

        tools_str = "Available tools:\n"
        for tool in context.available_tools:
            params_str = ", ".join(f"{k}: {v}" for k, v in tool.parameters.items())
            tools_str += f"- {tool.name}({params_str}): {tool.description}\n"

        enhanced_user_prompt = f"{user_prompt}\n\n{context_str}\n{tools_str}"

        return {
            "model": "pydantic-ai",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": enhanced_user_prompt}
            ],
            "max_tokens": self.prompt.max_tokens or self.config.max_tokens,
            "temperature": self.config.temperature
        }

    async def execute(self, context: AgentContext) -> AgentResponse:
        """Execute the agent with the given context.

        Args:
            context: The context for the agent request

        Returns:
            The agent's response
        """
        if not self.config.enabled:
            return AgentResponse(message="LLM Agent is disabled")

        if not self.agent:
            internal_logger.error("Agent not initialized. Tools must be registered first.")
            return AgentResponse(
                message="Agent not properly initialized with tools",
                requires_human_input=True
            )

        try:
            # Build context information with event details
            context_str = (
                f"Event: {context.event_type}\n"
                f"Entity: {context.entity_type} - {context.entity_name}\n"
                f"Status: {context.status}\n"
                f"Message: {context.message}\n"
            )

            # Add screenshot path if available
            if context.screenshot_path:
                context_str += f"Screenshot is available at: {context.screenshot_path}\n"

            # Get user prompt from config or use default
            user_prompt = self.prompt.user_prompt or (
                "The test automation has encountered an event that requires your attention. "
                "Review the information provided and take appropriate action using available tools."
            )

            # Combine prompt and context
            query = f"{user_prompt}\n\n{context_str}"

            internal_logger.debug(f"Executing Pydantic AI agent with query: {query}")

            # Execute the agent using Pydantic AI's native run method
            # This method automatically handles tool calls and responses
            response = await self.agent.run(query)

            # Convert the response to our internal format
            message = str(response)

            # Extract any information about tool usage, if available in response
            # (Format will depend on the Pydantic AI version and implementation)
            actions = []

            # Create the response
            agent_response = AgentResponse(
                message=message,
                actions=actions,
                requires_human_input=False
            )

            internal_logger.info(f"Agent executed successfully: {message[:100]}...")
            return agent_response

        except Exception as e:
            internal_logger.error(f"Failed to execute LLM agent: {str(e)}", exc_info=True)
            return AgentResponse(
                message=f"Error executing LLM agent: {str(e)}",
                requires_human_input=True
            )

    def _extract_actions(self, message: str) -> List[Dict[str, Any]]:
        """Extract actions from the LLM response.

        This is a simple implementation that looks for JSON-like tool calls.
        A more robust implementation might use structured output from the LLM.

        Args:
            message: The LLM response message

        Returns:
            List of extracted actions
        """
        actions = []
        # Look for blocks that might be tool calls
        # This is a simple implementation, actual extraction depends on the LLM output format
        try:
            # Check if there are JSON blocks in the message that look like tool calls
            import re
            json_blocks = re.findall(r'```json\n(.*?)\n```', message, re.DOTALL)

            for block in json_blocks:
                try:
                    data = json.loads(block)
                    if isinstance(data, dict) and "tool_name" in data and "parameters" in data:
                        actions.append(data)
                except json.JSONDecodeError:
                    pass
        except Exception as e:
            internal_logger.error(f"Failed to extract actions: {e}")

        return actions
