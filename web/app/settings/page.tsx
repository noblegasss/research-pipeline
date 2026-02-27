"use client";
import { useEffect, useState, useRef } from "react";
import { api, loadLocalSettings, saveLocalSettings, type AppSettings } from "@/lib/api";
import { Plus, X, Check, Loader2, Eye, EyeOff, KeyRound } from "lucide-react";
import { useI18n } from "@/lib/i18n";

const OPENAI_MODELS = ["gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini", "gpt-4o", "gpt-5"];
const GEMINI_MODELS = [
  "gemini-2.5-flash-lite",
  "gemini-2.5-flash",
  "gemini-1.5-flash",
  "gemini-2.5-pro",
  "gemini-3-pro-preview",
];

// ── TagList ────────────────────────────────────────────────────────────────
function TagList({
  items, options, onAdd, onRemove, placeholder,
}: {
  items: string[];
  options?: string[];
  onAdd: (v: string) => void;
  onRemove: (v: string) => void;
  placeholder?: string;
}) {
  const [query, setQuery] = useState("");
  const [show, setShow] = useState(false);
  // containerRef wraps the ENTIRE component so onBlur relatedTarget check works
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const filtered = options
    ? options.filter((o) => !items.includes(o) && o.toLowerCase().includes(query.toLowerCase()))
    : [];

  function add(val: string) {
    const v = val.trim();
    if (v && !items.includes(v)) {
      onAdd(v);
      setQuery("");
      setShow(true);
      // Use rAF so the DOM settles before re-focusing, keeping the dropdown open
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }

  return (
    <div ref={containerRef}>
      {/* Selected tags */}
      <div className="flex flex-wrap gap-1.5 mb-2 min-h-[28px]">
        {items.map((item) => (
          <span key={item}
            className="flex items-center gap-1 text-xs bg-blue-50 text-blue-800 border border-blue-200 px-2 py-1 rounded-full">
            {item}
            <button onClick={() => onRemove(item)} className="hover:text-red-500 transition-colors ml-0.5">
              <X size={10} />
            </button>
          </span>
        ))}
        {items.length === 0 && <span className="text-xs text-gray-400 self-center">None selected</span>}
      </div>

      {/* Input row + dropdown */}
      <div className="relative">
        <div className="flex gap-2">
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => { setQuery(e.target.value); setShow(true); }}
            onFocus={() => setShow(true)}
            onBlur={(e) => {
              // Only close if focus moves OUTSIDE the entire TagList container
              if (!containerRef.current?.contains(e.relatedTarget as Node)) {
                setShow(false);
              }
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); add(query); }
              if (e.key === "Escape") { setShow(false); }
            }}
            placeholder={placeholder ?? "Search or type to add…"}
            className="flex-1 text-sm border rounded-lg px-3 py-2 outline-none focus:border-blue-400 transition-colors bg-white"
          />
          {/* onMouseDown preventDefault keeps input focused when clicking Add */}
          <button
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => add(query)}
            className="text-sm bg-gray-900 text-white px-3 py-2 rounded-lg flex items-center gap-1.5 hover:bg-gray-700 transition-colors whitespace-nowrap">
            <Plus size={13} /> Add
          </button>
        </div>

        {show && filtered.length > 0 && (
          <div className="absolute left-0 right-12 top-full mt-1 z-50 bg-white border rounded-lg shadow-xl max-h-52 overflow-y-auto">
            {!query && filtered.length > 80 && (
              <p className="text-[11px] text-gray-400 px-3 pt-2 pb-1 border-b">
                {filtered.length} options — type to search
              </p>
            )}
            {/* Show all matches when user is typing; limit to 80 when browsing */}
            {(query ? filtered : filtered.slice(0, 80)).map((opt) => (
              <button
                key={opt}
                // preventDefault on mousedown keeps the input focused (no blur fires)
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => add(opt)}
                className="w-full text-left text-sm px-3 py-2 hover:bg-blue-50 transition-colors"
              >
                {opt}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Section wrapper ────────────────────────────────────────────────────────
function Section({ title, children, highlight }: { title: string; children: React.ReactNode; highlight?: boolean }) {
  return (
    <div className={`border rounded-xl ${highlight ? "border-blue-300 shadow-sm" : ""}`}
      style={{ borderColor: highlight ? undefined : "var(--card-border)" }}>
      <div className={`px-4 py-3 border-b text-sm font-semibold rounded-t-xl ${highlight ? "bg-blue-50 text-blue-900" : "bg-gray-50 text-gray-700"}`}
        style={{ borderColor: highlight ? "#bfdbfe" : "var(--card-border)" }}>
        {title}
      </div>
      <div className="p-4 bg-white rounded-b-xl">{children}</div>
    </div>
  );
}

// ── Main page ──────────────────────────────────────────────────────────────
export default function SettingsPage() {
  const { t } = useI18n();
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [synced, setSynced] = useState(false);
  const [showOpenAIKey, setShowOpenAIKey] = useState(false);
  const [showGeminiKey, setShowGeminiKey] = useState(false);
  const [backendOk, setBackendOk] = useState<boolean | null>(null);
  // Track whether the initial load is done so we don't auto-save before reading
  const initializedRef = useRef(false);

  // Load from localStorage immediately, then merge backend in background
  useEffect(() => {
    const local = loadLocalSettings();
    setSettings(local);
    api.getSettings().then((merged) => {
      setSettings(merged);
      setBackendOk(true);
    }).catch(() => setBackendOk(false))
      .finally(() => { initializedRef.current = true; });
  }, []);

  // Auto-save to localStorage on every settings change (after initial load)
  useEffect(() => {
    if (!settings) return;
    if (!initializedRef.current) return;
    saveLocalSettings(settings);
  }, [settings]);

  async function handleSyncBackend() {
    if (!settings) return;
    setSyncing(true);
    setSynced(false);
    saveLocalSettings(settings);
    try {
      await api.saveSettings(settings);
      setBackendOk(true);
      setSynced(true);
      setTimeout(() => setSynced(false), 2500);
    } catch {
      setBackendOk(false);
    } finally {
      setSyncing(false);
    }
  }

  function patch<K extends keyof AppSettings>(key: K, val: AppSettings[K]) {
    setSettings((s) => s ? { ...s, [key]: val } : s);
  }

  // Notify all mounted pages to refresh language after state/localStorage updates.
  useEffect(() => {
    if (!settings) return;
    if (typeof window === "undefined") return;
    window.dispatchEvent(new Event("app-language-change"));
  }, [settings]);

  useEffect(() => {
    if (!settings) return;
    setSettings((prev) => {
      if (!prev) return prev;
      const options = prev.api_provider === "gemini" ? GEMINI_MODELS : OPENAI_MODELS;
      if (options.includes(prev.api_model)) return prev;
      return { ...prev, api_model: options[0] };
    });
  }, [settings]);

  if (!settings) return (
    <div className="flex items-center justify-center h-64 text-gray-400 text-sm">{t("loading")}</div>
  );

  const allJournals = [...settings.journals, ...settings.custom_journals];
  const modelOptions = settings.api_provider === "gemini" ? GEMINI_MODELS : OPENAI_MODELS;

  return (
    <div className="max-w-2xl mx-auto px-8 py-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold text-gray-900">⚙️ {t("settings")}</h1>
          <p className="text-xs text-gray-400 mt-0.5">
            Auto-saved to browser.{" "}
            {backendOk === true && <span className="text-green-600">✓ Backend synced</span>}
            {backendOk === false && <span className="text-orange-500">Backend offline — local only</span>}
          </p>
        </div>
        <button onClick={handleSyncBackend} disabled={syncing}
          className="flex items-center gap-2 text-sm bg-gray-900 text-white px-4 py-2 rounded-lg hover:bg-gray-700 disabled:opacity-50 transition-colors">
          {syncing ? <Loader2 size={14} className="animate-spin" /> : synced ? <Check size={14} /> : null}
          {synced ? "Synced!" : "Sync to Backend"}
        </button>
        </div>

      <div className="space-y-5">
        {/* ── Language ── */}
        <Section title={`🌐 ${t("language")}`}>
          <div className="flex gap-2">
            {(["en", "zh"] as const).map((lng) => (
              <button
                key={lng}
                onClick={() => patch("language", lng)}
                className={`text-sm px-4 py-2 rounded-lg border transition-colors ${
                  settings.language === lng
                    ? "bg-blue-600 text-white border-blue-600"
                    : "bg-white text-gray-600 border-gray-200 hover:border-blue-400"
                }`}
              >
                {lng === "en" ? t("english") : t("chinese")}
              </button>
            ))}
          </div>
        </Section>

        {/* ── AI Provider & API Keys ── */}
        <Section title="🤖 AI Provider" highlight>
          <p className="text-xs text-gray-500 mb-3">
            Choose provider first, then fill the corresponding API key.
          </p>
          <div className="flex gap-2 mb-4">
            {(["gemini", "openai"] as const).map((provider) => (
              <button
                key={provider}
                onClick={() => patch("api_provider", provider)}
                className={`text-sm px-4 py-2 rounded-lg border transition-colors ${
                  settings.api_provider === provider
                    ? "bg-blue-600 text-white border-blue-600"
                    : "bg-white text-gray-600 border-gray-200 hover:border-blue-400"
                }`}
              >
                {provider === "gemini" ? "Gemini" : "OpenAI"}
              </button>
            ))}
          </div>

          <label className="text-xs font-medium text-gray-600 block mb-1.5">Gemini API Key</label>
          <div className="relative mb-2">
            <KeyRound size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              type={showGeminiKey ? "text" : "password"}
              value={settings.gemini_api_key}
              onChange={(e) => patch("gemini_api_key", e.target.value)}
              placeholder="AIza…"
              className="w-full text-sm border rounded-lg pl-9 pr-10 py-2.5 outline-none focus:border-blue-400 transition-colors font-mono bg-white"
            />
            <button
              onClick={() => setShowGeminiKey((v) => !v)}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 transition-colors"
            >
              {showGeminiKey ? <EyeOff size={15} /> : <Eye size={15} />}
            </button>
          </div>
          {settings.gemini_api_key && (
            <p className="text-xs text-green-600 mb-3 flex items-center gap-1">
              <Check size={11} /> Gemini key set
            </p>
          )}

          <label className="text-xs font-medium text-gray-600 block mb-1.5">OpenAI API Key</label>
          <p className="text-xs text-gray-500 mb-3">
            Stored only in your browser unless you click sync.
          </p>
          <div className="relative">
            <KeyRound size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              type={showOpenAIKey ? "text" : "password"}
              value={settings.openai_api_key}
              onChange={(e) => patch("openai_api_key", e.target.value)}
              placeholder="sk-proj-…"
              className="w-full text-sm border rounded-lg pl-9 pr-10 py-2.5 outline-none focus:border-blue-400 transition-colors font-mono bg-white"
            />
            <button
              onClick={() => setShowOpenAIKey((v) => !v)}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 transition-colors"
            >
              {showOpenAIKey ? <EyeOff size={15} /> : <Eye size={15} />}
            </button>
          </div>
          {settings.openai_api_key && (
            <p className="text-xs text-green-600 mt-1.5 flex items-center gap-1">
              <Check size={11} /> OpenAI key set
            </p>
          )}
          <div className="mt-3">
            <label className="text-xs font-medium text-gray-600 block mb-1.5">Model</label>
            <div className="flex gap-2 flex-wrap">
              {modelOptions.map((m) => (
                <button key={m} onClick={() => patch("api_model", m)}
                  className={`text-xs px-3 py-1.5 rounded-full border transition-colors ${
                    settings.api_model === m
                      ? "bg-blue-600 text-white border-blue-600"
                      : "bg-white text-gray-600 border-gray-200 hover:border-blue-400"
                  }`}>
                  {m}
                </button>
              ))}
            </div>
          </div>
        </Section>

        {/* ── Journals ── */}
        <Section title="📰 Journals to Track">
          <p className="text-xs text-gray-400 mb-3">
            Search from 70+ journals or type any name to add a custom one.
          </p>
          <TagList
            items={allJournals}
            options={settings.journal_options}
            placeholder="Search journals (e.g. Nature Medicine, Cell)…"
            onAdd={(v) => {
              const isKnown = settings.journal_options.includes(v);
              if (isKnown) patch("journals", [...settings.journals.filter(j => j !== v), v]);
              else patch("custom_journals", [...settings.custom_journals.filter(j => j !== v), v]);
            }}
            onRemove={(v) => {
              patch("journals", settings.journals.filter((j) => j !== v));
              patch("custom_journals", settings.custom_journals.filter((j) => j !== v));
            }}
          />
        </Section>

        {/* ── Fields ── */}
        <Section title="🔬 Research Fields">
          <TagList
            items={settings.fields}
            options={settings.field_options}
            placeholder="Add field (e.g. Machine Learning, Neuroscience)…"
            onAdd={(v) => patch("fields", [...settings.fields, v])}
            onRemove={(v) => patch("fields", settings.fields.filter((f) => f !== v))}
          />
        </Section>

        {/* ── Fetch ── */}
        <Section title="📡 Fetch Settings">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-xs font-medium text-gray-600 block mb-1.5">Date window (days)</label>
              <input type="number" min={1} max={14} value={settings.date_days}
                onChange={(e) => patch("date_days", Number(e.target.value))}
                className="w-full text-sm border rounded-lg px-3 py-2 outline-none focus:border-blue-400" />
            </div>
            <div>
              <label className="text-xs font-medium text-gray-600 block mb-1.5">Max deep-read reports</label>
              <input type="number" min={1} max={5} value={settings.max_reports}
                onChange={(e) => patch("max_reports", Math.max(1, Math.min(5, Number(e.target.value) || 1)))}
                className="w-full text-sm border rounded-lg px-3 py-2 outline-none focus:border-blue-400" />
            </div>
            <div className="col-span-2">
              <label className="text-xs font-medium text-gray-600 block mb-1.5">Exclude keywords</label>
              <input type="text" value={settings.exclude_keywords}
                onChange={(e) => patch("exclude_keywords", e.target.value)}
                placeholder="e.g. protocol only, review"
                className="w-full text-sm border rounded-lg px-3 py-2 outline-none focus:border-blue-400" />
            </div>
            <div className="col-span-2 flex items-center gap-2">
              <input type="checkbox" id="strict" checked={settings.strict_journal}
                onChange={(e) => patch("strict_journal", e.target.checked)}
                className="rounded" />
              <label htmlFor="strict" className="text-sm text-gray-700 cursor-pointer select-none">
                Strict journal matching
              </label>
            </div>
            <div className="col-span-2 flex items-center gap-2">
              <input type="checkbox" id="download_pdf" checked={settings.download_pdf}
                onChange={(e) => patch("download_pdf", e.target.checked)}
                className="rounded" />
              <label htmlFor="download_pdf" className="text-sm text-gray-700 cursor-pointer select-none">
                Auto-download PDF for deep reports (default: on)
              </label>
            </div>
          </div>
        </Section>

        {/* ── Archive & Webhook ── */}
        <Section title="📦 Archive & Webhook">
          <div className="space-y-3">
            <div>
              <label className="text-xs font-medium text-gray-600 block mb-1.5">Archive DB path</label>
              <input type="text" value={settings.archive_db}
                onChange={(e) => patch("archive_db", e.target.value)}
                className="w-full text-sm border rounded-lg px-3 py-2 outline-none focus:border-blue-400 font-mono" />
              <p className="text-xs text-gray-400 mt-1">
                SQLite file path for storing all processed papers and reports. Leave blank to use the default <code className="bg-gray-100 px-1 rounded">paper_archive.db</code>.
              </p>
            </div>
            <div>
              <label className="text-xs font-medium text-gray-600 block mb-1.5">Webhook URL (optional)</label>
              <input type="password" value={settings.webhook_url}
                onChange={(e) => patch("webhook_url", e.target.value)}
                placeholder="https://hooks.slack.com/…"
                className="w-full text-sm border rounded-lg px-3 py-2 outline-none focus:border-blue-400" />
            </div>
            <div>
              <label className="text-xs font-medium text-gray-600 block mb-1.5">Auto-run Schedule</label>
              <div className="flex items-center gap-3">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" id="auto_schedule"
                    checked={settings.auto_schedule_enabled}
                    onChange={(e) => patch("auto_schedule_enabled", e.target.checked)}
                    className="w-4 h-4 accent-blue-500" />
                  <span className="text-sm text-gray-700">Run pipeline automatically every day at</span>
                </label>
                <input
                  type="time"
                  value={settings.auto_schedule_time}
                  onChange={(e) => patch("auto_schedule_time", e.target.value)}
                  disabled={!settings.auto_schedule_enabled}
                  className="text-sm border rounded-lg px-2 py-1.5 outline-none focus:border-blue-400 disabled:opacity-40"
                />
              </div>
              <p className="text-xs text-gray-400 mt-1">Uses your configured timezone. Skips if already run today.</p>
            </div>
          </div>
        </Section>
      </div>
    </div>
  );
}
