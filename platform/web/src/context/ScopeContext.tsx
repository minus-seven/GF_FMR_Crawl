import { createContext, useContext, useState, type ReactNode } from "react";

export interface ChartScope {
  fundCodes: string[];
  managerId?: string;
  category?: string;
  sourceDocumentVersionId?: string;
  label: string;
}

interface ScopeValue {
  scope: ChartScope;
  setScope: (scope: ChartScope) => void;
}

const defaultScope: ChartScope = {
  fundCodes: [],
  label: "全部基金",
};

const ScopeContext = createContext<ScopeValue | null>(null);

export function ScopeProvider({ children }: { children: ReactNode }) {
  const [scope, setScope] = useState<ChartScope>(defaultScope);
  return (
    <ScopeContext.Provider value={{ scope, setScope }}>
      {children}
    </ScopeContext.Provider>
  );
}

export function useScope(): ScopeValue {
  const value = useContext(ScopeContext);
  if (!value) throw new Error("useScope must be used inside ScopeProvider");
  return value;
}
