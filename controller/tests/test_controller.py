import os
import base64
import sys
import tempfile
import uuid
from pathlib import Path

TEMP_DATA = tempfile.TemporaryDirectory(prefix="agentsafe-tests-")
os.environ["AGENTSAFE_DATA_DIR"] = TEMP_DATA.name
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app import DelegateRequest, app, canonical, delegation_signing_payload, fresh_agent_key, signed_action  # noqa: E402
from verify_export import verify  # noqa: E402


def test_policy_workflow_approval_budget_revocation_and_export():
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/health").json()["external_connectors"] is False
        prepared = client.post("/demo/run").json()
        assert prepared["agent_runtime"].startswith("LangGraph StateGraph")
        assert prepared["model"].startswith("Deterministic synthetic scenario")
        assert prepared["results"] == {
            "internal_confidential_transfer": "allow",
            "external_confidential_transfer": "block",
            "missing_approval": "block",
        }

        order = prepared["pending_order"]
        digest = client.post("/actions/hash", json=order).json()["parameter_hash"]
        approval = client.post("/approvals", params={"mission_id": order["mission_id"], "request_hash": digest}).json()
        finished = client.post("/demo/finish", json={"pending_order": order, "approval_id": approval["approval_id"]})
        assert finished.status_code == 200, finished.text
        assert finished.json()["results"] == {"approved_order": "allow", "budget_overrun": "block", "revoked_mission": "block"}

        journal = client.get("/journal/export").json()
        assert not verify(journal)
        assert client.get("/journal/verify").json()["valid"] is True


def test_replay_and_scope_escalation_are_rejected():
    with TestClient(app, base_url="http://127.0.0.1") as client:
        public, private = fresh_agent_key()
        mission = client.post("/missions", json={"name": "test", "root_agent": "root", "root_public_key": public, "actions": ["records.share_local"], "resources": ["file-a"], "budget_limit": 25}).json()
        child_public, _ = fresh_agent_key()
        denied = client.post("/delegations", json={"parent_grant_id": mission["grant_id"], "child_agent": "child", "child_public_key": child_public, "parent_signature": "invalid", "actions": ["procurement.create_order"], "resources": ["file-a"], "budget_limit": 5, "ttl_seconds": 60})
        assert denied.status_code == 403
        body = signed_action(private, request_id=str(uuid.uuid4()), mission_id=mission["mission_id"], grant_id=mission["grant_id"], agent_id="root", tool="records.share_local", resource="file-a", payload={"local_ref": "private-data"}).model_dump()
        tampered = dict(body)
        tampered["request_id"] = str(uuid.uuid4())
        tampered["payload"] = {"local_ref": "different-data"}
        assert client.post("/actions", json=tampered).json()["reason"] == "agent request signature is invalid"
        assert client.post("/actions", json=body).json()["decision"] == "allow"
        replay = client.post("/actions", json=body)
        assert replay.status_code == 409


def test_browser_origin_guard_blocks_remote_sites():
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/demo/run", headers={"Origin": "https://attacker.example"})
        assert response.status_code == 403


def test_export_verifier_detects_tampering():
    with TestClient(app, base_url="http://127.0.0.1") as client:
        public, _ = fresh_agent_key()
        client.post("/missions", json={"name": "test", "root_agent": "root", "root_public_key": public, "actions": ["records.share_local"], "resources": ["file-a"], "budget_limit": 25})
        journal = client.get("/journal/export").json()
        journal["events"][0]["body"] = journal["events"][0]["body"].replace("mission.created", "mission.tampered")
        assert verify(journal)


def test_real_groth16_delegation_proof_is_stored_and_independently_verified():
    with TestClient(app, base_url="http://127.0.0.1") as client:
        prepared = client.post("/demo/run").json()
        assert prepared["delegation_zk_status"] == "verified"
        proof = prepared["delegation_zk_proof"]
        assert proof["protocol"] == "Groth16/bn128"
        assert proof["verified"] is True
        assert len(proof["publicSignals"]) == 5
        result = client.get(f"/grants/{prepared['delegation']}/zk-proof/verify")
        assert result.status_code == 200, result.text
        assert result.json()["valid"] is True


