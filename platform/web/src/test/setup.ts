import "@testing-library/jest-dom/vitest";

// Node 26 can disable jsdom's storage backend unless a localstorage file is
// supplied. The browser code only needs the Web Storage contract in tests, so
// provide a deterministic in-memory fallback when jsdom leaves it undefined.
if (typeof window !== "undefined" && !window.localStorage) {
  const values = new Map<string, string>();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      clear: () => values.clear(),
      getItem: (key: string) => values.get(key) ?? null,
      key: (index: number) => Array.from(values.keys())[index] ?? null,
      removeItem: (key: string) => values.delete(key),
      setItem: (key: string, value: string) => values.set(key, String(value)),
      get length() {
        return values.size;
      },
    } satisfies Storage,
  });
}
