"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";
import { ChevronDown, ChevronUp, ExternalLink, FileText } from "lucide-react";
import ScoreBar from "./ScoreBar";
import type { PaperCard as PaperCardType } from "@/lib/api";

interface Props {
  card: PaperCardType;
  index: number;
  defaultOpen?: boolean;
  date?: string;
}

function safeMarkdownUrlTransform(url: string): string {
  if (/^data:image\/[\w.+-]+;base64,/i.test(url)) return url;
  return defaultUrlTransform(url);
}

function Section({ icon, title, content }: { icon: string; title: string; content: string }) {
  if (!content) return null;
  return (
    <div className="mb-3">
      <h4 className="text-[11px] font-semibold text-[#8e887f] uppercase tracking-wide mb-1.5">
        {icon} {title}
      </h4>
      <div className="prose prose-sm text-[#3a3631]">
        <ReactMarkdown remarkPlugins={[remarkGfm]} urlTransform={safeMarkdownUrlTransform}>
          {content}
        </ReactMarkdown>
      </div>
    </div>
  );
}

function _safeSlug(text: string, maxLen = 60): string {
  return text.toLowerCase()
    .replace(/[^\w\s-]/g, "")
    .replace(/[\s_-]+/g, "_")
    .replace(/^_|_$/g, "")
    .slice(0, maxLen) || "paper";
}