def test_parameter_constraints_are_inherited_and_three_denials_trigger_kill_switch():
    with TestClient(app, base_url="http://127.0.0.1") as client:
        public, private = fresh_agent_key()
        mission = client.post("/missions", json={"name": "supplier policy", "root_agent": "root", "root_public_key": public, "actions": ["records.share_local"], "resources": ["catalogue"], "parameter_constraints": {"supplier_id": 42}, "budget_limit": 25}).json()
        child_public, _ = fresh_agent_key()
        request = DelegateRequest(parent_grant_id=mission["grant_id"], child_agent="child", child_public_key=child_public, parent_signature="pending", actions=["records.share_local"], resources=["catalogue"], parameter_constraints={}, budget_limit=5, ttl_seconds=60)
        claim = delegation_signing_payload(request, mission["mission_id"])
        parent_signature = base64.urlsafe_b64encode(private.sign(canonical(claim))).decode().rstrip("=")
        denied = client.post("/delegations", json=request.model_copy(update={"parent_signature": parent_signature}).model_dump())
        assert denied.status_code == 403

        for _ in range(3):
            request = signed_action(private, request_id=str(uuid.uuid4()), mission_id=mission["mission_id"], grant_id=mission["grant_id"], agent_id="root", tool="records.share_local", resource="catalogue", payload={"supplier_id": 42})
            tampered = request.model_copy(update={"agent_signature": "invalid"})
            response = client.post("/actions", json=tampered.model_dump())
            assert response.status_code == 200
            assert response.json()["decision"] == "block"

        grants = client.get("/missions").json()["grants"]
        root = next(item for item in grants if item["id"] == mission["grant_id"])
        assert root["status"] == "revoked"
        alerts = client.get("/alerts").json()["alerts"]
        assert any(item["code"] == "AUTO-KILL-SWITCH" for item in alerts)
        jsonl = client.get("/journal/jsonl")
        assert jsonl.status_code == 200
        assert "grant.auto_revoked" in jsonl.text


def test_soc_group_inventory_and_quantitative_policy_are_live():
    with TestClient(app, base_url="http://127.0.0.1") as client:
        group = client.post("/groups", json={
            "name": "Procurement fleet", "department": "Purchasing",
            "actions": ["records.share_local"], "resources": ["supplier-x"],
            "parameter_constraints": {"supplier_id": 42, "max_amount": 80},
            "budget_limit": 100, "ttl_seconds": 1800, "members": ["Agent C", "Agent D"],
        })
        assert group.status_code == 200, group.text
        listed = client.get("/groups").json()["groups"]
        assert listed[0]["name"] == "Procurement fleet"
        assert listed[0]["members"] == ["Agent C", "Agent D"]
        assert listed[0]["budget_limit"] == 100

        public, private = fresh_agent_key()
        mission = client.post("/missions", json={
            "name": "quantitative policy", "root_agent": "buyer", "root_public_key": public,
            "actions": ["records.share_local"], "resources": ["supplier-x"],
            "parameter_constraints": {"supplier_id": 42, "max_amount": 80}, "budget_limit": 200,
        }).json()
        request = signed_action(private, request_id=str(uuid.uuid4()), mission_id=mission["mission_id"],
            grant_id=mission["grant_id"], agent_id="buyer", tool="records.share_local",
            resource="supplier-x", amount=85, payload={"supplier_id": 42})
        denied = client.post("/actions", json=request.model_dump()).json()
        assert denied["decision"] == "block"
        assert "quantitative limit" in denied["reason"]
        assert client.get("/alerts").json()["alerts"][0]["code"] == "ASI03-PRIVILEGE-ABUSE"
