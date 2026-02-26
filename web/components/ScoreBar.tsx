interface Props {
  label: string;
  value: number;
}

export default function ScoreBar({ label, value }: Props) {
  const pct = Math.min(100, Math.max(0, Math.round(value)));
  const color = pct >= 70 ? "#4caf50" : pct >= 45 ? "#ff9800" : "#9e9e9e";
  return (
    <div className="flex items-center gap-2 min-w-36">
      <span className="text-xs text-gray-400 w-14 flex-shrink-0">{label}</span>
      <div className="flex-1 h-1.5 bg-gray-200 rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all"
          style={{ width: `${pct}%`, background: color }}
        />
      </div>
      <span className="text-xs text-gray-400 w-6 text-right">{pct}</span>
    </div>
  );
}
