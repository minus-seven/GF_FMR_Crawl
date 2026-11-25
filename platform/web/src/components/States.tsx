import { AlertCircle, Inbox, LoaderCircle } from "lucide-react";

export function LoadingState({ label = "正在读取数据…" }: { label?: string }) {
  return (
    <div className="state-card">
      <LoaderCircle className="spin" size={20} />
      <span>{label}</span>
    </div>
  );
}

export function ErrorState({
  message,
  requestId,
  onRetry,
}: {
  message: string;
  requestId?: string;
  onRetry?: () => void;
}) {
  return (
    <div className="state-card state-error">
      <AlertCircle size={20} />
      <div>
        <strong>数据读取失败</strong>
        <p>{message}</p>
        {requestId ? <code>{requestId}</code> : null}
      </div>
      {onRetry ? (
        <button className="button secondary" onClick={onRetry}>
          重试
        </button>
      ) : null}
    </div>
  );
}

export function EmptyState({
  title = "没有匹配结果",
  description = "调整筛选条件后再试。",
}: {
  title?: string;
  description?: string;
}) {
  return (
    <div className="state-card">
      <Inbox size={22} />
      <div>
        <strong>{title}</strong>
        <p>{description}</p>
      </div>
    </div>
  );
}
