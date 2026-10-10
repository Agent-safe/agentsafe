"""Create and seed the demo's non-personal policy guidance vector index."""
from __future__ import annotations

import json
import os
from urllib.parse import urlparse

import boto3
from opensearchpy import OpenSearch, RequestsHttpConnection
from opensearchpy.helpers import AWSV4SignerAuth

REGION = os.getenv("AWS_REGION", "us-west-2")
INDEX = os.getenv("AGENTSAFE_OPENSEARCH_INDEX", "agentsafe-policy-guidance")
EMBEDDING_MODEL_ID = os.getenv("BEDROCK_EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0")
DOCS = [
    {"title": "Confidential data outbound policy", "rule_id": "DATA-OUT-01", "guidance": "Confidential records may only go to explicitly approved internal destinations. External and personal destinations must be blocked before tool execution. Derived summaries retain the source classification unless an authorized reviewer lowers it."},
    {"title": "Least privilege and tool scope", "rule_id": "AUTH-SCOPE-01", "guidance": "A delegated agent may call only tools and resources in its signed mandate. A model suggestion is not authorization. Unknown tools, resources, and destinations are denied by the controller."},
    {"title": "Budget and replay controls", "rule_id": "BUDGET-REPLAY-01", "guidance": "Enforce per-action and cumulative mission budgets atomically. Reject expired or revoked grants and previously consumed request identifiers. Model confidence never overrides a limit."},
    {"title": "Human approval binding", "rule_id": "APPROVAL-01", "guidance": "Sensitive operations require an unexpired, unused approval bound to the exact mission and exact canonical action parameters. Approval for a different action cannot be reused."},
    {"title": "Audit evidence semantics", "rule_id": "AUDIT-01", "guidance": "Record requested, authorized, executed, failed, and uncertain states separately. A signed controller receipt proves what the controller observed, not the truth of a tool response or the quality of model reasoning."},
]


def main() -> None:
    endpoint = os.environ["AGENTSAFE_OPENSEARCH_ENDPOINT"]
    parsed = urlparse(endpoint if "://" in endpoint else f"https://{endpoint}")
    if parsed.scheme != "https" or not parsed.hostname:
        raise SystemExit("AGENTSAFE_OPENSEARCH_ENDPOINT must be an HTTPS endpoint")
    session = boto3.Session(region_name=REGION)
    credentials = session.get_credentials()
    if credentials is None:
        raise SystemExit("No AWS credentials available")
    auth = AWSV4SignerAuth(credentials, REGION, "aoss")
    client = OpenSearch(hosts=[{"host": parsed.hostname, "port": 443}], http_auth=auth, use_ssl=True, verify_certs=True, connection_class=RequestsHttpConnection, timeout=10)
    bedrock = session.client("bedrock-runtime", region_name=REGION)
    if not client.indices.exists(INDEX):
        client.indices.create(INDEX, body={"settings": {"index": {"knn": True}}, "mappings": {"properties": {"title": {"type": "text"}, "rule_id": {"type": "keyword"}, "guidance": {"type": "text"}, "embedding": {"type": "knn_vector", "dimension": 1024, "method": {"name": "hnsw", "engine": "faiss", "space_type": "l2"}}}}})
    for index, doc in enumerate(DOCS):
        response = bedrock.invoke_model(modelId=EMBEDDING_MODEL_ID, contentType="application/json", accept="application/json", body=json.dumps({"inputText": doc["title"] + ". " + doc["guidance"], "dimensions": 1024, "normalize": True}))
        vector = json.loads(response["body"].read()).get("embedding")
        if not isinstance(vector, list) or len(vector) != 1024:
            raise RuntimeError("Titan embedding response was invalid")
        client.index(index=INDEX, id=doc["rule_id"], body={**doc, "embedding": vector}, refresh=True)
    print(json.dumps({"index": INDEX, "documents_seeded": len(DOCS), "source": "synthetic AgentSafe policy guidance"}))


if __name__ == "__main__":
    main()
