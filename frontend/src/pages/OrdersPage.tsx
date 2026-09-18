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
  expires_at: string;
  renew_count: number;
  renewals_remaining: number;
};

const STATUS_LABELS: Record<string, string> = {
  held: "持有中",
  released: "已释放",
  cancelled: "已取消",
};

const FILTERS: Array<[string, string]> = [
  ["", "全部"],
  ["held", "持有中"],
  ["released", "已释放"],
  ["cancelled", "已取消"],
];

function fmtTime(iso: string) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("zh-CN", { hour12: false });
}

export default function OrdersPage() {
  const [rows, setRows] = useState<Hold[]>([]);
  const [status, setStatus] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  const load = useCallback(() => {
    const q = status ? `?status=${status}` : "";
    api<Hold[]>(`/holds${q}`).then(setRows);
  }, [status]);

  useEffect(() => {
    load();
  }, [load]);

  async function act(h: Hold, action: "renew" | "cancel") {
    setMsg("");
    setErr("");
    try {
      const updated = await api<Hold>(`/holds/${h.id}/${action}`, { method: "POST" });
      setMsg(
        action === "renew"
          ? `已续期 ${updated.order_code}：到期 ${fmtTime(updated.expires_at)}，剩余续期 ${updated.renewals_remaining} 次`
          : `已取消 ${updated.order_code}`
      );
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <>
      <h2>订单</h2>
      <div className="toolbar">
        <label>
          状态{" "}
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            {FILTERS.map(([v, label]) => (
              <option key={v} value={v}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <button onClick={load}>刷新</button>
      </div>
      {msg && <div className="ok">{msg}</div>}
      {err && <div className="err">{err}</div>}
      <table className="table">
        <thead>
          <tr>
            <th>订单号</th>
            <th>场次</th>
            <th>座位</th>
            <th>人数</th>
            <th>状态</th>
            <th>到期时间</th>
            <th>剩余续期</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((h) => (
            <tr key={h.id}>
              <td className="mono">{h.order_code}</td>
              <td>{h.showtime_id}</td>
              <td className="mono">
                R{h.row} C{h.start_col}-{h.end_col}
              </td>
              <td>{h.party_size}</td>
              <td>{STATUS_LABELS[h.status] ?? h.status}</td>
              <td className="mono">{fmtTime(h.expires_at)}</td>
              <td>{h.renewals_remaining} 次</td>
              <td>
                {h.status === "held" && (
                  <>
                    <button disabled={h.renewals_remaining <= 0} onClick={() => act(h, "renew")}>
                      续期
                    </button>{" "}
                    <button onClick={() => act(h, "cancel")}>取消</button>
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
