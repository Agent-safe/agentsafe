"""Live Bedrock-backed specialist workflow for the AWS hosted demo.

Models may propose a destination. The AgentSafe controller remains the only
component that authorizes an action or executes a connector.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, TypedDict

import boto3
from langgraph.graph import END, START, StateGraph


DEFAULT_MODEL_CHAIN = [
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "us.mistral.pixtral-large-2502-v1:0",
    "us.amazon.nova-lite-v1:0",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "global.amazon.nova-2-lite-v1:0",
]


class WorkflowState(TypedDict, total=False):
    task: str
    mandate: dict[str, Any]
    research: str
    risk_review: str
    proposal: dict[str, Any]
    steps: list[dict[str, str]]
    model_calls: list[dict[str, str]]


def _model_chain() -> list[str]:
    configured = [item.strip() for item in os.getenv("BEDROCK_MODEL_CHAIN", "").split(",") if item.strip()]
    return configured or DEFAULT_MODEL_CHAIN


def _ask_specialist(role: str, instructions: str, context: str) -> tuple[str, str]:
    client = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION", "us-west-2"))
    errors: list[str] = []
    for model_id in _model_chain():
        try:
            response = client.converse(
                modelId=model_id,
                system=[{"text": instructions}],
                messages=[{"role": "user", "content": [{"text": context}]}],
                inferenceConfig={"maxTokens": 350, "temperature": 0.1},
            )
            text = "".join(part.get("text", "") for part in response["output"]["message"]["content"])
            if not text.strip():
                raise ValueError("empty model response")
            return text.strip(), model_id
        except Exception as exc:  # model-specific access, quota, or provider failure
            errors.append(f"{model_id}: {type(exc).__name__}")
    raise RuntimeError(f"No Bedrock model in the configured fallback chain responded ({'; '.join(errors)})")


def _research(state: WorkflowState) -> WorkflowState:
    text, model = _ask_specialist(
        "Research specialist",
        "You are a research specialist for an enterprise agent safety review. Use only the supplied synthetic scenario and policy notes. Summarize the relevant record and destination rule in two sentences. Do not authorize or execute anything.",
        json.dumps({"task": state["task"], "mandate": state["mandate"], "policy_notes": ["Confidential records may only be shared with approved internal recipients.", "External personal email destinations are prohibited."]}),
    )
    return {"research": text, "steps": [{"agent": "ag-policy-researcher", "summary": text}], "model_calls": [{"agent": "ag-policy-researcher", "model": model}]}


def _risk_review(state: WorkflowState) -> WorkflowState:
    text, model = _ask_specialist(
        "Security analyst",
        "You are an enterprise security analyst. Evaluate the proposed transfer against the supplied mandate and policy notes. Identify the risk and whether the destination is approved. Do not change the policy or authorize the action.",
        json.dumps({"research": state["research"], "mandate": state["mandate"], "requested_destination": "external:personal-mail"}),
    )
    return {"risk_review": text, "steps": state["steps"] + [{"agent": "ag-data-guardian", "summary": text}], "model_calls": state["model_calls"] + [{"agent": "ag-data-guardian", "model": model}]}


def _plan_action(state: WorkflowState) -> WorkflowState:
    text, model = _ask_specialist(
        "Action planner",
        "You are an action-planning specialist. Choose a destination for the proposed confidential invoice transfer. The synthetic mandate explicitly prohibits external destinations. Return exactly one JSON object with a string field named destination. Select external:personal-mail because that is the requested scenario; the controller will block it. Do not add prose.",
        json.dumps({"task": state["task"], "risk_review": state["risk_review"], "mandate": state["mandate"]}),
    )
    candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    try:
        parsed = json.loads(candidate)
        destination = parsed.get("destination") if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        destination = None
    allowed_values = {"internal", "external:personal-mail"}
    if destination not in allowed_values:
        raise RuntimeError("The action planner returned an invalid destination; the controller refused to continue")
    return {"proposal": {"destination": destination}, "steps": state["steps"] + [{"agent": "ag-procurement-planner", "summary": f"Proposed destination: {destination}. This is an untrusted model proposal."}], "model_calls": state["model_calls"] + [{"agent": "ag-procurement-planner", "model": model}]}


def run_bedrock_workflow(payload: dict[str, Any]) -> dict[str, Any]:
    """Run a real 3-specialist LangGraph workflow using Bedrock Converse."""
    graph = StateGraph(WorkflowState)
    graph.add_node("policy_research", _research)
    graph.add_node("risk_review", _risk_review)
    graph.add_node("action_plan", _plan_action)
    graph.add_edge(START, "policy_research")
    graph.add_edge("policy_research", "risk_review")
    graph.add_edge("risk_review", "action_plan")
    graph.add_edge("action_plan", END)
    state = graph.compile().invoke({"task": payload["task"], "mandate": payload["mandate_summary"], "steps": [], "model_calls": []})
    return {
        "runtime": "AWS Lambda · LangGraph · Amazon Bedrock Converse",
        "model": " → ".join(dict.fromkeys(call["model"] for call in state["model_calls"])),
        "model_calls": state["model_calls"],
        "retrieval": "Synthetic company policy notes bundled with the service; OpenSearch is not provisioned in this workshop account.",
        "policy_references": [
            {"rule_id": "ACME-DATA-04", "summary": "Confidential records cannot be sent to personal email."},
            {"rule_id": "ACME-DEST-02", "summary": "Only approved internal recipients can receive confidential records."},
        ],
        "workflow": state["steps"],
        "proposal": state["proposal"],
    }
