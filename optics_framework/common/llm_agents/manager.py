"""
Manager for LLM agents and their triggers.
"""
import os
import asyncio
from typing import Dict, List, Optional, Callable
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.events import Event, EventSubscriber, get_event_manager, CommandType
from optics_framework.common.llm_agents.models import (
    LLMAgentConfig, AgentContext, Tool, AgentAction
)
from optics_framework.common.llm_agents.client import PydanticAIClient
from optics_framework.common.runner.keyword_register import KeywordRegistry


class LLMAgentManager(EventSubscriber):
    """Manager for LLM agents that integrates with the event system."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(LLMAgentManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self.agents: Dict[str, PydanticAIClient] = {}
            self.configs: Dict[str, LLMAgentConfig] = {}
            self.event_manager = get_event_manager()
            # Map of entity_type + "_" + status to agent names
            self.trigger_map: Dict[str, List[str]] = {}
            self.keyword_registry: Optional[KeywordRegistry] = None
            self.session_tools: Dict[str, Dict[str, Callable]] = {}
            self._initialized = True

    def register_agent(self, name: str, config: LLMAgentConfig) -> None:
        """Register an LLM agent with the manager.

        Args:
            name: Name of the agent
            config: Configuration for the agent
        """
        if not config.enabled:
            internal_logger.debug(f"Agent {name} is disabled, not registering")
            return

        self.configs[name] = config
        self.agents[name] = PydanticAIClient(config)

        # Register the agent's trigger
        trigger = config.trigger
        if trigger not in self.trigger_map:
            self.trigger_map[trigger] = []
        self.trigger_map[trigger].append(name)

        internal_logger.info(f"Registered LLM agent {name} for trigger {trigger}")

    def register_tools_for_session(self, session_id: str, keyword_map: Dict[str, Callable]) -> None:
        """Register tools (keywords) available for a specific session.

        Args:
            session_id: ID of the session
            keyword_map: Map of keyword names to their implementations
        """
        self.session_tools[session_id] = keyword_map
        internal_logger.debug(f"Registered {len(keyword_map)} tools for session {session_id}")

        # Register tools with each agent
        for agent_name, agent in self.agents.items():
            # Filter out private methods
            public_tools = {name: func for name, func in keyword_map.items()
                           if not name.startswith('_')}

            # Register the tools with the agent
            if hasattr(agent, 'register_tools'):
                agent.register_tools(public_tools)
                internal_logger.info(f"Registered {len(public_tools)} tools with agent {agent_name}")

    def unregister_session(self, session_id: str) -> None:
        """Unregister tools for a session when it's terminated.

        Args:
            session_id: ID of the session to unregister
        """
        if session_id in self.session_tools:
            self.session_tools.pop(session_id)
            internal_logger.debug(f"Unregistered tools for session {session_id}")

    async def on_event(self, event: Event) -> None:
        """Handle events and trigger appropriate LLM agents.

        Args:
            event: The event to handle
        """
        # Construct trigger string from entity_type and status
        trigger = f"{event.entity_type}_{event.status.value}"

        internal_logger.debug(f"Checking for agents with trigger: {trigger}")

        # Get agents registered for this trigger
        agent_names = self.trigger_map.get(trigger, [])
        if not agent_names:
            return

        session_id = event.extra.get("session_id")
        if not session_id:
            internal_logger.warning(f"Event {event.entity_id} has no session_id, cannot trigger LLM agents")
            return

        # Get available tools for this session
        tools = self._get_available_tools(session_id)
        if not tools:
            internal_logger.warning(f"No tools available for session {session_id}, cannot trigger LLM agents")
            return

        # Find the latest screenshot if available
        screenshot_path = self._find_latest_screenshot()

        # Create context for the agent
        context = AgentContext(
            event_type=trigger,
            entity_type=event.entity_type,
            entity_id=event.entity_id,
            entity_name=event.name,
            status=event.status.value,
            message=event.message,
            session_id=session_id,
            screenshot_path=screenshot_path,
            available_tools=tools,
            execution_history=[]  # Could be populated with recent events
        )

        # Execute agents for this trigger in parallel
        tasks = [self._execute_agent(name, context) for name in agent_names]
        await asyncio.gather(*tasks)

    async def _execute_agent(self, agent_name: str, context: AgentContext) -> None:
        """Execute an LLM agent and handle its response.

        Args:
            agent_name: Name of the agent to execute
            context: Context for the agent execution
        """
        agent = self.agents.get(agent_name)
        if not agent:
            internal_logger.error(f"Agent {agent_name} not found")
            return

        try:
            internal_logger.info(f"Executing LLM agent {agent_name} for {context.event_type}")
            response = await agent.execute(context)

            internal_logger.info(f"Agent {agent_name} response: {response.message}")

            if response.requires_human_input:
                internal_logger.warning(f"Agent {agent_name} requires human input: {response.message}")
                # Could send a notification or log for human review
                return

            # Process agent actions
            await self._process_actions(context.session_id, response.actions)

        except Exception as e:
            internal_logger.error(f"Error executing agent {agent_name}: {e}", exc_info=True)

    async def _process_actions(self, session_id: str, actions: List[AgentAction]) -> None:
        """Process actions requested by an LLM agent.

        Args:
            session_id: ID of the session
            actions: List of actions to process
        """
        if not actions:
            internal_logger.debug("No actions to process")
            return

        if session_id not in self.session_tools:
            internal_logger.warning(f"No tools available for session {session_id}, cannot process actions")
            return

        tools = self.session_tools[session_id]

        for action in actions:
            tool_name = action.tool_name
            parameters = action.parameters

            internal_logger.info(f"Processing action: {tool_name} with parameters {parameters}")

            if tool_name == "pause_execution":
                # Special case for pausing execution
                await self.event_manager.publish_command(
                    CommandType.PAUSE,
                    session_id,
                    params=parameters.get("reason", "Paused by LLM agent")
                )
                continue

            if tool_name == "resume_execution":
                # Special case for resuming execution
                await self.event_manager.publish_command(
                    CommandType.RESUME,
                    session_id
                )
                continue

            # Execute standard tool/keyword
            if tool_name in tools:
                try:
                    # Convert parameters dict to args and kwargs
                    args = parameters.get("args", [])
                    kwargs = parameters.get("kwargs", {})

                    # Execute the tool
                    internal_logger.debug(f"Executing {tool_name} with args={args}, kwargs={kwargs}")
                    if asyncio.iscoroutinefunction(tools[tool_name]):
                        await tools[tool_name](*args, **kwargs)
                    else:
                        tools[tool_name](*args, **kwargs)

                    internal_logger.info(f"Successfully executed tool {tool_name}")
                except Exception as e:
                    internal_logger.error(f"Error executing tool {tool_name}: {e}", exc_info=True)
            else:
                internal_logger.warning(f"Tool {tool_name} not found")

    def _get_available_tools(self, session_id: str) -> List[Tool]:
        """Get the list of available tools for a session.

        Args:
            session_id: ID of the session

        Returns:
            List of available tools
        """
        if session_id not in self.session_tools:
            return []

        tools = []
        for name, func in self.session_tools[session_id].items():
            # Skip private methods
            if name.startswith("_"):
                continue

            # Extract parameter information from the function
            params = {}
            # Add special tool for pausing execution
            tools.append(Tool(
                name=name,
                description=func.__doc__ or f"Execute the {name} function",
                parameters=params
            ))

        # Add special tool for pausing execution
        tools.append(Tool(
            name="pause_execution",
            description="Pause the test execution",
            parameters={"reason": "str"}
        ))

        # Add special tool for resuming execution
        tools.append(Tool(
            name="resume_execution",
            description="Resume the test execution",
            parameters={}
        ))

        return tools

    def _find_latest_screenshot(self) -> Optional[str]:
        """Find the latest screenshot file in the execution output directory.

        Returns:
            Path to the latest screenshot or None if not found
        """
        # This is a simplified implementation
        # In a real system, you'd want to track screenshots more systematically
        try:
            # Look in the execution_output directory for the latest screenshot
            output_dir = "execution_output"
            if not os.path.exists(output_dir):
                return None

            # Find the most recent screenshot file
            files = [os.path.join(output_dir, f) for f in os.listdir(output_dir)]
            screenshots = [f for f in files if f.endswith(('.png', '.jpg', '.jpeg'))]

            if not screenshots:
                return None

            # Return the most recently modified file
            return max(screenshots, key=os.path.getmtime)
        except Exception as e:
            internal_logger.error(f"Error finding latest screenshot: {e}")
            return None


def get_llm_agent_manager() -> LLMAgentManager:
    """Get the singleton instance of the LLM agent manager."""
    return LLMAgentManager()
