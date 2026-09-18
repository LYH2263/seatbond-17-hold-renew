import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";

type Hold = {
  id: number;
  showtime_id: number;
  order_code: string;
  row: number;
  start_col: number;
  end_col: number;
  party_size: number;
  status: string;
  created_at: string;
  expires_at: string | null;
  renewals_used: number;
  renewals_left: number;
};

const STATUS_LABEL: Record<string, string> = {
  held: "持有中",
  released: "已释放",
  cancelled: "已取消",
};

const FILTERS = [
  ["", "全部"],
  ["held", "持有中"],
  ["released", "已释放"],
  ["cancelled", "已取消"],
] as const;

function pad(n: number) {
  return String(n).padStart(2, "0");
}

function formatExpire(iso: string | null) {
  if (!iso) return "—";
  const d = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function remainText(iso: string | null, status: string, tick: number) {
  if (status !== "held" || !iso) return "";
  const d = new Date(iso.endsWith("Z") ? iso : `${iso}Z`).getTime();
  const secs = Math.max(0, Math.round((d - Date.now()) / 1000));
  void tick;
  if (secs <= 0) return "已到点";
  return `剩 ${Math.floor(secs / 60)}:${pad(secs % 60)}`;
}

export default function OrdersPage() {
  const [rows, setRows] = useState<Hold[]>([]);
  const [filter, setFilter] = useState<string>("");
  const [busyId, setBusyId] = useState<number | null>(null);
  const [err, setErr] = useState("");
  // 每秒 +1 驱动倒计时重渲染
  const [tick, setTick] = useState(0);

  const load = useCallback(() => {
    const qs = filter ? `?status=${filter}` : "";
    api<Hold[]>(`/holds${qs}`).then(setRows).catch(() => {});
  }, [filter]);

  useEffect(() => {
    load();
    const t = setInterval(load, 6000);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, []);

  async function act(h: Hold, kind: "renew" | "cancel") {
    setBusyId(h.id);
    setErr("");
    try {
      await api<Hold>(`/holds/${h.id}/${kind}`, { method: "POST" });
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <>
      <h2>持座票根</h2>
      <div className="toolbar">
        {FILTERS.map(([value, label]) => (
          <button
            key={value || "all"}
            onClick={() => setFilter(value)}
            style={
              filter === value
                ? undefined
                : { background: "#2a1a22", color: "var(--text)", border: "1px solid #4a3040" }
            }
          >
            {label}
          </button>
        ))}
      </div>
      {err && <div className="err">{err}</div>}
      <table className="table">
        <thead>
          <tr>
            <th>订单号</th>
            <th>场次</th>
            <th>座位</th>
            <th>人数</th>
            <th>状态</th>
            <th>到期时刻</th>
            <th>续期额度</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((h) => {
            const held = h.status === "held";
            return (
              <tr key={h.id}>
                <td className="mono">{h.order_code}</td>
                <td>{h.showtime_id}</td>
                <td className="mono">
                  R{h.row} C{h.start_col}-{h.end_col}
                </td>
                <td>{h.party_size}</td>
                <td>{STATUS_LABEL[h.status] ?? h.status}</td>
                <td className="mono">
                  {formatExpire(h.expires_at)}
                  {held && (
                    <span className="stub-meta" style={{ marginLeft: 8, color: "var(--cinema-gold)" }}>
                      {remainText(h.expires_at, h.status, tick)}
                    </span>
                  )}
                </td>
                <td className="mono">
                  剩 {h.renewals_left} / {h.renewals_left + h.renewals_used} 次
                </td>
                <td>
                  {held ? (
                    <span style={{ display: "flex", gap: 6 }}>
                      <button
                        disabled={busyId === h.id || h.renewals_left <= 0}
                        title={h.renewals_left <= 0 ? "续期次数已用完" : "延长一个持座窗口"}
                        onClick={() => act(h, "renew")}
                      >
                        续期
                      </button>
                      <button
                        disabled={busyId === h.id}
                        style={{ background: "#5a2a30", color: "var(--text)" }}
                        onClick={() => act(h, "cancel")}
                      >
                        取消
                      </button>
                    </span>
                  ) : (
                    <span className="stub-meta">—</span>
                  )}
                </td>
              </tr>
            );
          })}
          {rows.length === 0 && (
            <tr>
              <td colSpan={8} className="stub-empty">
                暂无记录
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </>
  );
}
