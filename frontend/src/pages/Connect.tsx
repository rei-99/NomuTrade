import { useCallback, useEffect, useState } from "react";
import { toDataURL } from "qrcode";
import { api } from "../api/client";
import type { ConnectConfig } from "../api/types";
import { usePoll } from "../hooks";
import { useT } from "../i18n";

/**
 * Connect guide (demo convenience): a projector-friendly card with the WiFi
 * details, a QR code for the app URL and a host-editable message, so a demo
 * audience can join on the spot. The config is shared (single-row
 * demo_config) — any logged-in user may edit, and edits are visible to every
 * viewer of the page. The QR encodes the effective URL: url_override →
 * server-detected lan_url → window.location.origin. Generated offline in the
 * browser (qrcode), so the demo works on an isolated LAN. The config is
 * re-fetched every 15 s (paused while editing), so the QR tracks a network
 * change without a manual refresh.
 */
export function Connect() {
  const { t } = useT();
  const [cfg, setCfg] = useState<ConnectConfig | null>(null);
  const [editing, setEditing] = useState(false);
  const [ssid, setSsid] = useState("");
  const [password, setPassword] = useState("");
  const [urlOverride, setUrlOverride] = useState("");
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const [qr, setQr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setCfg(await api<ConnectConfig>("/connect-config"));
    } catch {
      // toast raised by client
    }
  }, []);

  // Re-fetch lightly so the QR tracks the actual network: the server
  // re-detects the LAN IP per request, so a WiFi change updates the QR within
  // ~15 s without a manual refresh. Paused while editing so an in-flight form
  // is never clobbered by a poll.
  usePoll(
    () => {
      if (!editing) void load();
    },
    15_000,
    [load, editing],
  );

  const effectiveUrl =
    (cfg?.url_override?.trim() || cfg?.lan_url || window.location.origin) ?? "";

  useEffect(() => {
    let cancelled = false;
    toDataURL(effectiveUrl, { margin: 1, width: 320 })
      .then((dataUrl) => {
        if (!cancelled) setQr(dataUrl);
      })
      .catch(() => {
        if (!cancelled) setQr(null);
      });
    return () => {
      cancelled = true;
    };
  }, [effectiveUrl]);

  const startEdit = () => {
    setSsid(cfg?.wifi_ssid ?? "");
    setPassword(cfg?.wifi_password ?? "");
    setUrlOverride(cfg?.url_override ?? "");
    setMessage(cfg?.message ?? "");
    setEditing(true);
  };

  const save = useCallback(async () => {
    setSaving(true);
    try {
      await api<ConnectConfig>("/connect-config", {
        method: "PUT",
        body: {
          wifi_ssid: ssid,
          wifi_password: password,
          message,
          url_override: urlOverride.trim() === "" ? null : urlOverride.trim(),
        },
      });
      setEditing(false);
      await load();
    } catch {
      // toast raised by client (validation / network)
    } finally {
      setSaving(false);
    }
  }, [ssid, password, message, urlOverride, load]);

  if (cfg === null) {
    return (
      <div className="page">
        <div className="muted">{t("common.loading")}</div>
      </div>
    );
  }

  return (
    <div className="page connect-page">
      <section className="panel connect-card">
        <div className="connect-card-header">
          <h2>{t("connect.title")}</h2>
          {!editing && (
            <button className="btn btn-ghost btn-sm" onClick={startEdit}>
              {t("connect.edit")}
            </button>
          )}
        </div>

        {editing ? (
          <div className="connect-form">
            <label>
              <span className="muted">{t("connect.wifi")}</span>
              <input
                value={ssid}
                onChange={(e) => setSsid(e.target.value)}
                placeholder={t("connect.ssidPh")}
                autoFocus
              />
            </label>
            <label>
              <span className="muted">{t("connect.password")}</span>
              <input
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={t("connect.passwordPh")}
              />
            </label>
            <label>
              <span className="muted">{t("connect.urlOverride")}</span>
              <input
                value={urlOverride}
                onChange={(e) => setUrlOverride(e.target.value)}
                placeholder={t("connect.urlPh")}
              />
              <span className="muted connect-hint">{t("connect.urlOverrideHint")}</span>
            </label>
            <label>
              <span className="muted">{t("connect.message")}</span>
              <textarea
                rows={4}
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                placeholder={t("connect.messagePh")}
              />
            </label>
            <div className="connect-actions">
              <button className="btn btn-ghost" disabled={saving} onClick={() => setEditing(false)}>
                {t("common.cancel")}
              </button>
              <button className="btn btn-primary" disabled={saving} onClick={() => void save()}>
                {t("common.save")}
              </button>
            </div>
          </div>
        ) : (
          <>
            <div className="connect-section">
              <div className="connect-label connect-label-lg">{t("connect.wifi")}</div>
              <div className="connect-wifi num">
                <span className="connect-ssid">{cfg.wifi_ssid || t("common.na")}</span>
                <span className="connect-password">
                  {t("connect.password")}: {cfg.wifi_password || t("common.na")}
                </span>
              </div>
            </div>

            <div className="connect-section connect-qr-section">
              <div className="connect-label">{t("connect.scan")}</div>
              {qr && <img className="connect-qr" src={qr} alt={effectiveUrl} />}
              <a className="connect-url num" href={effectiveUrl} target="_blank" rel="noreferrer">
                {effectiveUrl}
              </a>
            </div>

            {cfg.message.trim() !== "" && (
              <div className="connect-section">
                <div className="connect-label">{t("connect.message")}</div>
                <div className="connect-message">{cfg.message}</div>
              </div>
            )}

            <div className="connect-note muted">{t("connect.sharedNote")}</div>
          </>
        )}
      </section>
    </div>
  );
}
