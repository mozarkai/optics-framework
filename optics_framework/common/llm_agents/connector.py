"""
Connector module for integrating LLM agents with the Optics Framework.
"""
import os
import yaml
from typing import Optional
from optics_framework.common.config_handler import ConfigHandler
from optics_framework.common.llm_agents.manager import get_llm_agent_manager
from optics_framework.common.logging_config import internal_logger


def initialize_llm_agents(project_path: Optional[str] = None) -> None:
    """Initialize LLM agents from configuration.

    Args:
        project_path: Optional path to the project directory
    """
    config_handler = ConfigHandler.get_instance()
    llm_agent_manager = get_llm_agent_manager()

    # Get LLM agent configurations from config
    llm_agents = config_handler.config.llm_agents

    # If no agents configured, return early
    if not llm_agents:
        internal_logger.debug("No LLM agents configured")
        return

    # First scan for agent prompts in YAML files
    scan_yaml_files_for_agents(project_path)

    # For each configured agent, register it with the manager
    for agent_name, agent_config in llm_agents.items():
        # Setup prompt source file - prefer existing prompt_file if specified
        if agent_config.prompt_file and project_path and not os.path.isabs(agent_config.prompt_file):
            agent_config.prompt_file = os.path.join(project_path, agent_config.prompt_file)
        elif agent_config.prompt_source_file:
            # Use discovered prompt file
            agent_config.prompt_file = agent_config.prompt_source_file

        internal_logger.info(f"Registering LLM agent: {agent_name}")
        llm_agent_manager.register_agent(agent_name, agent_config)

    internal_logger.info(f"Initialized {len(llm_agents)} LLM agents")


def scan_yaml_files_for_agents(project_path: str) -> None:
    """Scan all YAML files in the project directory for LLM agent configurations.

    Args:
        project_path: Path to the project directory
    """
    if not project_path or not os.path.exists(project_path):
        internal_logger.debug(f"Invalid project path: {project_path}")
        return

    # Get config handler and agent manager
    config_handler = ConfigHandler.get_instance()
    llm_agent_manager = get_llm_agent_manager() #ignore F841

    # Get existing agent configurations
    llm_agents = config_handler.config.llm_agents or {}

    # Find all YAML files in the project
    yaml_files = []
    for root, _, files in os.walk(project_path):
        for file in files:
            if file.endswith(('.yml', '.yaml')):
                yaml_files.append(os.path.join(root, file))

    internal_logger.debug(f"Found {len(yaml_files)} YAML files in project")

    # Process each YAML file for agent configurations
    for yaml_file in yaml_files:
        try:
            with open(yaml_file, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}

            # Look for 'agents' section in YAML files
            if 'agents' in data and isinstance(data['agents'], dict):
                internal_logger.info(f"Found {len(data['agents'])} agents defined in {yaml_file}")

                # For each agent in the config, check if it exists in this file
                for agent_name, agent_config in llm_agents.items():
                    if agent_name in data['agents']:
                        # Store the file path in the agent configuration
                        agent_config.prompt_source_file = yaml_file
                        internal_logger.debug(f"Found prompt definitions for {agent_name} in {yaml_file}")

            # Also look for the legacy llm_agent_prompt section
            if 'llm_agent_prompt' in data and isinstance(data['llm_agent_prompt'], dict):
                internal_logger.info(f"Found legacy agent prompts in {yaml_file}")

                # For each agent in the config, check if it exists in this file
                for agent_name, agent_config in llm_agents.items():
                    if agent_name in data['llm_agent_prompt']:
                        # Store the file path in the agent configuration
                        agent_config.prompt_source_file = yaml_file
                        internal_logger.debug(f"Found legacy prompt definitions for {agent_name} in {yaml_file}")

        except Exception as e:
            internal_logger.error(f"Error processing {yaml_file}: {e}")

# Keep backward compatibility
def check_for_data_yml(project_path: str) -> None:
    """Legacy function for checking data.yml.

    Args:
        project_path: Path to the project directory
    """
    # Call the more general function that scans all YAML files
    scan_yaml_files_for_agents(project_path)
