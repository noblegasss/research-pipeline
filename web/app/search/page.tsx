"use client";
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { api, type ReportDate, type ReportFileMeta } from "@/lib/api";

type SearchItem = {
  date: string;
  name: string;
  slug: string;
  title: string;
  tags: string[];
};

/** Normalize a tag: lowercase, collapse separators, strip punctuation */
function normalizeTag(t: string): string {
  return t.toLowerCase()
    .replace(/[-_/\\|]+/g, " ")   // separators → space
    .replace(/[^a-z0-9 ]/g, "")   // strip other punctuation
    .replace(/\s+/g, " ")
    .trim();
}

/** Canonical display label: title-case words */
function displayTag(norm: string): string {
  return norm.replace(/\b\w/g, (c) => c.toUpperCase());
}

export default function SearchPage() {
  const router = useRouter();
  const [items, setItems] = useState<SearchItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [activeTags, setActiveTags] = useState<Set<string>>(new Set());

  useEffect(() => {
    queueMicrotask(() => setLoading(true));
    api.listReportDates()
      .then(async (dates: ReportDate[]) => {
        const perDate = await Promise.all(
          dates.map(async (d) => {
            try {
              const r = await api.listReports(d.date);
              return { date: d.date, files: r.files || [] };
            } catch {
              return { date: d.date, files: [] as ReportFileMeta[] };
            }
          })
        );
        const out: SearchItem[] = [];
        for (const row of perDate) {
          for (const f of row.files) {
            if (!f.name.endsWith(".md")) continue;
            if (f.name === "digest.md" || f.name.endsWith("_note.md")) continue;
            const slug = f.name.replace(/\.md$/, "");
            out.push({
              date: row.date,
              name: f.name,
              slug,
              title: (f.title || slug).trim(),
              tags: f.tags || [],
            });
          }
        }
        setItems(out);
      })
      .finally(() => setLoading(false));
  }, []);

  // Deduplicated normalized tags, sorted by frequency then alpha
  const { allTags, normMap } = useMemo(() => {
    // normMap: normalized → count
    const counts = new Map<string, number>();
    for (const item of items) {
      const seen = new Set<string>();
      for (const t of item.tags) {
        const n = normalizeTag(t);
        if (!n || seen.has(n)) continue;
        seen.add(n);
        counts.set(n, (counts.get(n) ?? 0) + 1);
      }
    }
    const sorted = [...counts.entries()]
      .filter(([, c]) => c >= 1)
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .map(([n]) => n);
    return { allTags: sorted, normMap: counts };
  }, [items]);

  // For each item, precompute its normalized tag set
  const itemsWithNorm = useMemo(
    () => items.map((i) => ({
      ...i,
      normTags: new Set(i.tags.map(normalizeTag).filter(Boolean)),
    })),
    [items]
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return itemsWithNorm.filter((i) => {
      const text = `${i.title} ${i.date} ${[...i.normTags].join(" ")}`.toLowerCase();
      const qOk = !q || text.includes(q);
      // OR logic: item must match at least one selected tag
      const tagOk = activeTags.size === 0 || [...activeTags].some((t) => i.normTags.has(t));
      return qOk && tagOk;
    });
  }, [itemsWithNorm, query, activeTags]);

  return (
    <div className="max-w-5xl mx-auto px-8 py-8">
      <div className="mb-5 rounded-2xl border px-5 py-4"
        style={{ borderColor: "#e1ddcf", background: "rgba(252,251,248,0.86)", boxShadow: "0 8px 24px rgba(28,24,18,0.06)" }}>
        <h1 className="text-2xl font-bold text-[#1f1d1a] mb-3">Report Search</h1>
        <div className="flex flex-col gap-2">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search deep reports by title, date, or tags..."
            className="w-full text-sm border rounded-lg px-3 py-2 outline-none focus:border-blue-400"
            style={{ borderColor: "#dcd6c8", background: "#fff" }}
          />
          <div className="flex flex-wrap gap-1.5">
            <button
              onClick={() => setActiveTags(new Set())}
              className={`text-xs px-2 py-1 rounded-full border transition-colors ${activeTags.size === 0 ? "bg-[#2f2c28] text-white border-[#2f2c28]" : "bg-white text-[#5f5a54] border-[#d9d4c9]"}`}
            >
              All
            </button>
            {allTags.map((norm) => (
              <button
                key={norm}
                onClick={() => setActiveTags((prev) => {
                  const next = new Set(prev);
                  next.has(norm) ? next.delete(norm) : next.add(norm);
                  return next;
                })}
                className={`text-xs px-2 py-1 rounded-full border transition-colors ${activeTags.has(norm) ? "bg-blue-600 text-white border-blue-600" : "bg-white text-[#5f5a54] border-[#d9d4c9]"}`}
                title={`${normMap.get(norm)} paper${(normMap.get(norm) ?? 0) > 1 ? "s" : ""}`}
              >
                #{displayTag(norm)}
              </button>
            ))}
          </div>
          {activeTags.size > 0 && (
            <p className="text-xs text-[#8f887f]">
              Showing papers matching <strong>any</strong> of {activeTags.size} selected tag{activeTags.size > 1 ? "s" : ""} · {filtered.length} result{filtered.length !== 1 ? "s" : ""}
            </p>
          )}
        </div>
      </div>

      {loading ? (
        <p className="text-sm text-gray-500">Loading reports…</p>
      ) : filtered.length === 0 ? (
        <p className="text-sm text-gray-500">No matching deep reports.</p>
      ) : (
        <div className="space-y-2">
          {filtered.map((r) => (
            <button
              key={`${r.date}/${r.name}`}
              onClick={() => router.push(`/reports/${r.date}/${r.slug}`)}
              className="w-full text-left rounded-xl border px-4 py-3 hover:bg-[#f7f4ee] transition-colors"
              style={{ borderColor: "#e1ddcf", background: "rgba(252,251,248,0.86)" }}
            >
              <p className="text-sm font-semibold text-[#2e2b27]">{r.title}</p>
              <p className="text-xs text-[#8f887f] mt-1">{r.date}</p>
              {r.normTags.size > 0 && (
                <div className="flex flex-wrap gap-1 mt-2">
                  {[...r.normTags].slice(0, 8).map((norm) => (
                    <span
                      key={norm}
                      className={`text-[11px] px-2 py-0.5 rounded-full border transition-colors ${activeTags.has(norm) ? "bg-blue-100 border-blue-300 text-blue-700" : ""}`}
                      style={activeTags.has(norm) ? {} : { background: "#eef2fb", color: "#3b5ea6", borderColor: "#c7d4f0" }}
                    >
                      #{displayTag(norm)}
                    </span>
                  ))}
                </div>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
