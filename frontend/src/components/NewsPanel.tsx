import { useCallback, useState } from "react";
import { api, ApiError } from "../api/client";
import type {
  NewsItem,
  NewsListResponse,
  NewsSummary,
  SentimentLabel,
} from "../api/types";
import { fmtNum, fmtTs } from "../format";
import { usePoll } from "../hooks";
import { useT } from "../i18n";
import { Badge } from "./Badge";
import { Modal } from "./Modal";

/** Map a mean sentiment score (-1..+1) to a display label (Alpha-Vantage-ish bands). */
function meanToLabel(mean: number): SentimentLabel {
  if (mean >= 0.35) return "Bullish";
  if (mean >= 0.1) return "Somewhat-Bullish";
  if (mean > -0.1) return "Neutral";
  if (mean > -0.35) return "Somewhat-Bearish";
  return "Bearish";
}

interface NewsPanelProps {
  symbol: string | undefined;
}

/**
 * Mock-GenAI news summary (/assistant/news-summary) + compact headline list
 * (/instruments/{symbol}/news?limit=8). Summary refetches on symbol change and
 * refresh click only. The panel is height-bounded (Trading one-screen grid):
 * the header stays pinned and the whole content below it — summary, divider,
 * headline list — scrolls as one region, so a long summary never makes the
 * cited headlines unreachable. Clicking a headline opens its detail modal.
 */
