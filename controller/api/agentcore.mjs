import { randomUUID, timingSafeEqual } from "node:crypto";
import { AssumeRoleWithWebIdentityCommand, STSClient } from "@aws-sdk/client-sts";
import { BedrockAgentCoreClient, InvokeAgentRuntimeCommand } from "@aws-sdk/client-bedrock-agentcore";

export const config = { maxDuration: 120 };

function equalSecret(actual, expected) {
  if (typeof actual !== "string" || typeof expected !== "string") return false;
  const left = Buffer.from(actual);
  const right = Buffer.from(expected);
  return left.length === right.length && timingSafeEqual(left, right);
}

export default async function handler(request, response) {
  if (request.method !== "POST") {
    response.setHeader("Allow", "POST");
    response.status(405).json({ error: "method not allowed" });
    return;
  }
  if (!equalSecret(request.headers["x-agentsafe-bridge-key"], process.env.AGENTSAFE_AWS_BRIDGE_SECRET)) {
    response.status(401).json({ error: "unauthorized bridge call" });
    return;
  }

  const region = process.env.AGENTSAFE_AWS_REGION;
  const roleArn = process.env.AWS_ROLE_ARN;
  const runtimeArn = process.env.AGENTCORE_RUNTIME_ARN;
  const token = request.headers["x-vercel-oidc-token"];
  if (!region || !roleArn || !runtimeArn || typeof token !== "string") {
    response.status(503).json({ error: "AWS AgentCore OIDC runtime is not configured" });
    return;
  }

  try {
    const sts = new STSClient({ region });
    const assumed = await sts.send(new AssumeRoleWithWebIdentityCommand({
      RoleArn: roleArn,
      RoleSessionName: `agentsafe-${randomUUID().replaceAll("-", "").slice(0, 24)}`,
      WebIdentityToken: token,
      DurationSeconds: 900,
    }));
    const temporary = assumed.Credentials;
    if (!temporary?.AccessKeyId || !temporary.SecretAccessKey || !temporary.SessionToken) {
      throw new Error("AWS did not return short-lived credentials");
    }
    const agentcore = new BedrockAgentCoreClient({
      region,
      credentials: {
        accessKeyId: temporary.AccessKeyId,
        secretAccessKey: temporary.SecretAccessKey,
        sessionToken: temporary.SessionToken,
      },
    });
    const result = await agentcore.send(new InvokeAgentRuntimeCommand({
      agentRuntimeArn: runtimeArn,
      runtimeSessionId: `agentsafe-${randomUUID()}`,
      contentType: "application/json",
      accept: "application/json",
      payload: new TextEncoder().encode(JSON.stringify(request.body ?? {})),
    }));
    const body = result.response ? await result.response.transformToString() : "{}";
    response.status(200).json(JSON.parse(body));
  } catch (error) {
    // Keep AWS credentials, OIDC claims, and request contents out of HTTP logs.
    const name = error instanceof Error ? error.name : "AWSInvocationError";
    response.status(502).json({ error: "AgentCore invocation failed", code: name });
  }
}
