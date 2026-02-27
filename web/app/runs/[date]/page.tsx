"use client";
import { use, useEffect, useState, useCallback } from "react";
import { api, loadLocalSettings, type RunData, type PaperCard as PaperCardData } from "@/lib/api";
import PaperCardComponent from "@/components/PaperCard";
import { ExternalLink, FileText, Sparkles, Maximize2, Minimize2 } from "lucide-react";

function safeSlug(text: string, maxLen = 60): string {
  return text.toLowerCase()
    .replace(/[^\w\s-]/g, "")
    .replace(/[\s_-]+/g, "_")
    .replace(/^_|_$/g, "")
    .slice(0, maxLen) || "paper";
}

// ── Also-notable row with "Summarize" button ──────────────────────────────
function NotableRow({
  card,
  index,
  date,
  onPromoted,
}: {
  card: PaperCardData;
  index: number;
  date: string;
  onPromoted: (card: PaperCardData) => void;
}) {
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);
  const [err, setErr] = useState("");
  const notableText = (() => {
    const ai = (card.ai_feed_summary || "").trim();
    const value = (card.value_assessment || "").trim();
    const abs = (card.source_abstract || "").replace(/\s+/g, " ").trim();
    // If fallback summary is clipped, use a longer abstract preview for readability.
    if (ai && ai.length < 220 && abs.length > ai.length + 80) {
      return abs.length > 420 ? `${abs.slice(0, 420)}...` : abs;
    }
    return ai || value;
  })();

  const handleSummarize = async () => {
    setLoading(true);
    setErr("");
    try {
      const settings = loadLocalSettings();
      const res = await api.summarizePaper(date, card, settings);
      if (res.ok) {
        setDone(true);
        onPromoted(res.card as PaperCardData);
      }
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Failed");
    } finally {
      setLoading(false);
    }
  };

  if (done) return null; // removed from list after promotion

  return (
    <div className="flex gap-3 py-2.5 px-2 border-b last:border-0 rounded-lg hover:bg-[#f4f1ea]" style={{ borderColor: "#ebe6db" }}>
      <span className="text-xs text-[#8f887f] w-5 flex-shrink-0 pt-0.5">{index}</span>
      <div className="flex-1 min-w-0">
        <div className="flex items-start justify-between gap-2">
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-[#2e2b27] leading-snug">{card.title}</p>
            <p className="text-xs text-[#8f887f] mt-0.5">{card.venue} · {card.date}</p>
            {notableText && (
              <p className="text-xs text-[#565048] mt-1 leading-relaxed">
                {notableText}
              </p>
            )}
            {err && <p className="text-xs text-red-500 mt-1">{err}</p>}
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            {card.link && (
              <a href={card.link} target="_blank" rel="noopener noreferrer"
                className="text-xs text-[#225ea8] hover:underline flex items-center gap-1">
                <ExternalLink size={11} /> Read
              </a>
            )}
            <button
              onClick={handleSummarize}
              disabled={loading}
              className="flex items-center gap-1 text-xs bg-amber-50 text-amber-700 border border-amber-200 px-2.5 py-1.5 rounded-md hover:bg-amber-100 disabled:opacity-50 transition-colors whitespace-nowrap"
              title="Generate an AI deep-read summary and add it to today's report"
            >
              {loading
                ? <span className="animate-pulse">Generating…</span>
                : <><Sparkles size={11} /> Summarize</>}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Main page ──────────────────────────────────────────────────────────────
export default function RunPage({ params }: { params: Promise<{ date: string }> }) {
  const { date } = use(params);
  const [run, setRun] = useState<RunData | null>(null);
  const [loading, setLoading] = useState(true);
  const [showSlack, setShowSlack] = useState(false);
  const [error, setError] = useState("");
  // extra report cards promoted from also-notable
  const [extraCards, setExtraCards] = useState<PaperCardData[]>([]);
  // also-notable cards (mutable: remove promoted)
  const [notable, setNotable] = useState<PaperCardData[]>([]);
  const [wide, setWide] = useState(true);

  useEffect(() => {
    queueMicrotask(() => {
      setLoading(true);
      setError("");
      setExtraCards([]);
    });
    Promise.all([
      api.getRun(date),
      api.listReportAssets(date).catch(() => ({ files: [] as { name: string; size: number }[] })),
    ])
      .then(([r, assets]) => {
        const pdfBySlug = new Map<string, string>();
        for (const f of assets.files || []) {
          const slug = f.name.replace(/\.pdf$/, "");
          pdfBySlug.set(slug, `/api/reports/${date}/assets/${f.name}`);
        }
        const reportCards = (r.report_cards || []).map((c) => {
          const slug = safeSlug(c.title || "");
          return {
            ...c,
            downloaded_pdf_url: pdfBySlug.get(slug) || c.downloaded_pdf_url,
          };
        });
        setRun({ ...r, report_cards: reportCards });
        setNotable(r.also_notable);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [date]);

  // Background: attempt to cache local PDFs for deep-read cards missing a local copy.
  useEffect(() => {
    const settings = loadLocalSettings();
    if (!settings.download_pdf) return;
    if (!run?.report_cards?.length) return;
    const targets = run.report_cards.filter((c) => !c.downloaded_pdf_url && (c.link || c.paper_id));
    if (!targets.length) return;
    let cancelled = false;
    (async () => {
      for (const c of targets) {
        try {
          const res = await api.cachePaperPdf(date, c);
          if (cancelled) return;
          if (res.ok && res.downloaded_pdf_url) {
            setRun((prev) => {
              if (!prev) return prev;
              return {
                ...prev,
                report_cards: (prev.report_cards || []).map((x) =>
                  x.paper_id === c.paper_id ? { ...x, downloaded_pdf_url: res.downloaded_pdf_url } : x
                ),
              };
            });
          }
        } catch {
          // best-effort only
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [date, run?.report_cards]);

  const handlePromoted = useCallback((card: PaperCardData) => {
    setExtraCards((prev) => [...prev, card]);
    setNotable((prev) => prev.filter((c) => c.paper_id !== card.paper_id));
  }, []);

  if (loading) return (
    <div className="flex items-center justify-center h-64 text-[#8e887f] text-sm">Loading {date}…</div>
  );
  if (error) return (
    <div className="p-8 text-red-500 text-sm">Error: {error}</div>
  );
  if (!run) return null;

  const { report_cards, slack_text, total_count } = run;
  const allReportCards = [...report_cards, ...extraCards];

  return (
    <div className={`${wide ? "max-w-5xl" : "max-w-3xl"} mx-auto px-8 py-8 transition-all`}>
      {/* Header */}
      <div className="mb-6 flex items-start justify-between gap-4 rounded-2xl border px-5 py-4"
        style={{ borderColor: "#e1ddcf", background: "rgba(252,251,248,0.86)", boxShadow: "0 8px 24px rgba(28,24,18,0.06)" }}>
        <div>
          <h1 className="text-2xl font-bold text-[#1f1d1a] mb-2">{date}</h1>
          <div className="flex flex-wrap gap-4 text-sm text-[#7e776e]">
            <span>📄 <strong className="text-[#302d29]">{total_count}</strong> papers today</span>
            <span>📑 <strong className="text-[#302d29]">{allReportCards.length}</strong> deep reads</span>
            <span>📋 <strong className="text-[#302d29]">{notable.length}</strong> also notable</span>
          </div>
        </div>
        <button
          onClick={() => setWide(w => !w)}
          title={wide ? "Narrow view" : "Wide view"}
          className="flex-shrink-0 p-2 rounded-lg border transition-colors"
          style={{ borderColor: "#dcd6c8", color: "#8d877f", background: "#f7f5ef" }}
        >
          {wide ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
        </button>
      </div>

      {/* Deep reads */}
      {allReportCards.length > 0 && (
        <section className="mb-7">
          <h2 className="text-base font-semibold text-[#4f4a43] mb-3 pb-2 border-b flex items-center gap-2"
            style={{ borderColor: "var(--card-border)" }}>
            📑 Deep Read
          </h2>
          {allReportCards.map((card, i) => (
            <PaperCardComponent
              key={card.paper_id}
              card={card}
              index={i + 1}
              defaultOpen={i === 0}
              date={date}
            />
          ))}
        </section>
      )}

      {/* Also notable */}
      {notable.length > 0 && (
        <section className="mb-7 rounded-2xl border px-4 py-3"
          style={{ borderColor: "#e1ddcf", background: "rgba(252,251,248,0.86)", boxShadow: "0 8px 20px rgba(28,24,18,0.05)" }}>
          <h2 className="text-base font-semibold text-[#4f4a43] mb-3 pb-2 border-b flex items-center gap-2"
            style={{ borderColor: "var(--card-border)" }}>
            📋 Also Notable
            <span className="text-xs font-normal text-[#938d84] ml-1">
              — click Summarize to add a paper to deep reads
            </span>
          </h2>
          <div className="space-y-0">
            {notable.map((c, i) => (
              <NotableRow
                key={c.paper_id}
                card={c}
                index={allReportCards.length + i + 1}
                date={date}
                onPromoted={handlePromoted}
              />
            ))}
          </div>
        </section>
      )}

      {/* Slack text */}
      {slack_text && (
        <div className="mt-4 rounded-xl border px-4 py-3"
          style={{ borderColor: "#e1ddcf", background: "rgba(252,251,248,0.86)" }}>
          <button
            onClick={() => setShowSlack((s) => !s)}
            className="flex items-center gap-2 text-xs text-[#8f887f] hover:text-[#4f4a43] transition-colors"
          >
            <FileText size={13} />
            {showSlack ? "Hide" : "Show"} digest text
          </button>
          {showSlack && (
            <pre className="mt-2 p-4 rounded-lg text-xs text-[#4a443d] whitespace-pre-wrap leading-relaxed overflow-x-auto border"
              style={{ background: "#f5f2ea", borderColor: "#dfd9cc" }}>
              {slack_text}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