function _pdfLink(card: PaperCardType): string {
  if (card.downloaded_pdf_url) return card.downloaded_pdf_url;
  const link = (card.link || "").trim();
  const pid = (card.paper_id || "").trim();
  if (link.toLowerCase().endsWith(".pdf")) return link;
  if (pid.startsWith("arxiv:")) return `https://arxiv.org/pdf/${pid.slice(6)}.pdf`;
  if ((/biorxiv\.org\/content\//i.test(link) || /medrxiv\.org\/content\//i.test(link))) {
    const base = link.split("?", 1)[0].replace(/\/+$/, "");
    return `${base}.full.pdf`;
  }
  return "";
}

export default function PaperCard({ card, index, defaultOpen = false, date }: Props) {
  const router = useRouter();
  const [open, setOpen] = useState(defaultOpen);
  const [showAbstract, setShowAbstract] = useState(false);

  const rpt = card.report ?? {};
  const methods = rpt.methods_detailed || card.methods_in_one_line || "";
  const conclusion = rpt.main_conclusion || card.main_conclusion || "";
  const future = rpt.future_direction || card.future_direction || "";
  const value = rpt.value_assessment || card.value_assessment || "";
  const aiSum = rpt.ai_feed_summary || card.ai_feed_summary || "";
  const scores = card.scores ?? {};
  const link = card.link;
  const pdfLink = _pdfLink(card);

  const hasReport = Boolean(methods || conclusion || aiSum);
  const mdSlug = date ? _safeSlug(card.title) : null;

  return (
    <article className="border rounded-xl mb-3 overflow-hidden transition-all hover:-translate-y-[1px]"
      style={{ borderColor: "var(--card-border)", background: "#fcfbf8", boxShadow: "0 6px 18px rgba(23,19,14,0.06)" }}>
      {/* Header */}
      <header
        className="flex items-start gap-3 p-4 cursor-pointer select-none"
        style={{ background: "#f4f2eb", borderBottom: open ? "1px solid var(--card-border)" : "none" }}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0 mt-0.5"
          style={{ background: "#ebe7dc", color: "#7e776d" }}>
          {index}
        </span>
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-semibold text-[#201e1b] leading-snug mb-1.5">{card.title}</h3>
          <div className="flex flex-wrap items-center gap-2">
            {card.venue && (
              <span className="text-xs px-2 py-0.5 rounded-full font-medium"
                style={{ background: "var(--accent-soft)", color: "var(--accent)" }}>
                {card.venue}
              </span>
            )}
            {Array.from(new Set(card.tags || [])).slice(0, 4).map((tag) => (
              <span
                key={tag}
                className="text-xs px-2 py-0.5 rounded-full border"
                style={{ background: "#eef2fb", color: "#3b5ea6", borderColor: "#c7d4f0" }}
              >
                #{tag}
              </span>
            ))}
            {card.date && <span className="text-xs text-[#8e887f]">📅 {card.date}</span>}
            {typeof scores.total === "number" && (
              <span
                className="w-2 h-2 rounded-full"
                style={{ background: scores.total >= 70 ? "#4caf50" : scores.total >= 45 ? "#ff9800" : "#9e9e9e" }}
                title={`Score: ${scores.total}`}
              />
            )}
          </div>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0" onClick={(e) => e.stopPropagation()}>
          {/* MD report button */}
          {date && mdSlug && hasReport && (
            <button
              onClick={() => router.push(`/reports/${date}/${mdSlug}`)}
              className="flex items-center gap-1 text-xs px-2.5 py-1.5 rounded-md transition-colors border"
              style={{ background: "#edf8f1", color: "#1e7a4c", borderColor: "#cdebd9" }}
              title="View / edit Markdown report"
            >
              <FileText size={11} />
              Deep Report
            </button>
          )}
          {pdfLink && (
            <a
              href={pdfLink}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1 text-xs px-2.5 py-1.5 rounded-md transition-colors border"
              style={{ background: "#f5f7ff", color: "#2b4ea2", borderColor: "#ced8f3" }}
              title="Open PDF"
            >
              <FileText size={11} />
              PDF
            </a>
          )}
          {link && (
            <a
              href={link}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1 text-xs text-white px-3 py-1.5 rounded-md transition-colors"
              style={{ background: "linear-gradient(135deg, #2d2b28 0%, #4a453f 100%)" }}
            >
              <ExternalLink size={11} />
              Read
            </a>
          )}
          {open ? <ChevronUp size={15} className="text-[#918b82]" /> : <ChevronDown size={15} className="text-[#918b82]" />}
        </div>
      </header>

      {open && (
        <div className="p-4 bg-[#fcfbf8]">
          {/* Scores */}
          {Object.keys(scores).length > 0 && (
            <div className="flex flex-wrap gap-3 mb-4 pb-3 border-b" style={{ borderColor: "var(--card-border)" }}>
              {[
                { key: "relevance", label: "Relevance" },
                { key: "novelty", label: "Novelty" },
                { key: "rigor", label: "Rigor" },
                { key: "impact", label: "Impact" },
              ].map(({ key, label }) =>
                typeof (scores as Record<string, number>)[key] === "number" ? (
                  <ScoreBar key={key} label={label} value={(scores as Record<string, number>)[key]} />
                ) : null
              )}
            </div>
          )}

          {/* AI Summary banner */}
          {aiSum && (
            <div className="mb-4 p-3 bg-amber-50 border-l-2 border-amber-400 rounded-r-md text-sm text-[#3d3731] leading-relaxed">
              <span className="font-semibold text-amber-700 text-xs uppercase tracking-wide block mb-1">AI Summary</span>
              {aiSum}
            </div>
          )}

          {/* Two-column report */}
          <div className="grid grid-cols-2 gap-x-6 gap-y-0">
            <div>
              <Section icon="🔬" title="Methods" content={methods} />
              <Section icon="💡" title="Key Conclusion" content={conclusion} />
            </div>
            <div>
              <Section icon="🚀" title="Future Directions" content={future} />
              <Section icon="⭐" title="Research Value" content={value} />
            </div>
          </div>

          {/* Abstract toggle */}
          {card.source_abstract && (
            <div className="mt-2 border rounded-lg overflow-hidden" style={{ borderColor: "var(--card-border)" }}>
              <button
                onClick={() => setShowAbstract((s) => !s)}
                className="w-full text-left text-xs text-[#7f786f] px-3 py-2 bg-[#f3f1ea] hover:bg-[#ebe7dd] transition-colors flex items-center justify-between"
              >
                <span>Abstract</span>
                {showAbstract ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
              </button>
              {showAbstract && (
                <p className="px-3 py-2 text-sm text-[#4a443d] leading-relaxed bg-[#fbfaf7]">
                  {card.source_abstract}
                </p>
              )}
            </div>
          )}

          {/* Similar papers */}
          {card.similar && card.similar.length > 0 && (
            <div className="mt-3 pt-3 border-t" style={{ borderColor: "var(--card-border)" }}>
              <h4 className="text-xs font-semibold text-[#8e887f] uppercase tracking-wide mb-2">
                📎 Related Past Papers
              </h4>
              <ul className="space-y-1.5">
                {card.similar.map((s) => {
                  const sLink = s.paper_id.startsWith("doi:")
                    ? `https://doi.org/${s.paper_id.slice(4)}`
                    : s.paper_id.startsWith("arxiv:")
                    ? `https://arxiv.org/abs/${s.paper_id.slice(6)}`
                    : s.paper_id.startsWith("pmid:")
                    ? `https://pubmed.ncbi.nlm.nih.gov/${s.paper_id.slice(5)}/`
                    : "";
                  return (
                    <li key={s.paper_id} className="text-sm">
                      {sLink ? (
                        <a href={sLink} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline">
                          {s.title}
                        </a>
                      ) : (
                        <span className="text-gray-700">{s.title}</span>
                      )}
                      <span className="text-xs text-gray-400 ml-1">
                        ({s.venue}, {s.date} · Similarity {Math.round(s.score * 100)}%)
                      </span>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
        </div>
      )}
    </article>
  );
}
