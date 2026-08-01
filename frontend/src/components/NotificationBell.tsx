import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { AppNotification, ListResponse } from "../api/types";
import { usePoll, useWsMessage } from "../hooks";
import { fmtTs } from "../format";
import { useT } from "../i18n";
import { Badge } from "./Badge";

// Structural fallback; push `notification` messages (design 22) trigger an
// immediate reload below.
const POLL_MS = 30_000;

export function NotificationBell() {
  const { t } = useT();
  const [items, setItems] = useState<AppNotification[]>([]);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      const res = await api<ListResponse<AppNotification>>("/notifications", {
        skipErrorToast: true,
      });
      setItems(res.items);
    } catch {
      // notifications are best-effort; don't spam toasts
    }
  }, []);

  usePoll(load, POLL_MS, [load]);

  // Push hint: a notification for this user just landed — refresh now.
  useWsMessage("notification", () => void load(), [load]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [open]);

  const unread = items.filter((n) => n.status !== "READ").length;

  const markRead = async (n: AppNotification) => {
    if (n.status === "READ") return;
    try {
      await api(`/notifications/${n.notification_id}/read`, {
        method: "POST",
        skipErrorToast: true,
      });
      setItems((xs) =>
        xs.map((x) => (x.notification_id === n.notification_id ? { ...x, status: "READ" } : x)),
      );
    } catch {
      // ignore
    }
  };

  return (
    <div className="bell" ref={rootRef}>
      <button className="btn btn-ghost bell-btn" onClick={() => setOpen((o) => !o)} aria-label={t("notif.title")}>
        🔔
        {unread > 0 && <span className="bell-count num">{unread}</span>}
      </button>
      {open && (
        <div className="bell-dropdown panel">
          <div className="bell-header">{t("notif.title")}</div>
          {items.length === 0 ? (
            <div className="bell-empty muted">{t("notif.empty")}</div>
          ) : (
            items.slice(0, 20).map((n) => (
              <button
                key={n.notification_id}
                className={`bell-item${n.status !== "READ" ? " bell-item-unread" : ""}`}
                onClick={() => void markRead(n)}
              >
                <div className="bell-item-top">
                  <span className="bell-item-title">{n.payload.title}</span>
                  <Badge text={n.category} />
                </div>
                <div className="bell-item-body">{n.payload.body}</div>
                <div className="bell-item-ts muted num">{fmtTs(n.created_at)}</div>
              </button>
            ))
          )}
          <Link to="/notifications" className="bell-viewall" onClick={() => setOpen(false)}>
            {t("notif.viewAll")}
          </Link>
        </div>
      )}
    </div>
  );
}
