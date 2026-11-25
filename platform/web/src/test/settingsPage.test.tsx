import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { SettingsPage } from "../pages/SettingsPage";

vi.mock("../api/client", () => {
  class MockApiError extends Error {
    requestId = "";
  }
  return {
    ApiError: MockApiError,
    api: {
      modelSettings: vi.fn(),
      localModelHealth: vi.fn(),
      saveModelSettings: vi.fn(),
    },
  };
});

const modelSettings = {
  mineru: {
    mode: "token" as const,
    api_url: "https://mineru.example.com/api",
    api_token: "mineru-real-token",
    token_configured: true,
    token_hint: "min***ken",
  },
  structured_provider: "deepseek" as const,
  structured_thinking: false,
  deepseek: {
    base_url: "https://api.deepseek.com",
    api_key: "deepseek-real-key",
    api_key_configured: true,
    api_key_hint: "dee***key",
    primary_model: "deepseek-flash",
    fallback_model: "deepseek-v4-pro",
    vision_model: "deepseek-flash",
  },
  qwen: {
    base_url: "https://qwen.example.com/v1",
    api_key: "qwen-real-key",
    api_key_configured: true,
    api_key_hint: "qwe***key",
    primary_model: "qwen-primary",
    fallback_model: "qwen-fallback",
    vision_model: "qwen-vision",
  },
  local: {
    base_url: "http://127.0.0.1:11434/v1",
    primary_model: "qwen3:4b",
    workers: 1,
  },
};

describe("model settings page", () => {
  beforeEach(() => {
    vi.mocked(api.modelSettings).mockResolvedValue({
      data: modelSettings,
      snapshot_id: null,
      request_id: "req_settings",
      meta: { server_time: "2026-10-26T00:00:00Z" },
    });
    vi.mocked(api.localModelHealth).mockResolvedValue({
      data: {
        available: false,
        base_url: modelSettings.local.base_url,
        models: [],
        reason: "not running",
      },
      snapshot_id: null,
      request_id: "req_local_health",
      meta: { server_time: "2026-10-26T00:00:00Z" },
    });
  });

  afterEach(() => cleanup());

  it("fills real tokens and keys into password inputs", async () => {
    render(<SettingsPage />);
    await waitFor(() => {
      expect(screen.getByDisplayValue("mineru-real-token")).toBeInTheDocument();
      expect(screen.getByDisplayValue("deepseek-real-key")).toBeInTheDocument();
      expect(screen.getByDisplayValue("qwen-real-key")).toBeInTheDocument();
    });
  });

  it("switches saved secrets between hidden and visible fields", async () => {
    const { container } = render(<SettingsPage />);
    await screen.findByDisplayValue("mineru-real-token");
    const tokenInput = container.querySelectorAll<HTMLInputElement>(
      'input[type="password"]',
    )[0];
    fireEvent.click(screen.getByRole("button", { name: "显示 Token" }));
    expect(tokenInput.type).toBe("text");
    expect(tokenInput.value).toBe("mineru-real-token");
  });
});
