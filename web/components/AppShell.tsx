"use client";
import { useState, useEffect, useMemo } from "react";
import { usePathname, useRouter } from "next/navigation";
import {
  api, loadLocalSettings,
  type RunMeta, type ReportDate, type NoteMeta,
} from "@/lib/api";
import {
  Settings, Network, Play, ChevronLeft, ChevronRight,
  Loader2, FileText, BookOpen, StickyNote, Plus, ChevronDown, ChevronUp,
  Trash2, Folder, FolderOpen, FolderPlus, Search,
} from "lucide-react";
import { useI18n } from "@/lib/i18n";

function SectionHeader({
  icon, label, open, onToggle,
}: { icon: React.ReactNode; label: string; open: boolean; onToggle: () => void }) {
  return (
    <button
      onClick={onToggle}
      className="w-full flex items-center gap-1.5 px-3 py-2 text-left rounded-lg mx-1 hover:bg-[#e8e4db]"
      style={{ color: "#8f887e" }}
    >
      <span className="opacity-90">{icon}</span>
      <span className="flex-1 text-[10px] font-semibold uppercase tracking-widest">{label}</span>
      {open ? <ChevronUp size={10} /> : <ChevronDown size={10} />}
    </button>
  );
}

// ── Folder-aware notes section ─────────────────────────────────────────────

