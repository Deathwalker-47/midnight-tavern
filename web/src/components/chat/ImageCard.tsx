/**
 * In-chat image generation card.
 *
 * Subscribes to the job's SSE event stream and renders status transitions.
 * Final image is served via ``/static/images/{job_id}/0.png``.
 */

import { useEffect, useState } from "react";
import {
  streamImageJob,
  type ImageJobStatus,
  type ImageEventType,
} from "../../api/images";

interface ImageCardProps {
  jobId: string;
  prompt: string;
}

export function ImageCard({ jobId, prompt }: ImageCardProps) {
  const [status, setStatus] = useState<ImageJobStatus>("queued");
  const [stage, setStage] = useState<string | null>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const abort = new AbortController();
    streamImageJob(
      jobId,
      (type: ImageEventType, data: Record<string, unknown>) => {
        switch (type) {
          case "image_queued":
            setStatus("queued");
            break;
          case "image_started":
            setStatus("running");
            break;
          case "image_progress":
            setStage((data.stage as string) ?? null);
            break;
          case "image_completed": {
            setStatus("completed");
            const outputs = data.outputs as Array<{ storage_url: string }> | undefined;
            if (outputs && outputs[0]) setImageUrl(outputs[0].storage_url);
            break;
          }
          case "image_failed":
            setStatus("failed");
            setError((data.error as string) ?? "Generation failed");
            break;
          case "image_canceled":
            setStatus("canceled");
            break;
        }
      },
      abort.signal,
    ).catch(() => {
      /* ignore — stream may close on completion */
    });
    return () => abort.abort();
  }, [jobId]);

  return (
    <div className="flex justify-start">
      <div className="max-w-[75%] rounded-2xl rounded-bl-sm bg-gray-800 border border-gray-700 overflow-hidden">
        <div className="px-4 py-2 text-xs text-gray-400 border-b border-gray-700/50">
          🎨 <span className="font-medium text-gray-300">{prompt}</span>
        </div>
        {status === "completed" && imageUrl ? (
          <img src={imageUrl} alt={prompt} className="w-full h-auto max-w-md" />
        ) : status === "failed" ? (
          <div className="px-4 py-6 text-sm text-red-400">{error ?? "Failed"}</div>
        ) : status === "canceled" ? (
          <div className="px-4 py-6 text-sm text-gray-500">Canceled</div>
        ) : (
          <div className="px-4 py-6 text-sm text-gray-400 flex items-center gap-2">
            <span className="inline-block h-2 w-2 rounded-full bg-indigo-500 animate-pulse" />
            {status === "queued" && "Queued…"}
            {status === "running" && (stage ? `Generating (${stage})…` : "Generating…")}
          </div>
        )}
      </div>
    </div>
  );
}
