"""Test-only direct native policy driver; production uses the full native bundle."""
from types import SimpleNamespace
from agent_control_spec import AcsInterceptor
from agent_hooks import AgentContextBuilder, InterceptionEmitter


class PolicyDriver:
    def __init__(self, path):
        self.emitter = InterceptionEmitter().register(AcsInterceptor(str(path)), "acs")
        self.builder = AgentContextBuilder(agent_id="policy-test", framework="test", session_id="test")

    async def run_tool(self, name, args, execute, *, snapshot):
        self.builder.with_l2(extensions={"safe.example/host": snapshot["safe"]})
        outcome = await self.emitter.emit(self.builder.pre_tool_call(call_id="fixture", name=name, args=args))
        value = await execute(outcome.target)
        outcome = await self.emitter.emit(self.builder.post_tool_call(
            call_id="fixture", name=name, args=outcome.target, value=value,
        ))
        return SimpleNamespace(value=outcome.target)