function NotesFolderTree({
  notes,
  activeNoteSlug,
  onNavigate,
  onRefresh,
  emptyHint,
  unfiledLabel,
}: {
  notes: NoteMeta[];
  activeNoteSlug: string | null;
  onNavigate: (slug: string) => void;
  onRefresh: () => void;
  emptyHint: string;
  unfiledLabel: string;
}) {
  // Track open folders: Set of folder names
  const [openFolders, setOpenFolders] = useState<Set<string>>(new Set([""]));
  // Inline rename state for folders
  const [renamingFolder, setRenamingFolder] = useState<string | null>(null);
  const [renameVal, setRenameVal] = useState("");

  // Group notes by folder
  const grouped = useMemo(() => {
    const map = new Map<string, NoteMeta[]>();
    for (const n of notes) {
      const f = n.folder || "";
      if (!map.has(f)) map.set(f, []);
      map.get(f)!.push(n);
    }
    return map;
  }, [notes]);

  // Folders: named ones first (alphabetical), then "" (unfiled)
  const folders = useMemo(() => {
    const named = [...grouped.keys()].filter(f => f !== "").sort();
    const hasUnfiled = grouped.has("");
    return hasUnfiled ? [...named, ""] : named;
  }, [grouped]);

  function toggleFolder(f: string) {
    setOpenFolders(prev => {
      const next = new Set(prev);
      if (next.has(f)) next.delete(f);
      else next.add(f);
      return next;
    });
  }

  async function handleRenameFolder(oldName: string, newName: string) {
    if (!newName.trim() || newName === oldName) { setRenamingFolder(null); return; }
    await api.renameFolder(oldName, newName.trim()).catch(() => {});
    setOpenFolders(prev => {
      const next = new Set(prev);
      if (next.has(oldName)) { next.delete(oldName); next.add(newName.trim()); }
      return next;
    });
    onRefresh();
    setRenamingFolder(null);
  }

  async function handleMoveToFolder(slug: string, folder: string) {
    await api.patchNoteMeta(slug, folder).catch(() => {});
    onRefresh();
  }

  const NoteRow = ({ n, indent = false }: { n: NoteMeta; indent?: boolean }) => (
    <div
      className="flex items-center group mx-1 rounded transition-colors"
      style={{ background: activeNoteSlug === n.slug ? "#e4e0d9" : "transparent" }}
    >
      <button
        onClick={() => onNavigate(n.slug)}
        className="flex items-center gap-2 flex-1 text-left py-1.5 truncate hover:bg-[#eeebe4] rounded"
        style={{ paddingLeft: indent ? "2.25rem" : "0.75rem" }}
      >
        <span className="w-1.5 h-1.5 rounded-full bg-blue-400 flex-shrink-0" />
        <span className="flex-1 min-w-0">
          <span className="block text-xs truncate">{n.name || n.slug}</span>
          <span className="block text-[10px] text-gray-400 truncate">
            {new Date(n.modified * 1000).toISOString().slice(0, 10)}
          </span>
        </span>
      </button>
      {/* Drag-to-folder: right-click to assign folder */}
      <div className="relative hidden group-hover:flex mr-1">
        <select
          value={n.folder || ""}
          onChange={e => handleMoveToFolder(n.slug, e.target.value)}
          className="text-[10px] text-gray-500 bg-transparent border-0 outline-none cursor-pointer max-w-[78px] truncate"
          title="Move to folder"
          onClick={e => e.stopPropagation()}
        >
          <option value="">— move —</option>
          {folders.filter(f => f !== "").map(f => (
            <option key={f} value={f}>{f}</option>
          ))}
          <option value={n.folder || ""} disabled>No folder</option>
        </select>
      </div>
    </div>
  );

  if (notes.length === 0) {
    return <p className="px-3 py-1.5 text-xs text-gray-400">{emptyHint}</p>;
  }

  // If no folders exist, flat list
  if (folders.length === 1 && folders[0] === "") {
    return (
      <div className="mt-0.5">
        {notes.map(n => <NoteRow key={n.slug} n={n} />)}
      </div>
    );
  }

  return (
    <div className="mt-0.5">
      {folders.map(f => {
        const folderNotes = grouped.get(f) || [];
        const isOpen = openFolders.has(f);
        const isUnfiled = f === "";
        return (
          <div key={f === "" ? "__unfiled__" : f}>
            {/* Folder header */}
            <div
              className="flex items-center gap-1 px-2 py-1 rounded mx-1 group/folder cursor-pointer hover:bg-[#eeebe4]"
              style={{ color: "#8b8680" }}
              onClick={() => toggleFolder(f)}
            >
              {isOpen
                ? <FolderOpen size={11} className="flex-shrink-0" />
                : <Folder size={11} className="flex-shrink-0" />}
              {renamingFolder === f ? (
                <input
                  autoFocus
                  value={renameVal}
                  onChange={e => setRenameVal(e.target.value)}
                  onBlur={() => handleRenameFolder(f, renameVal)}
                  onKeyDown={e => {
                    e.stopPropagation();
                    if (e.key === "Enter") handleRenameFolder(f, renameVal);
                    if (e.key === "Escape") setRenamingFolder(null);
                  }}
                  onClick={e => e.stopPropagation()}
                  className="flex-1 text-[11px] bg-white border border-blue-300 rounded px-1 outline-none"
                />
              ) : (
                <span
                  className="flex-1 text-[11px] font-medium truncate"
                  onDoubleClick={e => {
                    if (isUnfiled) return;
                    e.stopPropagation();
                    setRenamingFolder(f);
                    setRenameVal(f);
                  }}
                >
                  {isUnfiled ? unfiledLabel : f}
                </span>
              )}
              <span className="text-[10px] text-gray-400 mr-1">{folderNotes.length}</span>
              {isOpen ? <ChevronUp size={9} /> : <ChevronDown size={9} />}
            </div>
            {/* Notes inside folder */}
            {isOpen && (
              <div>
                {folderNotes.map(n => <NoteRow key={n.slug} n={n} indent />)}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}


// ── AppShell ───────────────────────────────────────────────────────────────

export default function AppShell({ children }: { children: React.ReactNode }) {
  const { t } = useI18n();
  const [runs, setRuns] = useState<RunMeta[]>([]);
  const [reportDates, setReportDates] = useState<ReportDate[]>([]);
  const [notes, setNotes] = useState<NoteMeta[]>([]);
  const [running, setRunning] = useState(false);
  const [runLog, setRunLog] = useState<string[]>([]);
  const pathname = usePathname();
  const router = useRouter();

  // Section collapse state
  const [runsOpen, setRunsOpen] = useState(true);
  const [digestOpen, setDigestOpen] = useState(true);
  const [notesOpen, setNotesOpen] = useState(true);
  const [notesQuery, setNotesQuery] = useState("");
  const [sidebarWidth, setSidebarWidth] = useState(300);

  // Auto-collapse sidebar on individual report/note pages (they have own sidebar)
  const onReportPage = pathname.startsWith("/reports/") && pathname.split("/").length >= 4;
  const [manualCollapse, setManualCollapse] = useState<boolean | null>(null);
  const collapsed = manualCollapse !== null ? manualCollapse : onReportPage;
  const clampedSidebarWidth = Math.max(220, Math.min(460, sidebarWidth));

  useEffect(() => {
    if (typeof window === "undefined") return;
    queueMicrotask(() => {
      const saved = Number(window.localStorage.getItem("app.sidebar.width"));
      if (Number.isFinite(saved)) {
        setSidebarWidth(Math.max(220, Math.min(460, saved)));
      }
    });
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem("app.sidebar.width", String(clampedSidebarWidth));
  }, [clampedSidebarWidth]);

  // Initial data fetch
  useEffect(() => {
    api.listRuns().then(setRuns).catch(() => {});
    api.listReportDates().then(setReportDates).catch(() => {});
    api.listNotes().then(setNotes).catch(() => {});
  }, []);

  // Refresh notes list when returning from a notes page
  useEffect(() => {
    if (!pathname.startsWith("/notes/")) {
      api.listNotes().then(setNotes).catch(() => {});
    }
  }, [pathname]);

  // Active date detection
  const activeRunDate =
    pathname.startsWith("/runs/") ? pathname.split("/runs/")[1] : null;
  const activeReportDate =
    pathname.startsWith("/reports/") ? pathname.split("/reports/")[1].split("/")[0] : null;
  const activeNoteSlug =
    pathname.startsWith("/notes/") ? pathname.split("/notes/")[1] : null;

  const filteredNotes = useMemo(() => {
    const q = notesQuery.trim().toLowerCase();
    if (!q) return notes;
    return notes.filter((n) => {
      const hay = `${n.name} ${n.slug} ${n.folder} ${(n.tags || []).join(" ")}`.toLowerCase();
      return hay.includes(q);
    });
  }, [notes, notesQuery]);

  async function handleRun(force = false) {
    if (running) return;
    setRunning(true);
    setRunLog(["🚀 Starting pipeline…"]);
    try {
      const settingsData = loadLocalSettings();
      const res = await fetch("/api/pipeline/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ settings: settingsData, force }),
      });
      const data = await res.json();
      if (!data.started) {
        if (data.reason === "beta_daily_limit") {
          setRunLog([`⚠️ Beta daily limit reached: pipeline can run once per day (${data.date}).`]);
          setRunning(false);
          return;
        }
        if (data.reason === "already_run_today") {
          setRunning(false);
          setRunLog([]);
          const ok = window.confirm(
            t("pipeline_already_run", { date: data.date })
          );
          if (ok) {
            handleRun(true); // re-run with force=true
          }
          return;
        }
        setRunLog(["⚠️ Pipeline is already running in the background."]);
        // Still start polling so UI reflects current state
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Unknown error";
      setRunLog([`❌ Failed to start: ${msg}`]);
      setRunning(false);
      return;
    }

    // Poll /api/pipeline/status every 3 seconds
    // Pipeline runs fully in background — user can navigate freely
    const poll = setInterval(async () => {
      try {
        const st = await fetch("/api/pipeline/status").then(r => r.json());
        setRunLog(st.logs ?? []);
        if (st.status === "done" || st.status === "error") {
          clearInterval(poll);
          setRunning(false);
          if (st.status === "done") {
            const [newRuns, newReports] = await Promise.all([
              api.listRuns(),
              api.listReportDates(),
            ]);
            setRuns(newRuns);
            setReportDates(newReports);
            if (st.date) router.push(`/runs/${st.date}`);
          }
        }
      } catch {}
    }, 3000);
  }

  async function handleNewNote() {
    const ts = Date.now().toString(36);
    const slug = `note_${ts}`;
    await api.saveNote(slug, `# New Note\n\n`, "New Note");
    const newNotes = await api.listNotes();
    setNotes(newNotes);
    router.push(`/notes/${slug}`);
  }

  const refreshNotes = () => api.listNotes().then(setNotes).catch(() => {});

  const sidebarBg = "var(--sidebar-bg)";
  const sidebarBorder = "var(--sidebar-border)";

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar */}
      <nav
        className="flex flex-col border-r transition-all duration-200 overflow-hidden flex-shrink-0 backdrop-blur-sm"
        style={{
          width: collapsed ? 0 : clampedSidebarWidth,
          minWidth: collapsed ? 0 : clampedSidebarWidth,
          background: sidebarBg,
          borderColor: sidebarBorder,
          boxShadow: "0 0 0 1px rgba(255,255,255,0.35) inset, 0 8px 28px rgba(28,24,18,0.07)",
        }}
      >
        {/* Run button */}
        <div className="px-3 pt-3 pb-2">
          <button
            onClick={() => handleRun()}
            disabled={running}
            className="w-full flex items-center justify-center gap-2 text-white text-xs font-semibold py-2.5 px-3 rounded-xl disabled:opacity-50"
            style={{
              background: "linear-gradient(135deg, #2d2b28 0%, #47433d 100%)",
              boxShadow: "0 8px 18px rgba(21,18,14,0.22), 0 1px 0 rgba(255,255,255,0.16) inset",
            }}
          >
            {running ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
            {running ? t("running") : t("run_pipeline")}
          </button>
          {runLog.length > 0 && (
            <div className="mt-2 p-2.5 rounded-xl text-xs text-gray-600 max-h-40 overflow-y-auto leading-5 border"
              style={{ background: "#fbfaf7", borderColor: "#e3dfd4", boxShadow: "inset 0 1px 0 rgba(255,255,255,0.5)" }}>
              {runLog.map((l, i) => (
                <div key={i} className={l.startsWith("⚠️") ? "text-amber-600" : l.startsWith("✅") ? "text-green-600" : ""}>
                  {l}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="border-t" style={{ borderColor: sidebarBorder }} />

        {/* Scrollable content */}
        <div className="flex-1 overflow-y-auto pb-4">

          {/* ── Section 1: Papers ── */}
          <div className="mt-2">
            <SectionHeader
              icon={<BookOpen size={11} />}
              label={t("papers")}
              open={runsOpen}
              onToggle={() => setRunsOpen((v) => !v)}
            />
            {runsOpen && (
              <div className="mt-0.5">
                {runs.map((r) => (
                  <div
                    key={r.run_date}
                    className="flex items-center gap-0 mx-1 rounded-lg group"
                    style={{
                      background: activeRunDate === r.run_date ? "#e6e2d8" : "transparent",
                    }}
                  >
                    <button
                      onClick={() => router.push(`/runs/${r.run_date}`)}
                      className="flex items-center gap-2 flex-1 px-3 py-2 text-sm text-left"
                      style={{ fontWeight: activeRunDate === r.run_date ? 600 : 400 }}
                    >
                      <span className="w-1.5 h-1.5 rounded-full bg-green-400 flex-shrink-0" />
                      <span className="flex-1 font-mono text-xs">{r.run_date}</span>
                      <span className="text-xs text-gray-400 bg-gray-200 px-1.5 py-0.5 rounded-full">
                        {r.total_count}
                      </span>
                    </button>
                    <button
                      onClick={async (e) => {
                        e.stopPropagation();
                        if (!confirm(`Delete run ${r.run_date}?`)) return;
                        await api.deleteRun(r.run_date);
                        setRuns((prev) => prev.filter((x) => x.run_date !== r.run_date));
                        if (activeRunDate === r.run_date) router.push("/");
                      }}
                      className="p-1 mr-1 opacity-0 group-hover:opacity-100 hover:text-red-500 transition-all rounded"
                      style={{ color: "#b0aba5" }}
                      title="Delete run"
                    >
                      <Trash2 size={11} />
                    </button>
                  </div>
                ))}
                {runs.length === 0 && (
                  <p className="px-3 py-1.5 text-xs text-gray-400">{t("no_runs")}</p>
                )}
              </div>
            )}
          </div>

          <div className="border-t mx-3 my-2" style={{ borderColor: sidebarBorder }} />

          {/* ── Section 2: Daily Digest ── */}
          <div>
            <SectionHeader
              icon={<FileText size={11} />}
              label={t("daily_digest")}
              open={digestOpen}
              onToggle={() => setDigestOpen((v) => !v)}
            />
            {digestOpen && (
              <div className="mt-0.5">
                {reportDates.map((r) => (
                  <div
                    key={r.date}
                    className="flex items-center group mx-1 rounded-lg"
                    style={{ background: activeReportDate === r.date ? "#e6e2d8" : "transparent" }}
                  >
                    <button
                      onClick={() => router.push(`/reports/${r.date}`)}
                      className="flex items-center gap-2 flex-1 px-2 py-2 text-left"
                      style={{ fontWeight: activeReportDate === r.date ? 600 : 400 }}
                    >
                      <span className="w-1.5 h-1.5 rounded-full bg-amber-400 flex-shrink-0 ml-1" />
                      <span className="flex-1 font-mono text-xs">{r.date}</span>
                      <span className="text-xs text-gray-400 bg-gray-200 px-1.5 py-0.5 rounded-full">
                        {r.files}
                      </span>
                    </button>
                    <button
                      onClick={async (e) => {
                        e.stopPropagation();
                        if (!confirm(`Delete all reports for ${r.date}?`)) return;
                        await api.deleteReportDate(r.date);
                        setReportDates((prev) => prev.filter((x) => x.date !== r.date));
                        if (activeReportDate === r.date) router.push("/");
                      }}
                      className="p-1 mr-1 opacity-0 group-hover:opacity-100 hover:text-red-500 transition-all rounded"
                      style={{ color: "#b0aba5" }}
                      title="Delete reports"
                    >
                      <Trash2 size={11} />
                    </button>
                  </div>
                ))}
                {reportDates.length === 0 && (
                  <p className="px-3 py-1.5 text-xs text-gray-400">{t("no_digests")}</p>
                )}
              </div>
            )}
          </div>

          <div className="border-t mx-3 my-2" style={{ borderColor: sidebarBorder }} />

          {/* ── Section 3: Notes ── */}
          <div>
            <div className="flex items-center">
              <div className="flex-1">
                <SectionHeader
                  icon={<StickyNote size={11} />}
                  label={t("notes")}
                  open={notesOpen}
                  onToggle={() => setNotesOpen((v) => !v)}
                />
              </div>
              <button
                onClick={handleNewNote}
                className="p-1 rounded-md hover:bg-[#e5e1d8] transition-colors"
                title={t("new_note")}
                style={{ color: "#a09b95" }}
              >
                <Plus size={12} />
              </button>
              <button
                onClick={async () => {
                  const name = prompt(`${t("new_folder")} name:`);
                  if (name?.trim()) {
                    alert(t("folder_prepared", { name: name.trim() }));
                  }
                }}
                className="p-1 mr-2 rounded-md hover:bg-[#e5e1d8] transition-colors"
                title={t("new_folder")}
                style={{ color: "#a09b95" }}
              >
                <FolderPlus size={12} />
              </button>
            </div>
            {notesOpen && (
              <>
                <div className="px-2 mt-1">
                  <label className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border bg-[#fbfaf7]"
                    style={{ borderColor: "#ddd8cd" }}>
                    <Search size={11} className="text-gray-400" />
                    <input
                      value={notesQuery}
                      onChange={(e) => setNotesQuery(e.target.value)}
                      placeholder={t("search_notes")}
                      className="w-full text-xs bg-transparent outline-none"
                    />
                    <span className="text-[10px] text-gray-400">{filteredNotes.length}</span>
                  </label>
                </div>
                <NotesFolderTree
                  notes={filteredNotes}
                  activeNoteSlug={activeNoteSlug}
                  onNavigate={(slug) => router.push(`/notes/${slug}`)}
                  onRefresh={refreshNotes}
                  emptyHint={t("no_notes_hint")}
                  unfiledLabel={t("unfiled")}
                />
              </>
            )}
          </div>

        </div>

        {/* Bottom nav */}
        <div className="border-t" style={{ borderColor: sidebarBorder }} />
        <div className="p-2 flex flex-col gap-1">
          {[
            { href: "/settings", icon: <Settings size={14} />, label: t("settings") },
            { href: "/network", icon: <Network size={14} />, label: t("paper_network") },
            { href: "/search", icon: <Search size={14} />, label: t("report_search") },
          ].map(({ href, icon, label }) => (
            <button
              key={href}
              onClick={() => router.push(href)}
              className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors hover:bg-[#e5e1d8]"
              style={{
                background: pathname === href ? "#e6e2d8" : "transparent",
                fontWeight: pathname === href ? 600 : 400,
              }}
            >
              {icon}
              {label}
            </button>
          ))}
        </div>
      </nav>

      {!collapsed && (
        <div
          className="w-1.5 cursor-col-resize transition-colors"
          style={{ background: "transparent" }}
          onMouseDown={(e) => {
            e.preventDefault();
            const startX = e.clientX;
            const startWidth = clampedSidebarWidth;
            const onMove = (ev: MouseEvent) => {
              const next = startWidth + (ev.clientX - startX);
              setSidebarWidth(Math.max(220, Math.min(460, next)));
            };
            const onUp = () => {
              window.removeEventListener("mousemove", onMove);
              window.removeEventListener("mouseup", onUp);
            };
            window.addEventListener("mousemove", onMove);
            window.addEventListener("mouseup", onUp);
          }}
          title="Drag to resize sidebar"
          onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.background = "#d4cec1"; }}
          onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.background = "transparent"; }}
        />
      )}

      {/* Toggle button */}
      <button
        onClick={() => setManualCollapse((c) => !(c !== null ? c : collapsed))}
        className="absolute top-1/2 -translate-y-1/2 z-20 w-6 h-11 flex items-center justify-center border rounded-r-xl shadow-sm transition-all"
        style={{
          left: collapsed ? 0 : clampedSidebarWidth,
          background: "#fbfaf7",
          borderColor: "#ded9cf",
          color: "#8a847b",
        }}
      >
        {collapsed ? <ChevronRight size={12} /> : <ChevronLeft size={12} />}
      </button>

      {/* Main */}
      <main className="flex-1 overflow-y-auto">
        {children}
      </main>
    </div>
  );
}
