import { NextResponse } from "next/server";
import { streamSimulation, type Scenario } from "./agent";

export async function POST(request: Request) {
  let scenario: Scenario;
  try {
    const body = await request.json() as { scenario?: unknown };
    if (body.scenario !== "attack" && body.scenario !== "safe") {
      return NextResponse.json({ error: "Choose the attack or safe synthetic scenario." }, { status: 400 });
    }
    scenario = body.scenario;
  } catch {
    return NextResponse.json({ error: "Invalid simulation request." }, { status: 400 });
  }

  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    async start(controller) {
      try {
        for await (const update of streamSimulation(scenario)) {
          controller.enqueue(encoder.encode(`${JSON.stringify(update)}\n`));
        }
      } catch {
        controller.enqueue(encoder.encode(`${JSON.stringify({ type: "error", message: "The LangGraph simulation could not complete." })}\n`));
      } finally {
        controller.close();
      }
    },
  });
  return new Response(body, {
    headers: { "Cache-Control": "no-store, no-transform", "Content-Type": "application/x-ndjson; charset=utf-8", "X-Accel-Buffering": "no" },
  });
}
