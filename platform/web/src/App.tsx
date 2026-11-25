import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AppShell } from "./app/AppShell";
import { ScopeProvider } from "./context/ScopeContext";
import { FundDetailPage } from "./pages/FundDetailPage";
import { FundsPage } from "./pages/FundsPage";
import { OverviewPage } from "./pages/OverviewPage";
import { KnowledgePage } from "./pages/KnowledgePage";
import { QualityPage } from "./pages/QualityPage";
import { QualityReviewPage } from "./pages/QualityReviewPage";
import { SettingsPage } from "./pages/SettingsPage";
import { TasksPage } from "./pages/TasksPage";

function NotFoundPage() {
  return (
    <section className="not-found">
      <span>404</span>
      <h1>页面不存在</h1>
      <p>该研究视图可能已经移动，请从左侧导航重新进入。</p>
    </section>
  );
}

export default function App() {
  return (
    <ScopeProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<AppShell />}>
            <Route index element={<OverviewPage />} />
            <Route path="funds" element={<FundsPage />} />
            <Route path="funds/:code" element={<FundDetailPage />} />
            <Route path="knowledge" element={<KnowledgePage />} />
            <Route path="quality" element={<QualityPage />} />
            <Route path="quality/:fundCode" element={<QualityReviewPage />} />
            <Route path="tasks" element={<TasksPage />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ScopeProvider>
  );
}
