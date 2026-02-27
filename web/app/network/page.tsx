"use client";
import { useEffect, useRef, useState } from "react";
import { api, type NetworkData, type NetworkNode } from "@/lib/api";
import * as d3 from "d3";

const PALETTE = [
  "#2563eb", "#16a34a", "#dc2626", "#d97706", "#7c3aed",
  "#db2777", "#0891b2", "#65a30d", "#9f1239", "#1e40af",
];

function colorFor(group: string, groups: string[]) {
  const idx = groups.indexOf(group);
  return PALETTE[idx % PALETTE.length];
}

function edgeNodeId(v: string | NetworkNode): string {
  return typeof v === "string" ? v : v.id;
}

export default function NetworkPage() {
  const svgRef = useRef<SVGSVGElement>(null);
  const [data, setData] = useState<NetworkData | null>(null);
  const [loading, setLoading] = useState(true);
  const [hovered, setHovered] = useState<NetworkNode | null>(null);
  const [threshold, setThreshold] = useState(0.25);
  const [limit, setLimit] = useState(150);

  useEffect(() => {
    api.getNetwork(limit, threshold)
      .then(setData)
      .finally(() => setLoading(false));
  }, [limit, threshold]);

  useEffect(() => {
    if (!data || !svgRef.current) return;

    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();

    const W = svgRef.current.clientWidth || 800;
    const H = svgRef.current.clientHeight || 600;

    const groups = [...new Set(data.nodes.map((n) => n.group))];

    // D3 force simulation
    const simulation = d3.forceSimulation(data.nodes as d3.SimulationNodeDatum[])
      .force("link", d3.forceLink(data.edges)
        .id((d: d3.SimulationNodeDatum) => (d as NetworkNode).id)
        .distance((d) => 100 + 80 * (1 - (d as { similarity: number }).similarity))
        .strength(0.5))
      .force("charge", d3.forceManyBody().strength(-200))
      .force("center", d3.forceCenter(W / 2, H / 2))
      .force("collision", d3.forceCollide(22));

    // Container for pan/zoom
    const g = svg.append("g");

    svg.call(d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.2, 4])
      .on("zoom", (event) => g.attr("transform", event.transform)));

    // Edges
    const link = g.append("g").attr("class", "links")
      .selectAll("line")
      .data(data.edges)
      .join("line")
      .attr("stroke", "#94a3b8")
      .attr("stroke-opacity", (d) => 0.3 + d.similarity * 0.6)
      .attr("stroke-width", (d) => 0.8 + d.similarity * 3);

    // Node group
    const node = g.append("g").attr("class", "nodes")
      .selectAll<SVGGElement, NetworkNode>("g")
      .data(data.nodes as NetworkNode[])
      .join("g")
      .attr("cursor", "pointer")
      .call(
        d3.drag<SVGGElement, NetworkNode>()
          .on("start", (event, d) => {
            if (!event.active) simulation.alphaTarget(0.3).restart();
            (d as d3.SimulationNodeDatum).fx = (d as d3.SimulationNodeDatum).x;
            (d as d3.SimulationNodeDatum).fy = (d as d3.SimulationNodeDatum).y;
          })
          .on("drag", (event, d) => {
            (d as d3.SimulationNodeDatum).fx = event.x;
            (d as d3.SimulationNodeDatum).fy = event.y;
          })
          .on("end", (event, d) => {
            if (!event.active) simulation.alphaTarget(0);
            (d as d3.SimulationNodeDatum).fx = null;
            (d as d3.SimulationNodeDatum).fy = null;
          })
      );

    // Outer glow ring
    node.append("circle")
      .attr("r", 14)
      .attr("fill", (d) => colorFor(d.group, groups))
      .attr("fill-opacity", 0.15)
      .attr("stroke", "none");

    // Inner dot
    node.append("circle")
      .attr("r", 7)
      .attr("fill", (d) => colorFor(d.group, groups))
      .attr("stroke", "#fff")
      .attr("stroke-width", 2);

    // Short title label (truncated)
    node.append("text")
      .attr("dx", 10)
      .attr("dy", "0.35em")
      .attr("font-size", "9px")
      .attr("fill", "#6b7280")
      .attr("pointer-events", "none")
      .text((d) => d.title.length > 30 ? d.title.slice(0, 30) + "…" : d.title);

    // Hover events — highlight connected edges
    node
      .on("mouseenter", (_event, d) => {
        setHovered(d);
        link
          .attr("stroke-opacity", (e) =>
            edgeNodeId(e.source as string | NetworkNode) === d.id
            || edgeNodeId(e.target as string | NetworkNode) === d.id
              ? 0.9
              : 0.08)
          .attr("stroke", (e) =>
            edgeNodeId(e.source as string | NetworkNode) === d.id
            || edgeNodeId(e.target as string | NetworkNode) === d.id
              ? colorFor(d.group, groups)
              : "#94a3b8");
      })
      .on("mouseleave", () => {
        setHovered(null);
        link
          .attr("stroke-opacity", (d) => 0.3 + d.similarity * 0.6)
          .attr("stroke", "#94a3b8");
      })
      .on("click", (_event, d) => { if (d.link) window.open(d.link, "_blank"); });

    // Tick
    simulation.on("tick", () => {
      link
        .attr("x1", (d) => ((d.source as d3.SimulationNodeDatum).x ?? 0))
        .attr("y1", (d) => ((d.source as d3.SimulationNodeDatum).y ?? 0))
        .attr("x2", (d) => ((d.target as d3.SimulationNodeDatum).x ?? 0))
        .attr("y2", (d) => ((d.target as d3.SimulationNodeDatum).y ?? 0));

      node.attr("transform", (d) =>
        `translate(${(d as d3.SimulationNodeDatum).x ?? 0},${(d as d3.SimulationNodeDatum).y ?? 0})`);
    });

    return () => { simulation.stop(); };
  }, [data]);

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center gap-4 px-4 py-3 border-b text-sm"
        style={{ borderColor: "var(--card-border)", background: "#fafaf9" }}>
        <span className="font-semibold text-gray-700">🌐 Paper Network</span>
        <div className="flex items-center gap-2 text-xs text-gray-500">
          <label>Papers:</label>
          <input type="range" min={20} max={300} step={10} value={limit}
            onChange={(e) => {
              setLoading(true);
              setLimit(Number(e.target.value));
            }}
            className="w-24" />
          <span className="w-8 text-center">{limit}</span>
        </div>
        <div className="flex items-center gap-2 text-xs text-gray-500">
          <label>Min similarity:</label>
          <input type="range" min={0.1} max={0.6} step={0.05} value={threshold}
            onChange={(e) => {
              setLoading(true);
              setThreshold(Number(e.target.value));
            }}
            className="w-24" />
          <span className="w-8 text-center">{threshold.toFixed(2)}</span>
        </div>
        {data && (
          <span className="text-xs text-gray-400 ml-auto">
            {data.nodes.length} nodes · {data.edges.length} edges
          </span>
        )}
        {loading && <span className="text-xs text-gray-400">Loading…</span>}
      </div>

      {/* Canvas */}
      <div className="relative flex-1">
        <svg ref={svgRef} className="w-full h-full" style={{ background: "#fafaf9" }} />

        {/* Tooltip */}
        {hovered && (
          <div className="absolute top-4 right-4 max-w-sm bg-white border rounded-lg shadow-lg p-3 pointer-events-none"
            style={{ borderColor: "var(--card-border)" }}>
            <p className="text-sm font-semibold text-gray-900 mb-1 leading-snug">{hovered.title}</p>
            <p className="text-xs text-gray-500">{hovered.venue} · {hovered.date}</p>
            {hovered.link && (
              <p className="text-xs text-blue-400 mt-1 truncate">{hovered.link}</p>
            )}
            <p className="text-[10px] text-gray-400 mt-1.5">Click to open paper ↗</p>
          </div>
        )}

        {/* Empty state */}
        {!loading && data?.nodes.length === 0 && (
          <div className="absolute inset-0 flex flex-col items-center justify-center text-gray-400">
            <div className="text-4xl mb-3">🕸️</div>
            <p className="text-sm">No papers in archive yet.</p>
            <p className="text-xs mt-1">Run the pipeline first to populate the network.</p>
          </div>
        )}
      </div>
    </div>
  );
}
