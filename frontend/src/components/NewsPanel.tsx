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
import { Badge } from "./Badge";

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
 * refresh click only.
 */
export function NewsPanel({ symbol }: NewsPanelProps) {
  const [summary, setSummary] = useState<NewsSummary | null>(null);
  const [summaryState, setSummaryState] = useState<"idle" | "loading" | "forbidden">("idle");
  const [headlines, setHeadlines] = useState<NewsItem[]>([]);
  const [refreshTick, setRefreshTick] = useState(0);

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

  return (
    <section className="panel">
      <div className="panel-header">
        <h3>News</h3>
        <span className="news-header-right">
          <span className="chip chip-static" title={summary ? `model: ${summary.model}` : undefined}>
            AI summary · beta
          </span>
          <button
            className="btn btn-ghost btn-sm"
            disabled={!symbol || summaryState === "loading"}
            onClick={() => setRefreshTick((t) => t + 1)}
          >
            {summaryState === "loading" ? "…" : "Refresh"}
          </button>
        </span>
      </div>

      {summaryState === "forbidden" ? (
        <div className="panel-empty muted">News summary requires the ASSISTANT_USE permission.</div>
      ) : !summary ? (
        summaryState === "loading" ? (
          <div className="skeleton-stack">
            <div className="skeleton" style={{ height: 14 }} />
            <div className="skeleton" style={{ height: 34 }} />
            <div className="skeleton" style={{ height: 14 }} />
          </div>
        ) : (
          <div className="panel-empty muted">No summary available.</div>
        )
      ) : (
        <div className="news-summary">
          <div className="news-summary-top">
            {mean === null ? (
              <span className="muted">No sentiment data</span>
            ) : (
              <>
                <Badge text={meanToLabel(mean)} />
                <span className="num muted">
                  mean {mean >= 0 ? "+" : ""}
                  {fmtNum(mean, 2)} · {fmtNum(summary.article_count_7d)} articles (7d)
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
                <span>−1 bearish</span>
                <span>0</span>
                <span>+1 bullish</span>
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
          <div className="muted news-asof num">as of {fmtTs(summary.as_of)}</div>
        </div>
      )}

      <div className="news-divider" />

      {headlines.length === 0 ? (
        <div className="panel-empty muted">No headlines in this period.</div>
      ) : (
        <div className="news-list news-list-compact">
          {headlines.map((n) => (
            <div key={n.news_id} className="news-item">
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
    </section>
  );
}
