import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { ListResponse, NotificationItem, NotificationPreferences } from "../api/types";
import { Badge } from "../components/Badge";
import { fmtTs } from "../format";
import { useT } from "../i18n";

/** Security-critical categories the server refuses to disable (FR-NTF-003 E1). */
const NON_SUPPRESSIBLE = ["BREAK_GLASS", "GRANT", "PAM"];
const CHANNELS = ["IN_APP", "EMAIL"] as const;

export function Notifications() {
  const { t } = useT();
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [prefs, setPrefs] = useState<NotificationPreferences | null>(null);

  const load = useCallback(async (c: string | null, append: boolean) => {
    setLoading(true);
    try {
      const res = await api<ListResponse<NotificationItem>>("/notifications", {
        params: { limit: 50, cursor: c ?? undefined },
      });
      setItems((prev) => (append ? [...prev, ...res.items] : res.items));
      setCursor(res.next_cursor);
    } catch {
      // toast raised by client
    } finally {
      setLoading(false);
    }
  }, []);

  const loadPrefs = useCallback(async () => {
    try {
      const res = await api<NotificationPreferences>("/notification-preferences");
      setPrefs(res);
    } catch {
      // toast raised by client
    }
  }, []);

  useEffect(() => {
    void load(null, false);
    void loadPrefs();
  }, [load, loadPrefs]);

  const markRead = async (n: NotificationItem) => {
    if (n.status === "READ") return;
    try {
      await api(`/notifications/${n.notification_id}/read`, { method: "POST" });
      setItems((xs) =>
        xs.map((x) => (x.notification_id === n.notification_id ? { ...x, status: "READ" } : x)),
      );
    } catch {
      // toast raised by client
    }
  };

  // Category toggles: every category seen in the list plus the locked ones.
  const categories = useMemo(() => {
    const cats = new Set(items.map((n) => n.category));
    for (const c of NON_SUPPRESSIBLE) cats.add(c);
    return [...cats].sort();
  }, [items]);

  // Optimistic save: flip local state, PATCH, revert on failure (client toasts).
  const patchPrefs = async (
    next: NotificationPreferences,
    body: { channels?: Record<string, boolean>; categories?: Record<string, boolean> },
  ) => {
    const prev = prefs;
    setPrefs(next);
    try {
      const res = await api<NotificationPreferences>("/notification-preferences", {
        method: "PATCH",
        body,
      });
      setPrefs(res);
    } catch {
      setPrefs(prev);
    }
  };

  const toggleChannel = (ch: (typeof CHANNELS)[number]) => {
    if (!prefs) return;
    void patchPrefs(
      { ...prefs, channels: { ...prefs.channels, [ch]: !prefs.channels[ch] } },
      { channels: { [ch]: !prefs.channels[ch] } },
    );
  };

  const toggleCategory = (cat: string) => {
    if (!prefs || NON_SUPPRESSIBLE.includes(cat)) return;
    const enabled = prefs.categories[cat] ?? true;
    void patchPrefs(
      { ...prefs, categories: { ...prefs.categories, [cat]: !enabled } },
      { categories: { [cat]: !enabled } },
    );
  };

  return (
    <div className="page">
      <div className="page-header">
        <h2>{t("notif.title")}</h2>
      </div>

      <section className="panel">
        <div className="panel-header">
          <h3>{t("notif.inbox")}</h3>
        </div>
        {items.length === 0 ? (
          <div className="panel-empty muted">{t("notif.empty")}</div>
        ) : (
          <div className="notif-list">
            {items.map((n) => (
              <div
                key={n.notification_id}
                className={`bell-item notif-row${n.status !== "READ" ? " bell-item-unread" : ""}`}
              >
                <div className="bell-item-top">
                  <span className="bell-item-title">{n.payload.title}</span>
                  <span className="notif-row-side">
                    <Badge text={n.category} />
                    {n.status !== "READ" && (
                      <button className="btn btn-ghost btn-sm" onClick={() => void markRead(n)}>
                        {t("notif.markRead")}
                      </button>
                    )}
                  </span>
                </div>
                <div className="bell-item-body">{n.payload.body}</div>
                <div className="bell-item-ts muted num">{fmtTs(n.created_at)}</div>
              </div>
            ))}
          </div>
        )}
        {cursor && (
          <div className="table-footer">
            <button
              className="btn btn-ghost btn-sm"
              disabled={loading}
              onClick={() => void load(cursor, true)}
            >
              {loading ? t("common.loading") : t("common.loadMore")}
            </button>
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-header">
          <h3>{t("notif.preferences")}</h3>
        </div>
        {!prefs ? (
          <div className="panel-empty muted">Loading preferences…</div>
        ) : (
          <>
            <div className="pref-group">
              <span className="pref-label muted">Channels</span>
              <div className="pref-toggles">
                {CHANNELS.map((ch) => (
                  <label key={ch} className={`chip${prefs.channels[ch] ? " chip-on" : ""}`}>
                    <input
                      type="checkbox"
                      checked={prefs.channels[ch]}
                      onChange={() => toggleChannel(ch)}
                    />
                    {ch.replace(/_/g, " ")}
                  </label>
                ))}
              </div>
            </div>
            <div className="pref-group">
              <span className="pref-label muted">Categories</span>
              <div className="pref-toggles">
                {categories.map((cat) => {
                  const locked = NON_SUPPRESSIBLE.includes(cat);
                  const enabled = locked || (prefs.categories[cat] ?? true);
                  return locked ? (
                    <span
                      key={cat}
                      className="chip chip-on chip-static"
                      title="Security-critical — cannot be disabled"
                    >
                      {cat.replace(/_/g, " ")}
                    </span>
                  ) : (
                    <label key={cat} className={`chip${enabled ? " chip-on" : ""}`}>
                      <input
                        type="checkbox"
                        checked={enabled}
                        onChange={() => toggleCategory(cat)}
                      />
                      {cat.replace(/_/g, " ")}
                    </label>
                  );
                })}
              </div>
              <p className="muted">
                Break glass, grant and PAM alerts are security-critical and cannot be disabled.
                Preferences are stored in memory on the server and reset on restart.
              </p>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
