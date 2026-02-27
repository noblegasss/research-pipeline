"use client";
import { useEffect, useMemo, useState } from "react";
import { loadLocalSettings } from "@/lib/api";

export type Language = "en" | "zh";

const dict: Record<Language, Record<string, string>> = {
  en: {
    settings: "Settings",
    paper_network: "Paper Network",
    report_search: "Report Search",
    papers: "Papers",
    daily_digest: "Daily Digest",
    notes: "Notes",
    run_pipeline: "Run Pipeline",
    running: "Running…",
    no_runs: "No runs yet",
    no_digests: "No digests yet",
    no_notes_hint: "Click + to create a note",
    unfiled: "Unfiled",
    search_notes: "Search notes...",
    new_note: "New note",
    new_folder: "New folder",
    folder_prepared: "Folder \"{name}\" prepared. Assign any note to this folder from the note row selector.",
    pipeline_already_run: "Pipeline already ran today ({date}) and generated summaries.\n\nRe-running will overwrite all generated summary files. Continue?",
    language: "Language",
    english: "English",
    chinese: "Chinese",
    loading: "Loading…",
  },
  zh: {
    settings: "设置",
    paper_network: "论文网络",
    report_search: "报告检索",
    papers: "论文",
    daily_digest: "每日摘要",
    notes: "笔记",
    run_pipeline: "运行流水线",
    running: "运行中…",
    no_runs: "还没有运行记录",
    no_digests: "还没有摘要",
    no_notes_hint: "点击 + 创建笔记",
    unfiled: "未归档",
    search_notes: "搜索笔记...",
    new_note: "新建笔记",
    new_folder: "新建文件夹",
    folder_prepared: "文件夹“{name}”已创建，请在笔记行右侧分配到该文件夹。",
    pipeline_already_run: "今天（{date}）已经运行过 pipeline 并生成摘要。\n\n重新运行会覆盖已生成摘要。确认继续？",
    language: "语言",
    english: "英文",
    chinese: "中文",
    loading: "加载中…",
  },
};

function getLanguage(): Language {
  if (typeof window === "undefined") return "en";
  const s = loadLocalSettings();
  return s.language === "zh" ? "zh" : "en";
}

export function useI18n() {
  // Keep first render deterministic for SSR/CSR hydration.
  const [lang, setLang] = useState<Language>("en");

  useEffect(() => {
    queueMicrotask(() => setLang(getLanguage()));
    const update = () => setLang(getLanguage());
    const onStorage = (e: StorageEvent) => {
      if (!e.key || e.key === "research_pipeline_settings_v1") update();
    };
    const onCustom = () => update();
    window.addEventListener("storage", onStorage);
    window.addEventListener("app-language-change", onCustom as EventListener);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener("app-language-change", onCustom as EventListener);
    };
  }, []);

  const t = useMemo(() => {
    return (key: string, vars?: Record<string, string | number>) => {
      let out = dict[lang][key] ?? dict.en[key] ?? key;
      if (vars) {
        for (const [k, v] of Object.entries(vars)) {
          out = out.replaceAll(`{${k}}`, String(v));
        }
      }
      return out;
    };
  }, [lang]);

  return { lang, t };
}
