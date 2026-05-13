import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

import { streamImageJob } from "./images";

/**
 * Helper: build a ReadableStream that emits the given chunks (Uint8Arrays
 * encoded from strings). Lets us exercise the SSE frame parser with arbitrary
 * chunk boundaries — including splitting an event mid-line.
 */
function streamFromChunks(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
}

const originalFetch = globalThis.fetch;

beforeEach(() => {
  // Each test sets its own fetch mock.
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

describe("streamImageJob SSE parser", () => {
  it("parses a single complete event", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      body: streamFromChunks([
        'event: image_started\ndata: {"job_id":"abc"}\n\n',
      ]),
    } as unknown as Response);

    const events: Array<[string, Record<string, unknown>]> = [];
    await streamImageJob("abc", (t, d) => events.push([t, d]));
    expect(events).toEqual([["image_started", { job_id: "abc" }]]);
  });

  it("parses multiple events in one chunk", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      body: streamFromChunks([
        'event: image_queued\ndata: {}\n\n' +
          'event: image_started\ndata: {}\n\n' +
          'event: image_completed\ndata: {"outputs":[{"storage_url":"/x"}]}\n\n',
      ]),
    } as unknown as Response);

    const events: Array<[string, Record<string, unknown>]> = [];
    await streamImageJob("abc", (t, d) => events.push([t, d]));
    expect(events.map((e) => e[0])).toEqual([
      "image_queued",
      "image_started",
      "image_completed",
    ]);
    expect((events[2][1] as { outputs: unknown[] }).outputs).toHaveLength(1);
  });

  it("handles event split across chunk boundaries", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      body: streamFromChunks([
        "event: image_pro",
        'gress\ndata: {"stage":"',
        'backdrop"}\n\n',
      ]),
    } as unknown as Response);

    const events: Array<[string, Record<string, unknown>]> = [];
    await streamImageJob("abc", (t, d) => events.push([t, d]));
    expect(events).toEqual([["image_progress", { stage: "backdrop" }]]);
  });

  it("ignores frames without both event and data lines", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      body: streamFromChunks([
        ": keepalive\n\nevent: image_started\ndata: {}\n\n",
      ]),
    } as unknown as Response);
    const events: Array<[string, Record<string, unknown>]> = [];
    await streamImageJob("abc", (t, d) => events.push([t, d]));
    expect(events).toEqual([["image_started", {}]]);
  });

  it("does nothing when response body is missing", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({ body: null } as Response);
    const events: Array<[string, Record<string, unknown>]> = [];
    await streamImageJob("abc", (t, d) => events.push([t, d]));
    expect(events).toEqual([]);
  });
});
