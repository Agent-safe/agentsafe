# AgentSafe AWS Multi-Agent Workflow

This AWS-backed workflow follows the ESSECKATHON stack and workshop pattern. Amazon Bedrock AgentCore Runtime hosts a Python orchestrator built with Strands Agents. Amazon Bedrock runs model inference, Titan creates embeddings, and Amazon OpenSearch Serverless retrieves synthetic policy notes.

## Specialist workflow

1. **Research Agent:** retrieve and summarize policy guidance relevant to the task.
2. **Security Analyst Agent:** review the task, retrieved guidance, and bounded mandate summary.
3. **Action Planner Agent:** return a constrained action proposal for the AgentSafe controller.

The runtime has no business-tool credentials and cannot authorize the proposal. The separate FastAPI controller validates signatures and mandates, records its decision, and remains the only authorization gate.

## Model selection and fallback

The four-model chain is configured in `agentcore/agentcore.json` and `BEDROCK_MODEL_CHAIN`:

1. `eu.anthropic.claude-sonnet-4-5-20250929-v1:0`
2. `eu.anthropic.claude-haiku-4-5-20251001-v1:0`
3. `global.anthropic.claude-sonnet-4-6`
4. `mistral.devstral-2-123b`

The Research Agent prefers model 1, the Security Analyst prefers model 3, and the Action Planner prefers model 2. Each role tries its preferred model first, then the remaining configured models in order. This keeps Anthropic first and Mistral as the last fallback. Empty responses, provider errors, and invalid planner JSON trigger the next model. The workflow fails closed after the chain is exhausted. AWS Converse smoke checks in the workshop session returned `OK` for all four IDs; this is a model-access check, not a live AgentCore deployment check.

The AWS model catalog also listed `mistral.pixtral-large-2502-v1:0`, but direct on-demand invocation returned an error requiring an inference profile. It is intentionally excluded from the working chain.

## Deployment inputs

- AWS account: regular **Future Unicorn** account; do not deploy into the temporary Workshop Studio account.
- Region: `eu-north-1`.
- Embeddings: `amazon.titan-embed-text-v2:0`, 1024 dimensions.
- Search: authenticated OpenSearch Serverless vector collection.
- Runtime entrypoint: `app/AgentSafeOrchestrator/main.py`.
- Policy data: short synthetic AgentSafe rules only. Do not index customer data, personal data, or workshop-provided materials.

The runtime and OpenSearch collection incur AWS usage. The dashboard must describe AWS as unavailable until the runtime, index, and Vercel OIDC bridge are configured and the end-to-end request succeeds.

## Commands

From this directory, follow the AgentCore CLI and CDK instructions in `agentcore/cdk/README.md` for synth/deploy/status. The runtime uses the `app/AgentSafeOrchestrator/requirements.txt` dependency manifest. Deploy with the active AWS workshop account and `eu-north-1` region after reviewing the intended resources and IAM scope.
