import { useEffect, useState } from "react";
import {
  Cpu,
  KeyRound,
  Save,
  ShieldCheck,
  Sparkles,
  Zap,
} from "lucide-react";
import { api, ApiError } from "../api/client";
import type { LocalModelHealth, ModelSettings, ModelSettingsUpdate } from "../api/types";
import { PageHeader } from "../components/PageHeader";
import { ErrorState, LoadingState } from "../components/States";

interface SettingsDraft {
  mineruMode: "lightweight" | "token";
  mineruUrl: string;
  mineruToken: string;
  clearMineruToken: boolean;
  structuredProvider: "deepseek" | "qwen" | "local";
  structuredThinking: boolean;
  deepseekUrl: string;
  deepseekKey: string;
  clearDeepseekKey: boolean;
  deepseekPrimary: string;
  deepseekFallback: string;
  deepseekVision: string;
  qwenUrl: string;
  qwenKey: string;
  clearQwenKey: boolean;
  qwenPrimary: string;
  qwenFallback: string;
  qwenVision: string;
  localModelUrl: string;
  localPrimary: string;
  localWorkers: number;
}

function draftFromSettings(settings: ModelSettings): SettingsDraft {
  return {
    mineruMode: settings.mineru.mode,
    mineruUrl: settings.mineru.api_url,
    mineruToken: settings.mineru.api_token,
    clearMineruToken: false,
    structuredProvider: settings.structured_provider,
    structuredThinking: settings.structured_thinking,
    deepseekUrl: settings.deepseek.base_url,
    deepseekKey: settings.deepseek.api_key,
    clearDeepseekKey: false,
    deepseekPrimary: settings.deepseek.primary_model,
    deepseekFallback: settings.deepseek.fallback_model,
    deepseekVision: settings.deepseek.vision_model,
    qwenUrl: settings.qwen.base_url,
    qwenKey: settings.qwen.api_key,
    clearQwenKey: false,
    qwenPrimary: settings.qwen.primary_model,
    qwenFallback: settings.qwen.fallback_model,
    qwenVision: settings.qwen.vision_model,
    localModelUrl: settings.local.base_url,
    localPrimary: settings.local.primary_model,
    localWorkers: settings.local.workers,
  };
}

