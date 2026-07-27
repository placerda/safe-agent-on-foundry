"""Microsoft Foundry hosted-agent entry point."""

from __future__ import annotations

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential

from acs_middleware import AcsFunctionMiddleware
from config import get_agent_config, get_instructions, get_mode
from tools import TOOLS


def build_agent() -> Agent:
    config = get_agent_config()
    mode = get_mode()
    client = FoundryChatClient(
        project_endpoint=config.project_endpoint,
        model=config.model_deployment_name,
        credential=DefaultAzureCredential(),
    )
    return Agent(
        client=client,
        name="HelpdeskBot",
        instructions=get_instructions(mode),
        tools=TOOLS,
        middleware=[AcsFunctionMiddleware()],
        default_options={"store": False},
    )


def main() -> None:
    ResponsesHostServer(build_agent()).run()


if __name__ == "__main__":
    main()

