import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { ScopeProvider } from "../context/ScopeContext";
import {
  FUND_FILTER_STORAGE_KEY,
  applySavedFundFilters,
  clearSavedFundFilters,
  readSavedFundFilters,
  saveFundFilters,
} from "../lib/fundFilters";
import { FundsPage } from "../pages/FundsPage";

vi.mock("../api/client", () => {
  class MockApiError extends Error {
    requestId = "";
  }
  return {
    ApiError: MockApiError,
    api: { funds: vi.fn() },
  };
});

const emptyPage = {
  data: {
    items: [],
    page: 1,
    page_size: 20,
    total: 0,
  },
  snapshot_id: "snapshot-test",
  request_id: "request-test",
  meta: { server_time: "2026-10-27T00:00:00Z" },
};

function renderFundsPage() {
  return render(
    <MemoryRouter initialEntries={["/funds"]}>
      <ScopeProvider>
        <Routes>
          <Route path="/funds" element={<FundsPage />} />
        </Routes>
      </ScopeProvider>
    </MemoryRouter>,
  );
}

describe("fund database filter persistence", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.mocked(api.funds).mockResolvedValue(emptyPage);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("keeps the first visit empty when no saved filters exist", async () => {
    renderFundsPage();

    await waitFor(() => {
      expect(api.funds).toHaveBeenCalledWith(
        expect.objectContaining({
          q: "",
          manager: "",
          category: "",
          reportPeriod: "",
          page: 1,
        }),
      );
    });
    expect(screen.getByPlaceholderText("名称、英文名或基金代码")).toHaveValue("");
    expect(screen.getByPlaceholderText("如 摩根大通")).toHaveValue("");
    expect(screen.queryByLabelText("清空搜索")).not.toBeInTheDocument();
  });

  it("restores the complete previous filter set", async () => {
    saveFundFilters(window.localStorage, {
      query: "环球债券",
      manager: "柏瑞",
      category: "固收",
      reportPeriod: "2026-07-01",
    });

    renderFundsPage();

    await waitFor(() => {
      expect(api.funds).toHaveBeenCalledWith(
        expect.objectContaining({
          q: "环球债券",
          manager: "柏瑞",
          category: "固收",
          reportPeriod: "2026-07-01",
          page: 1,
        }),
      );
    });
    expect(screen.getByPlaceholderText("名称、英文名或基金代码")).toHaveValue(
      "环球债券",
    );
    expect(screen.getByPlaceholderText("如 摩根大通")).toHaveValue("柏瑞");
    expect(screen.getByDisplayValue("固收")).toBeInTheDocument();
    expect(screen.getByDisplayValue("2026-07")).toBeInTheDocument();
  });

  it("clears filters and removes the saved state", async () => {
    saveFundFilters(window.localStorage, {
      query: "环球债券",
      manager: "柏瑞",
      category: "固收",
      reportPeriod: "2026-07-01",
    });
    renderFundsPage();
    await screen.findByDisplayValue("环球债券");

    fireEvent.click(screen.getByRole("button", { name: "清空" }));

    await waitFor(() => {
      expect(screen.getByPlaceholderText("名称、英文名或基金代码")).toHaveValue("");
      expect(screen.getByPlaceholderText("如 摩根大通")).toHaveValue("");
    });
    expect(readSavedFundFilters(window.localStorage)).toEqual({
      query: "",
      manager: "",
      category: "",
      reportPeriod: "",
    });
  });

  it("serializes and applies saved filter parameters deterministically", () => {
    saveFundFilters(window.localStorage, {
      query: "债券",
      manager: "柏瑞",
      category: "固收",
      reportPeriod: "2026-07-01",
    });
    expect(window.localStorage.getItem(FUND_FILTER_STORAGE_KEY)).toContain("债券");
    const params = applySavedFundFilters(new URLSearchParams("page=8"), {
      query: "债券",
      manager: "柏瑞",
      category: "固收",
      reportPeriod: "2026-07-01",
    });
    expect(params.get("q")).toBe("债券");
    expect(params.get("manager")).toBe("柏瑞");
    expect(params.get("category")).toBe("固收");
    expect(params.get("report_period")).toBe("2026-07-01");
    expect(params.get("page")).toBe("1");

    clearSavedFundFilters(window.localStorage);
    expect(readSavedFundFilters(window.localStorage)).toBeNull();
  });
});
