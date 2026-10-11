# AWS workflow notes

## Current deployment path

The current AWS demo is an AWS Lambda container running the AgentSafe FastAPI controller and a three-step LangGraph workflow. It calls Amazon Bedrock directly through the Lambda execution role. It does not call Vercel, AgentCore Runtime, or OpenSearch.

The workshop participant role denies AgentCore runtime control operations. No OpenSearch collection was provisioned. The LangGraph workflow receives a few synthetic Acme Commerce rules bundled with the service. These limitations are shown in the workflow details rather than hidden behind an “online” badge.

The deployment template is [`lambda-stack.yaml`](lambda-stack.yaml). The container recipe and request handler are in `../controller/Dockerfile` and `../controller/lambda_handler.py`.

## Specialist graph and model chain

1. `ag-policy-researcher` summarizes applicable synthetic rules.
2. `ag-data-guardian` reviews the requested destination.
3. `ag-procurement-planner` proposes a destination as untrusted model output.

Each step tries these Amazon Bedrock Converse model IDs in order:

1. `us.anthropic.claude-sonnet-4-5-20250929-v1:0`
2. `us.mistral.pixtral-large-2502-v1:0`
3. `us.amazon.nova-lite-v1:0`
4. `us.anthropic.claude-haiku-4-5-20251001-v1:0`
5. `global.amazon.nova-2-lite-v1:0`

The next model is tried after a provider error, empty response, or unusable planner output. If every configured option fails, the workflow stops. The AgentSafe controller still independently verifies the proposed action and saves its decision before an allowed connector call.

## AWS deployment resources

The CloudFormation stack creates one Lambda execution role, the Lambda function, and its HTTPS Function URL. The role is limited to writing that function's CloudWatch logs, reading the specified database/signing-key secret, and invoking the configured Bedrock models. The public demo uses synthetic records and allows at most ten live workflow runs per UTC day.

Create an ECR repository for the controller image, then build and push the image from the AWS Workshop CloudShell in `us-west-2`. Create the named Secrets Manager secret with `DATABASE_URL` and `AGENTSAFE_SIGNING_KEY_B64`, then deploy `lambda-stack.yaml` with the pushed image URI, secret ARN, and generated Function URL hostname. See the deployment notes in the repository README before changing this temporary workshop account.

The Container Function URL is public so reviewers can open the dashboard without AWS credentials. Do not place real customer records in this demo. The event is temporary; remove the Lambda stack, image repository, and database secret when the hosted demo is no longer needed.

## Older AgentCore experiment

`agentcore/` contains the earlier AgentCore/Strands exploration. Its CDK scaffold is not the active runtime and must not be described as deployed. The workshop role did not permit AgentCore runtime operations, so the current implementation uses LangGraph on Lambda as the fallback orchestration path.
