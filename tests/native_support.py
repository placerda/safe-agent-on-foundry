import json

from agent_framework import (
    BaseChatClient, ChatMiddlewareLayer, ChatResponse, ChatResponseUpdate, Content,
    FunctionInvocationLayer, Message, ResponseStream,
)


class ScriptClient(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    STORES_BY_DEFAULT = False

    def __init__(self, script):
        super().__init__()
        self.script = script
        self.requests = []

    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        self.requests.append(list(messages))
        message = self.script(messages, len(self.requests))
        if not stream:
            async def respond():
                return ChatResponse(messages=message)
            return respond()

        async def updates():
            yield ChatResponseUpdate(contents=message.contents, role=message.role)
        return ResponseStream(updates(), finalizer=ChatResponse.from_updates)


def call(name, args, call_id="call"):
    return Content.from_function_call(call_id, name, arguments=args)


def results(messages):
    values = []
    for message in messages:
        for content in message.contents:
            if content.type == "function_result":
                value = content.result
                if isinstance(value, str):
                    try:
                        value = json.loads(value)
                    except ValueError:
                        pass
                values.append(value)
    return values


def chain_script(case="locked-signin", model_ticket=False):
    def script(messages, count):
        values = results(messages)
        if not values:
            item = call("get_system_status", {"case_id": case, "service": "identity"}, "status")
        elif len(values) == 1:
            item = call("get_user_account", {"case_id": case,
                        "service_evidence_reference": values[-1]["evidence_reference"]}, "account")
        elif len(values) == 2:
            item = call("search_kb", {"case_id": case, "query": "sign-in diagnosis",
                        "account_evidence_reference": values[-1]["evidence_reference"]}, "kb")
        elif len(values) == 3 and model_ticket:
            item = call("create_escalation_ticket", {"case_id": case, "category": "access",
                        "severity": "medium", "decision_evidence_reference": values[-1]["evidence_reference"]}, "ticket")
        else:
            return Message("assistant", ["MODEL_DID_NOT_HANDOFF" if not model_ticket else "Ticket completed."])
        return Message("assistant", [item])
    return script


def one_call(name, args):
    return ScriptClient(lambda messages, count: Message("assistant", [call(name, args)])
                        if count == 1 else Message("assistant", ["RECOVERED"]))


def fault_manifest(tmp_path, point, *, decision="deny", reason="fixture_rejected", query=False):
    from pathlib import Path
    import yaml
    from host_boundary import POLICY_MANIFEST

    manifest = yaml.safe_load(POLICY_MANIFEST.read_text())
    manifest["policies"]["helpdesk"]["bundle"] = str(POLICY_MANIFEST.parent)
    if query:
        (tmp_path / "fault.rego").write_text(
            'package fault\nimport rego.v1\nverdict := {"decision": "invalid"}\n')
        manifest["policies"]["fault"] = {"type": "rego", "bundle": str(tmp_path),
                                        "query": "data.fault.verdict"}
    else:
        manifest["policies"]["fault"] = {"type": "test", "verdict": {"decision": decision, "reason": reason}}
    manifest["intervention_points"][point]["policy"] = {"id": "fault"}
    target = tmp_path / "manifest.yaml"
    target.write_text(yaml.safe_dump(manifest))
    return Path(target)
