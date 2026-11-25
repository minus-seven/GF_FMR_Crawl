export const FUND_FILTER_STORAGE_KEY = "fundscope:fund-database-filters:v1";

export interface SavedFundFilters {
  query: string;
  manager: string;
  category: string;
  reportPeriod: string;
}

export const emptyFundFilters: SavedFundFilters = {
  query: "",
  manager: "",
  category: "",
  reportPeriod: "",
};

type ReadableStorage = Pick<Storage, "getItem">;
type WritableStorage = Pick<Storage, "setItem" | "removeItem">;

export function hasExplicitFundFilters(params: URLSearchParams): boolean {
  return ["q", "manager", "category", "report_period"].some((key) =>
    params.has(key),
  );
}

export function readSavedFundFilters(
  storage: ReadableStorage | null,
): SavedFundFilters | null {
  if (!storage) return null;
  try {
    const raw = storage.getItem(FUND_FILTER_STORAGE_KEY);
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<SavedFundFilters>;
    return {
      query: typeof value.query === "string" ? value.query : "",
      manager: typeof value.manager === "string" ? value.manager : "",
      category: typeof value.category === "string" ? value.category : "",
      reportPeriod:
        typeof value.reportPeriod === "string" ? value.reportPeriod : "",
    };
  } catch {
    return null;
  }
}

export function saveFundFilters(
  storage: WritableStorage | null,
  filters: SavedFundFilters,
): void {
  if (!storage) return;
  try {
    storage.setItem(FUND_FILTER_STORAGE_KEY, JSON.stringify(filters));
  } catch {
    // Storage may be disabled by browser privacy settings.
  }
}

export function clearSavedFundFilters(storage: WritableStorage | null): void {
  if (!storage) return;
  try {
    storage.removeItem(FUND_FILTER_STORAGE_KEY);
  } catch {
    // Storage may be disabled by browser privacy settings.
  }
}

export function applySavedFundFilters(
  base: URLSearchParams,
  filters: SavedFundFilters,
): URLSearchParams {
  const next = new URLSearchParams(base);
  const values: Array<[string, string]> = [
    ["q", filters.query],
    ["manager", filters.manager],
    ["category", filters.category],
    ["report_period", filters.reportPeriod],
  ];
  values.forEach(([key, value]) => {
    if (value) next.set(key, value);
    else next.delete(key);
  });
  next.set("page", "1");
  return next;
}