export function NewsPanel({ symbol }: NewsPanelProps) {
  const [summary, setSummary] = useState<NewsSummary | null>(null);
  const [summaryState, setSummaryState] = useState<"idle" | "loading" | "forbidden">("idle");
  const [headlines, setHeadlines] = useState<NewsItem[]>([]);
  const [refreshTick, setRefreshTick] = useState(0);
  const [selected, setSelected] = useState<NewsItem | null>(null);

  const load = useCallback(async () => {
    if (!symbol) return;
    setSummaryState("loading");
    try {
      const res = await api<NewsSummary>("/assistant/news-summary", {
        params: { symbol },
        skipErrorToast: true,
      });
      setSummary(res);
      setSummaryState("idle");
    } catch (e) {
      // 403 = user lacks ASSISTANT_USE — show a quiet note, not an error toast.
      setSummary(null);
      setSummaryState(e instanceof ApiError && e.status === 403 ? "forbidden" : "idle");
    }
    try {
      const res = await api<NewsListResponse>(`/instruments/${symbol}/news`, {
        params: { limit: 8 },
        skipErrorToast: true,
      });
      setHeadlines(res.items);
    } catch {
      // keep last good headlines
    }
  }, [symbol]);

  usePoll(
    () => {
      void load();
    },
    0,
    [load, refreshTick],
  );

  const mean = summary?.sentiment_mean_7d ?? null;

  const { t } = useT();

  return (
    <section className="panel news-panel">
      <div className="panel-header">
        <h3>{t("news.title")}</h3>
        <span className="news-header-right">
          <span className="chip chip-static" title={summary ? t("news.modelTitle", { m: summary.model }) : undefined}>
            {summary?.mock ? t("news.mockChip") : t("news.aiChip")}
          </span>
          <button
            className="btn btn-ghost btn-sm"
            disabled={!symbol || summaryState === "loading"}
            onClick={() => setRefreshTick((tk) => tk + 1)}
          >
            {summaryState === "loading" ? "…" : t("common.refresh")}
          </button>
        </span>
      </div>

      <div className="panel-scroll">
      {summaryState === "forbidden" ? (
        <div className="panel-empty muted">{t("news.forbidden")}</div>
      ) : !summary ? (
        summaryState === "loading" ? (
          <div className="skeleton-stack">
            <div className="skeleton" style={{ height: 14 }} />
            <div className="skeleton" style={{ height: 34 }} />
            <div className="skeleton" style={{ height: 14 }} />
          </div>
        ) : (
          <div className="panel-empty muted">{t("news.noSummary")}</div>
        )
      ) : (
        <div className="news-summary">
          <div className="news-summary-top">
            {mean === null ? (
              <span className="muted">{t("news.noSentiment")}</span>
            ) : (
              <>
                <Badge text={meanToLabel(mean)} />
                <span className="num muted">
                  {t("news.meanLine", {
                    m: `${mean >= 0 ? "+" : ""}${fmtNum(mean, 2)}`,
                    n: fmtNum(summary.article_count_7d),
                  })}
                </span>
              </>
            )}
          </div>
          {mean !== null && (
            <>
              <div className="senti-strip">
                <div
                  className="senti-marker"
                  style={{ left: `${Math.min(100, Math.max(0, ((mean + 1) / 2) * 100))}%` }}
                />
              </div>
              <div className="senti-scale num">
                <span>{t("news.bearish")}</span>
                <span>0</span>
                <span>{t("news.bullish")}</span>
              </div>
            </>
          )}
          <p className="news-summary-text">{summary.summary}</p>
          {summary.top_topics.length > 0 && (
            <div className="news-topics">
              {summary.top_topics.map((t) => (
                <span key={t} className="chip chip-static">
                  {t}
                </span>
              ))}
            </div>
          )}
          {summary.headlines.length > 0 && (
            <div className="news-summary-headlines">
              {summary.headlines.slice(0, 3).map((h, i) => (
                <div key={`${h.ts}-${i}`} className="news-item">
                  <span className="news-ts muted num">{fmtTs(h.ts)}</span>
                  <span className="news-title">{h.title}</span>
                  <span className="news-badges">{h.label && <Badge text={h.label} />}</span>
                </div>
              ))}
            </div>
          )}
          <div className="muted news-asof num">{t("news.asOf", { ts: fmtTs(summary.as_of) })}</div>
        </div>
      )}

      <div className="news-divider" />

      {headlines.length === 0 ? (
        <div className="panel-empty muted">{t("news.noHeadlines")}</div>
      ) : (
        <div className="news-list news-list-compact">
          {headlines.map((n) => (
            <div
              key={n.news_id}
              className="news-item row-clickable"
              title={t("news.openDetail")}
              onClick={() => setSelected(n)}
            >
              <span className="news-ts muted num">{fmtTs(n.ts)}</span>
              <span className="news-title">{n.title}</span>
              <span className="news-badges">
                {n.sentiments
                  .filter((s) => s.label !== null)
                  .slice(0, 3)
                  .map((s) => (
                    <span key={s.ticker} className="news-badge">
                      <span className="mono">{s.ticker}</span> <Badge text={s.label} />
                    </span>
                  ))}
              </span>
            </div>
          ))}
        </div>
      )}
      </div>

      {selected && (
        <Modal title={selected.title} onClose={() => setSelected(null)}>
          <div className="news-detail-meta muted num">{fmtTs(selected.ts)}</div>
          {selected.topics.length > 0 && (
            <div className="news-topics">
              {selected.topics.map((t) => (
                <span key={t} className="chip chip-static">
                  {t}
                </span>
              ))}
            </div>
          )}
          {selected.sentiments.length === 0 ? (
            <div className="muted">{t("news.noAnnotations")}</div>
          ) : (
            <div className="news-detail-sentiments">
              {selected.sentiments.map((s) => (
                <div key={s.ticker} className="news-detail-row">
                  <span className="mono">{s.ticker}</span>
                  {s.label && <Badge text={s.label} />}
                  <span className="num muted">
                    {t("news.sentiment", {
                      v: s.sentiment_score !== null ? fmtNum(s.sentiment_score, 2) : "—",
                    })}
                  </span>
                  <span className="num muted">
                    {t("news.relevance", {
                      v: s.relevance_score !== null ? fmtNum(s.relevance_score, 2) : "—",
                    })}
                  </span>
                </div>
              ))}
            </div>
          )}
        </Modal>
      )}
    </section>
  );
}