export function SettingsPage() {
  const [settings, setSettings] = useState<ModelSettings | null>(null);
  const [draft, setDraft] = useState<SettingsDraft | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [saved, setSaved] = useState(false);
  const [showMineruToken, setShowMineruToken] = useState(false);
  const [showDeepseekKey, setShowDeepseekKey] = useState(false);
  const [showQwenKey, setShowQwenKey] = useState(false);
  const [localHealth, setLocalHealth] = useState<LocalModelHealth | null>(null);

  function load() {
    setLoading(true);
    api
      .modelSettings()
      .then((response) => {
        setSettings(response.data);
        setDraft(draftFromSettings(response.data));
        setError(null);
      })
      .catch((reason: unknown) => {
        setError(reason instanceof ApiError ? reason : null);
      })
      .finally(() => setLoading(false));
  }

  useEffect(load, []);

  useEffect(() => {
    api.localModelHealth().then((response) => setLocalHealth(response.data)).catch(() => setLocalHealth(null));
  }, [settings?.local.base_url]);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!draft) return;
    setSaving(true);
    setSaved(false);
    const body: ModelSettingsUpdate = {
      mineru: {
        mode: draft.mineruMode,
        api_url: draft.mineruUrl,
        ...(draft.mineruToken.trim()
          ? { api_token: draft.mineruToken.trim() }
          : {}),
        clear_api_token: draft.clearMineruToken,
      },
      structured_provider: draft.structuredProvider,
      structured_thinking: draft.structuredThinking,
      deepseek: {
        base_url: draft.deepseekUrl,
        ...(draft.deepseekKey.trim()
          ? { api_key: draft.deepseekKey.trim() }
          : {}),
        clear_api_key: draft.clearDeepseekKey,
        primary_model: draft.deepseekPrimary,
        fallback_model: draft.deepseekFallback,
        vision_model: draft.deepseekVision,
      },
      qwen: {
        base_url: draft.qwenUrl,
        ...(draft.qwenKey.trim() ? { api_key: draft.qwenKey.trim() } : {}),
        clear_api_key: draft.clearQwenKey,
        primary_model: draft.qwenPrimary,
        fallback_model: draft.qwenFallback,
        vision_model: draft.qwenVision,
      },
      local: {
        base_url: draft.localModelUrl,
        primary_model: draft.localPrimary,
        workers: draft.localWorkers,
      },
    };
    try {
      const response = await api.saveModelSettings(body);
      setSettings(response.data);
      setDraft(draftFromSettings(response.data));
      setError(null);
      setSaved(true);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason : null);
    } finally {
      setSaving(false);
    }
  }

  if (loading && !draft) {
    return <LoadingState label="正在读取本地模型配置…" />;
  }

  return (
    <>
      <PageHeader
        eyebrow="Local configuration"
        title="模型设置"
        description="配置外部模型路线与 API。真实密钥通过本机设置接口回填，并保存到本机 config.ini。"
      />
      {error ? (
        <ErrorState
          message={error.message}
          requestId={error.requestId}
          onRetry={load}
        />
      ) : null}
      {draft && settings ? (
        <form className="settings-form" onSubmit={save}>
          <section className="panel settings-card">
            <div className="settings-card-head">
              <div className="settings-icon"><Zap size={18} /></div>
              <div>
                <span className="eyebrow">Parsing engine</span>
                <h2>MinerU 解析</h2>
                <p>默认使用轻量型；Token 路线异常时自动降级。</p>
              </div>
              <span className="settings-state">
                {draft.mineruMode === "token" ? "Token 路线" : "轻量路线"}
              </span>
            </div>

            <div className="route-choice-grid" role="radiogroup" aria-label="MinerU 解析路线">
              <label className={`route-choice ${draft.mineruMode === "lightweight" ? "selected" : ""}`}>
                <input
                  type="radio"
                  name="mineru-mode"
                  checked={draft.mineruMode === "lightweight"}
                  onChange={() => setDraft({ ...draft, mineruMode: "lightweight" })}
                />
                <span className="route-choice-icon"><Sparkles size={16} /></span>
                <span>
                  <strong>轻量型</strong>
                  <small>默认 · 无需 Token · Agent API</small>
                </span>
              </label>
              <label className={`route-choice ${draft.mineruMode === "token" ? "selected" : ""}`}>
                <input
                  type="radio"
                  name="mineru-mode"
                  checked={draft.mineruMode === "token"}
                  onChange={() => setDraft({ ...draft, mineruMode: "token" })}
                />
                <span className="route-choice-icon"><KeyRound size={16} /></span>
                <span>
                  <strong>API Tokens</strong>
                  <small>Precise/VLM · 故障自动降级</small>
                </span>
              </label>
            </div>

            <div className="settings-fields">
              <label>
                <span>MinerU API 地址</span>
                <input
                  type="url"
                  required
                  value={draft.mineruUrl}
                  onChange={(event) => setDraft({ ...draft, mineruUrl: event.target.value })}
                />
              </label>
              <label>
                <span>
                  MinerU Token
                  <em>{settings.mineru.token_hint}</em>
                </span>
                <input
                  type={showMineruToken ? "text" : "password"}
                  autoComplete="new-password"
                  value={draft.mineruToken}
                  disabled={draft.clearMineruToken}
                  placeholder={
                    settings.mineru.token_configured
                      ? "MinerU API Token"
                      : "输入 API Token"
                  }
                  onChange={(event) => setDraft({ ...draft, mineruToken: event.target.value })}
                />
                <div className="secret-actions">
                  <button
                    className="secret-clear"
                    type="button"
                    onClick={() => setShowMineruToken((value) => !value)}
                  >
                    {showMineruToken ? "隐藏 Token" : "显示 Token"}
                  </button>
                  {settings.mineru.token_configured ? (
                    <button
                      className="secret-clear"
                      type="button"
                      onClick={() =>
                        setDraft({
                          ...draft,
                          clearMineruToken: !draft.clearMineruToken,
                          mineruToken: "",
                        })
                      }
                    >
                      {draft.clearMineruToken ? "撤销清除" : "清除已保存 Token"}
                    </button>
                  ) : null}
                </div>
              </label>
            </div>
            {draft.mineruMode === "token"
            && !settings.mineru.token_configured
            && !draft.mineruToken.trim() ? (
              <p className="settings-hint warning">保存 Token 路面前需要填写 Token。</p>
            ) : null}
          </section>

          <section className="panel settings-card">
            <div className="settings-card-head">
              <div className="settings-icon"><Cpu size={18} /></div>
              <div>
                <span className="eyebrow">Structured extraction</span>
                <h2>结构化模型供应商</h2>
                <p>DeepSeek Flash 默认用于高速抽取，Qwen 配置继续保留。</p>
              </div>
              <span className="settings-state">
                {draft.structuredProvider === "deepseek" ? "DeepSeek" : draft.structuredProvider === "qwen" ? "Qwen" : "本地 Qwen"}
              </span>
            </div>
            <div className="route-choice-grid" role="radiogroup" aria-label="结构化模型供应商">
              <label className={`route-choice ${draft.structuredProvider === "deepseek" ? "selected" : ""}`}>
                <input
                  type="radio"
                  name="structured-provider"
                  checked={draft.structuredProvider === "deepseek"}
                  onChange={() => setDraft({ ...draft, structuredProvider: "deepseek" })}
                />
                <span className="route-choice-icon"><Sparkles size={16} /></span>
                <span>
                  <strong>DeepSeek Flash</strong>
                  <small>默认 · 非思考 · 高并发低成本</small>
                </span>
              </label>
              <label className={`route-choice ${draft.structuredProvider === "qwen" ? "selected" : ""}`}>
                <input
                  type="radio"
                  name="structured-provider"
                  checked={draft.structuredProvider === "qwen"}
                  onChange={() => setDraft({ ...draft, structuredProvider: "qwen" })}
                />
                <span className="route-choice-icon"><Cpu size={16} /></span>
                <span>
                  <strong>Qwen / DashScope</strong>
                  <small>保留现有账户与模型配置</small>
                </span>
              </label>
              <label className={`route-choice ${draft.structuredProvider === "local" ? "selected" : ""}`}>
                <input
                  type="radio"
                  name="structured-provider"
                  checked={draft.structuredProvider === "local"}
                  onChange={() => setDraft({ ...draft, structuredProvider: "local", structuredThinking: false })}
                />
                <span className="route-choice-icon"><Cpu size={16} /></span>
                <span>
                  <strong>本地 Qwen 4B</strong>
                  <small>Ollama · 单并发 · 不使用云端 Key</small>
                </span>
              </label>
            </div>
            <label className="thinking-toggle">
              <input
                type="checkbox"
                checked={draft.structuredThinking}
                onChange={(event) =>
                  setDraft({ ...draft, structuredThinking: event.target.checked })
                }
              />
              <span>
                <strong>启用模型思考模式</strong>
                <small>关闭可显著降低延迟和输出 Token；复杂基金可在复核时临时开启。</small>
              </span>
            </label>
          </section>

          <section className="panel settings-card">
            <div className="settings-card-head">
              <div className="settings-icon"><Cpu size={18} /></div>
              <div>
                <span className="eyebrow">Local runtime</span>
                <h2>本地 Qwen 4B</h2>
                <p>使用 Ollama 的 OpenAI 兼容地址；仅支持 Markdown 文本结构化，视觉复核与联网 Ticker 会进入 review。</p>
              </div>
              <span className={`settings-state ${localHealth?.available ? "" : "missing"}`}>
                {localHealth?.available ? "运行中" : "未连接"}
              </span>
            </div>
            <div className="model-name-grid">
              <label>
                <span>本地服务地址</span>
                <input type="url" required value={draft.localModelUrl} onChange={(event) => setDraft({ ...draft, localModelUrl: event.target.value })} />
              </label>
              <label>
                <span>模型名称</span>
                <input required value={draft.localPrimary} onChange={(event) => setDraft({ ...draft, localPrimary: event.target.value })} />
              </label>
              <label>
                <span>本地并发</span>
                <select value={draft.localWorkers} onChange={(event) => setDraft({ ...draft, localWorkers: Number(event.target.value) })}>
                  <option value={1}>1（推荐，16GB）</option>
                  <option value={2}>2（仅空闲内存充足时）</option>
                </select>
              </label>
            </div>
            <p className={`settings-hint ${localHealth?.available ? "" : "warning"}`}>
              {localHealth?.available
                ? `已连接，可见模型：${localHealth.models.length ? localHealth.models.join("、") : "服务已响应"}`
                : "未检测到本地模型服务；保存配置不会自动安装 Ollama，请先运行 platform/scripts/setup_local_qwen.sh。"}
            </p>
          </section>

          <section className="panel settings-card">
            <div className="settings-card-head">
              <div className="settings-icon"><Sparkles size={18} /></div>
              <div>
                <span className="eyebrow">Primary provider</span>
                <h2>DeepSeek</h2>
                <p>主模型 Flash，复杂文本备用 V4 Pro；两者使用独立 API Key。</p>
              </div>
              <span className={`settings-state ${settings.deepseek.api_key_configured ? "" : "missing"}`}>
                {settings.deepseek.api_key_configured ? "API 已配置" : "缺少 API Key"}
              </span>
            </div>
            <div className="settings-fields">
              <label>
                <span>DeepSeek API 地址</span>
                <input
                  type="url"
                  required
                  value={draft.deepseekUrl}
                  onChange={(event) => setDraft({ ...draft, deepseekUrl: event.target.value })}
                />
              </label>
              <label>
                <span>
                  DeepSeek API Key
                  <em>{settings.deepseek.api_key_hint}</em>
                </span>
                <input
                  type={showDeepseekKey ? "text" : "password"}
                  autoComplete="new-password"
                  value={draft.deepseekKey}
                  disabled={draft.clearDeepseekKey}
                  placeholder="DeepSeek API Key"
                  onChange={(event) => setDraft({ ...draft, deepseekKey: event.target.value })}
                />
                <div className="secret-actions">
                  <button
                    className="secret-clear"
                    type="button"
                    onClick={() => setShowDeepseekKey((value) => !value)}
                  >
                    {showDeepseekKey ? "隐藏 API Key" : "显示 API Key"}
                  </button>
                  {settings.deepseek.api_key_configured ? (
                    <button
                      className="secret-clear"
                      type="button"
                      onClick={() =>
                        setDraft({
                          ...draft,
                          clearDeepseekKey: !draft.clearDeepseekKey,
                          deepseekKey: "",
                        })
                      }
                    >
                      {draft.clearDeepseekKey ? "撤销清除" : "清除已保存 API Key"}
                    </button>
                  ) : null}
                </div>
              </label>
            </div>
            <div className="model-name-grid">
              <label>
                <span>Flash 主模型</span>
                <input
                  required
                  value={draft.deepseekPrimary}
                  onChange={(event) => setDraft({ ...draft, deepseekPrimary: event.target.value })}
                />
              </label>
              <label>
                <span>文本备用模型</span>
                <input
                  required
                  value={draft.deepseekFallback}
                  onChange={(event) => setDraft({ ...draft, deepseekFallback: event.target.value })}
                />
              </label>
              <label>
                <span>视觉复核模型</span>
                <input
                  required
                  value={draft.deepseekVision}
                  onChange={(event) => setDraft({ ...draft, deepseekVision: event.target.value })}
                />
              </label>
            </div>
          </section>

          <section className="panel settings-card">
            <div className="settings-card-head">
              <div className="settings-icon"><Cpu size={18} /></div>
              <div>
                <span className="eyebrow">Fallback provider</span>
                <h2>Qwen / DashScope</h2>
                <p>保留现有配置，可随时作为结构化主供应商切换。</p>
              </div>
              <span className={`settings-state ${settings.qwen.api_key_configured ? "" : "missing"}`}>
                {settings.qwen.api_key_configured ? "API 已配置" : "缺少 API Key"}
              </span>
            </div>

            <div className="settings-fields">
              <label>
                <span>Qwen API 地址</span>
                <input
                  type="url"
                  required
                  value={draft.qwenUrl}
                  onChange={(event) => setDraft({ ...draft, qwenUrl: event.target.value })}
                />
              </label>
              <label>
                <span>
                  Qwen API Key
                  <em>{settings.qwen.api_key_hint}</em>
                </span>
                <input
                  type={showQwenKey ? "text" : "password"}
                  autoComplete="new-password"
                  value={draft.qwenKey}
                  disabled={draft.clearQwenKey}
                  placeholder={
                    settings.qwen.api_key_configured
                      ? "Qwen API Key"
                      : "输入 API Key"
                  }
                  onChange={(event) => setDraft({ ...draft, qwenKey: event.target.value })}
                />
                <div className="secret-actions">
                  <button
                    className="secret-clear"
                    type="button"
                    onClick={() => setShowQwenKey((value) => !value)}
                  >
                    {showQwenKey ? "隐藏 API Key" : "显示 API Key"}
                  </button>
                  {settings.qwen.api_key_configured ? (
                    <button
                      className="secret-clear"
                      type="button"
                      onClick={() =>
                        setDraft({
                          ...draft,
                          clearQwenKey: !draft.clearQwenKey,
                          qwenKey: "",
                        })
                      }
                    >
                      {draft.clearQwenKey ? "撤销清除" : "清除已保存 API Key"}
                    </button>
                  ) : null}
                </div>
              </label>
            </div>

            <div className="model-name-grid">
              <label>
                <span>主模型</span>
                <input
                  required
                  value={draft.qwenPrimary}
                  onChange={(event) => setDraft({ ...draft, qwenPrimary: event.target.value })}
                />
              </label>
              <label>
                <span>备用模型</span>
                <input
                  required
                  value={draft.qwenFallback}
                  onChange={(event) => setDraft({ ...draft, qwenFallback: event.target.value })}
                />
              </label>
              <label>
                <span>视觉模型</span>
                <input
                  required
                  value={draft.qwenVision}
                  onChange={(event) => setDraft({ ...draft, qwenVision: event.target.value })}
                />
              </label>
            </div>
          </section>

          <div className="settings-actions">
            <div className="settings-security">
              <ShieldCheck size={16} />
              <span>真实值仅在本机页面回填和保存，新设置对后续任务生效。</span>
            </div>
            {saved ? <span className="settings-saved">已保存</span> : null}
            <button
              className="button primary"
              disabled={
                saving
                || (
                  draft.mineruMode === "token"
                  && (
                    draft.clearMineruToken
                    || (
                      !settings.mineru.token_configured
                      && !draft.mineruToken.trim()
                    )
                  )
                )
                || (
                  draft.structuredProvider === "deepseek"
                  && (
                    draft.clearDeepseekKey
                    || (
                      !settings.deepseek.api_key_configured
                      && !draft.deepseekKey.trim()
                    )
                  )
                )
                || (
                  draft.structuredProvider === "qwen"
                  && (
                    draft.clearQwenKey
                    || (
                      !settings.qwen.api_key_configured
                      && !draft.qwenKey.trim()
                    )
                  )
                )
              }
            >
              <Save size={16} />
              {saving ? "保存中…" : "保存设置"}
            </button>
          </div>
        </form>
      ) : null}
    </>
  );
}
