"""Microsoft Foundry hosted-agent entry point."""

from __future__ import annotations
import os

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential

from host_boundary import SafeAgent
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
    return SafeAgent(
        client=client,
        name="HelpdeskBot",
        instructions=get_instructions(mode),
        tools=TOOLS,
        default_options={"store": False},
    )


def main() -> None:
    # The hosting runtime owns OpenTelemetry setup: it builds the provider,
    # wires the exporters, and enables Agent Framework instrumentation before
    # serving traffic. The agent only needs to emit its own spans.
    # Host evidence facts must not enter framework model/tool content telemetry.
    os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "false"
    ResponsesHostServer(build_agent()).run()


if __name__ == "__main__":
    main()
