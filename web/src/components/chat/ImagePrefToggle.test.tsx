import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

import { ImagePrefToggle } from "./ImagePrefToggle";
import { useAuthStore } from "../../store/authStore";

vi.mock("../../api/users", () => ({
  usersApi: {
    updateProfile: vi.fn(),
  },
}));

import { usersApi } from "../../api/users";

const updateProfile = usersApi.updateProfile as unknown as ReturnType<
  typeof vi.fn
>;

beforeEach(() => {
  updateProfile.mockReset();
  useAuthStore.setState({
    user: {
      id: "u1",
      username: "tester",
      email: "t@e.com",
      display_name: "Tester",
      image_pref: "auto",
    },
    isLoading: false,
  });
});

describe("ImagePrefToggle", () => {
  it("renders Auto label by default and cycles through auto → always → never → auto", async () => {
    updateProfile.mockImplementation(async ({ image_pref }: { image_pref: string }) => ({
      id: "u1",
      username: "tester",
      email: "t@e.com",
      display_name: "Tester",
      image_pref,
    }));

    render(<ImagePrefToggle />);
    expect(screen.getByRole("button")).toHaveTextContent(/Auto/);

    fireEvent.click(screen.getByRole("button"));
    await waitFor(() =>
      expect(screen.getByRole("button")).toHaveTextContent(/Always/),
    );
    expect(updateProfile).toHaveBeenLastCalledWith({ image_pref: "always" });

    fireEvent.click(screen.getByRole("button"));
    await waitFor(() =>
      expect(screen.getByRole("button")).toHaveTextContent(/Never/),
    );
    expect(updateProfile).toHaveBeenLastCalledWith({ image_pref: "never" });

    fireEvent.click(screen.getByRole("button"));
    await waitFor(() =>
      expect(screen.getByRole("button")).toHaveTextContent(/Auto/),
    );
    expect(updateProfile).toHaveBeenLastCalledWith({ image_pref: "auto" });
  });

  it("silently keeps the previous label when the API call fails", async () => {
    updateProfile.mockRejectedValue(new Error("network down"));
    render(<ImagePrefToggle />);
    expect(screen.getByRole("button")).toHaveTextContent(/Auto/);
    fireEvent.click(screen.getByRole("button"));
    // Wait for the rejected promise to settle; label should remain Auto
    // because setUser was never called.
    await waitFor(() => expect(updateProfile).toHaveBeenCalled());
    expect(screen.getByRole("button")).toHaveTextContent(/Auto/);
  });
});
