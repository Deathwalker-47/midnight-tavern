/**
 * Image generation API client.
 *
 * Use ``imagesApi.generate`` to enqueue a job, then ``streamImageJob`` to
 * receive SSE events (image_queued / image_started / image_progress /
 * image_completed / image_failed / image_canceled).
 */

import { apiFetch } from "./client";

export type ImageJobStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "canceled";

export interface ImageOutput {
  id: string;
  storage_url: string;
  mime_type: string;
  bytes_size: number | null;
  width: number | null;
  height: number | null;
  created_at: string;
}

export interface ImageJob {
  id: string;
  user_id: string;
  chat_id: string | null;
  character_id: string | null;
  status: ImageJobStatus;
  prompt_user: string;
  prompt_final: string | null;
  negative_prompt: string | null;
  width: number;
  height: number;
  steps: number;
  cfg_scale: number;
  seed: number | null;
  loras_used: Array<Record<string, unknown>>;
  provider_used: string | null;
  providers_attempted: string[];
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  outputs: ImageOutput[];
}

export interface GenerateImageRequest {
  prompt: string;
  negative_prompt?: string;
  chat_id?: string;
  character_id?: string;
  width?: number;
  height?: number;
  steps?: number;
  cfg_scale?: number;
  seed?: number;
}

export const imagesApi = {
  generate: (body: GenerateImageRequest) =>
    apiFetch<ImageJob>("/api/v1/images/generate", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  get: (jobId: string) => apiFetch<ImageJob>(`/api/v1/images/jobs/${jobId}`),

  cancel: (jobId: string) =>
    apiFetch<ImageJob>(`/api/v1/images/jobs/${jobId}/cancel`, {
      method: "POST",
    }),
};

export type ImageEventType =
  | "image_queued"
  | "image_started"
  | "image_progress"
  | "image_completed"
  | "image_failed"
  | "image_canceled";

export async function streamImageJob(
  jobId: string,
  onEvent: (type: ImageEventType, data: Record<string, unknown>) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`/api/v1/images/jobs/${jobId}/events`, {
    credentials: "include",
    signal,
  });
  if (!res.body) return;
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      let eventType = "";
      let dataLine = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) eventType = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLine = line.slice(5).trim();
      }
      if (!eventType || !dataLine) continue;
      let data: Record<string, unknown> = {};
      try {
        data = JSON.parse(dataLine);
      } catch {
        continue;
      }
      onEvent(eventType as ImageEventType, data);
    }
  }
}
