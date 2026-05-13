import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";

import { ImageCard } from "./ImageCard";

vi.mock("../../api/images", () => {
  // Capture the latest event handler so tests can drive the SSE state machine
  // synchronously from outside the component.
  let handler:
    | ((type: string, data: Record<string, unknown>) => void)
    | null = null;
  return {
    streamImageJob: (
      _jobId: string,
      onEvent: (type: string, data: Record<string, unknown>) => void,
    ) => {
      handler = onEvent;
      // Never resolves — the test drives state via __emit__.
      return new Promise(() => undefined);
    },
    __emit__: (type: string, data: Record<string, unknown> = {}) => {
      if (handler) handler(type, data);
    },
  };
});

import * as imagesMod from "../../api/images";
const rawEmit = (
  imagesMod as unknown as {
    __emit__: (type: string, data?: Record<string, unknown>) => void;
  }
).__emit__;

function emit(type: string, data: Record<string, unknown> = {}): void {
  act(() => {
    rawEmit(type, data);
  });
}

beforeEach(() => {
  // Each test gets a fresh ImageCard so its handler is the most recent.
});

describe("ImageCard SSE state machine", () => {
  it("shows Queued initially, then Generating on image_started", async () => {
    render(<ImageCard jobId="abc" prompt="a forest" />);
    expect(screen.getByText(/Queued/)).toBeInTheDocument();

    emit("image_started");
    await waitFor(() => {
      expect(screen.getByText(/Generating/)).toBeInTheDocument();
    });
  });

  it("renders HQ stage labels — backdrop and character_1 and harmonize", async () => {
    render(<ImageCard jobId="def" prompt="captain on bridge" kind="hq" />);
    emit("image_started");
    emit("image_progress", { stage: "backdrop" });
    await waitFor(() => {
      expect(screen.getByText(/Generating backdrop/)).toBeInTheDocument();
    });

    emit("image_progress", { stage: "character_1" });
    await waitFor(() => {
      expect(screen.getByText(/Painting character 1/)).toBeInTheDocument();
    });

    emit("image_progress", { stage: "harmonize" });
    await waitFor(() => {
      expect(screen.getByText(/Harmonizing lighting/)).toBeInTheDocument();
    });
  });

  it("renders <img> on image_completed with storage_url", async () => {
    render(<ImageCard jobId="ghi" prompt="tavern" />);
    emit("image_completed", {
      outputs: [{ storage_url: "/static/images/ghi/0.png" }],
    });
    await waitFor(() => {
      const img = screen.getByRole("img");
      expect(img).toHaveAttribute("src", "/static/images/ghi/0.png");
    });
  });

  it("shows error text on image_failed", async () => {
    render(<ImageCard jobId="jkl" prompt="tavern" />);
    emit("image_failed", { error: "all_providers_failed" });
    await waitFor(() => {
      expect(screen.getByText(/all_providers_failed/)).toBeInTheDocument();
    });
  });
});
