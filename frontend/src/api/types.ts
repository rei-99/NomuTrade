// API contract types for the STP platform (base /api/v1).
// Every shape here mirrors the agreed backend contract — do not "improve"
// field names without coordinating with the backend.

// ---------- generic envelopes ----------

export interface ListResponse<T> {
  items: T[];
  next_cursor: string | null;
}

export interface ErrorBody {
  code: string;
  message: string;
  details?: unknown;
  traceId?: string;
}

export interface ErrorEnvelope {
  error: ErrorBody;
}

// ---------- auth ----------

export interface User {
  upn: string;
  display_name: string;
  email: string;
}

export interface DevLoginResponse {
  token: string;
  user: User;
}

export interface MeResponse {
  user: User;
  roles: string[];
  permissions: string[];
}

// ---------- instruments / market data ----------

export interface Instrument {
  instrument_id: string;
  symbol: string;
  name: string;
  asset_class: string;
  currency: string;
  lot_size: number;
  tick_size: number;
  tradable: boolean;
  latest_price: number | null;
}

export type Timeframe = "1D" | "1W" | "1M" | "3M" | "1Y" | "MAX";
export const TIMEFRAMES: Timeframe[] = ["1D", "1W", "1M", "3M", "1Y", "MAX"];

export interface Candle {
  ts: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface PriceSeries {
  symbol: string;
  timeframe: Timeframe;
  candles: Candle[];
}

export type IndicatorName = "SMA" | "EMA" | "RSI" | "MACD" | "BB";
export const INDICATOR_NAMES: IndicatorName[] = ["SMA", "EMA", "RSI", "MACD", "BB"];

export interface IndicatorPoint {
  ts: string;
  value: number;
}

export interface MacdPoint {
  ts: string;
  macd: number;
  signal: number;
  histogram: number;
}

export interface BbPoint {
  ts: string;
  upper: number;
  middle: number;
  lower: number;
}

export interface IndicatorsResponse {
  indicators: {
    SMA?: IndicatorPoint[];
    EMA?: IndicatorPoint[];
    RSI?: IndicatorPoint[];
    MACD?: MacdPoint[];
    BB?: BbPoint[];
  };
}

// ---------- orders / trades ----------

export type OrderSide = "BUY" | "SELL";
export type OrderType = "MARKET" | "LIMIT";
export type OrderStatus =
  | "ACCEPTED"
  | "REJECTED"
  | "OPEN"
  | "PARTIALLY_FILLED"
  | "FILLED"
  | "CANCELLED";

export interface Execution {
  execution_id: string;
  price: number;
  quantity: number;
  executed_at: string;
}

export interface Order {
  order_id: string;
  portfolio_id: string;
  instrument_symbol: string;
  side: OrderSide;
  order_type: OrderType;
  quantity: number;
  limit_price: number | null;
  status: OrderStatus;
  reject_reason: string | null;
  created_at: string;
  executions: Execution[];
}

export interface OrderRequest {
  portfolio_id: string;
  instrument: string;
  side: OrderSide;
  order_type: OrderType;
  quantity: number;
  limit_price?: number;
}

export interface OrderCreated {
  order_id: string;
  status: OrderStatus;
  submitted_at: string;
}

export type PortfolioType = "CLIENT" | "HOUSE" | "PAPER";

export interface Trade {
  execution_id: string;
  order_id: string;
  portfolio_id: string;
  instrument_symbol: string;
  side: OrderSide;
  price: number;
  quantity: number;
  executed_at: string;
  portfolio_type: PortfolioType;
}

// ---------- portfolios ----------

export interface Portfolio {
  portfolio_id: string;
  name: string;
  type: PortfolioType;
  owner_id: string;
  cash_balance: number;
  total_value: number;
}

export interface Position {
  instrument_symbol: string;
  name: string;
  asset_class: string;
  quantity: number;
  avg_cost: number;
  latest_price: number;
  market_value: number;
  unrealized_pnl: number;
  stale_price: boolean;
}

export interface PositionsResponse {
  as_of: string;
  items: Position[];
  totals: {
    market_value: number;
    unrealized_pnl: number;
  };
}

export interface AllocationSlice {
  asset_class: string;
  value: number;
  pct: number;
}

export interface TopHolding {
  instrument_symbol: string;
  market_value: number;
  pct: number;
}

export interface Valuation {
  ts: string;
  cash: number;
  market_value: number;
  total_value: number;
  realized_pnl: number;
  unrealized_pnl: number;
  day_change: number;
  kpis: {
    allocation: AllocationSlice[];
    top_holdings: TopHolding[];
    concentration_pct: number;
    volatility_annualized_pct: number | null;
  };
}

export type TransactionKind = "EXECUTION";

export interface Transaction {
  ts: string;
  kind: TransactionKind;
  instrument_symbol: string;
  side: OrderSide;
  quantity: number;
  price: number;
  amount: number;
  ref_id: string;
}

export interface PerformanceSeries {
  series: { ts: string; total_value: number }[];
}

// ---------- reports ----------

export type ReportType = "HOLDINGS" | "TRANSACTIONS" | "PERFORMANCE";
export type ReportFormat = "PDF" | "CSV";

export interface Report {
  report_id: string;
  type: ReportType;
  portfolio_id: string;
  period_start: string;
  period_end: string;
  format: ReportFormat;
  status: string;
  created_at: string;
  download_url: string | null;
}

export interface ReportCreated {
  report_id: string;
  status: string;
  download_url: string | null;
}

// ---------- paper trading ----------

export interface PaperAccountCreated {
  portfolio_id: string;
  name: string;
  cash_balance: number;
  initial_balance: number;
}

export interface PaperStatistics {
  trades: number;
  win_rate: number;
  avg_pnl_per_trade: number;
  max_drawdown: number;
}

export interface PaperAccount {
  portfolio_id: string;
  name: string;
  cash_balance: number;
  initial_balance: number;
  statistics: PaperStatistics | null;
  equity_curve: { ts: string; value: number }[];
}

// ---------- assistant ----------

export interface Citation {
  kind: string;
  ref: string;
  figures: Record<string, unknown>;
}

export interface SuggestedTicket {
  portfolio_id?: string | null;
  instrument: string;
  side: OrderSide;
  quantity?: number | null;
}

export interface AssistantResponse {
  answer: string;
  citations: Citation[];
  suggested_ticket: SuggestedTicket | null;
}

// ---------- access requests / approvals ----------

export interface UserRef {
  email: string;
  display_name: string;
}

export interface RoleRef {
  role_id: string;
  name: string;
}

export interface ApprovalStep {
  step_id: string;
  level: number;
  approver: UserRef;
  decision: string | null;
  comment: string | null;
  decided_at: string | null;
}

export interface AccessRequest {
  request_id: string;
  requester: UserRef;
  on_behalf_of: string | null;
  role: RoleRef;
  justification: string;
  requested_duration_hours: number;
  status: string;
  created_at: string;
  decided_at: string | null;
  steps: ApprovalStep[];
}

// ASSUMPTION: the exact shape of `levels` entries in the 201 response of
// POST /access-requests is not pinned down by the contract; we keep the known
// `level` field and tolerate anything else.
export interface AccessLevelInfo {
  level: number;
  [extra: string]: unknown;
}

export interface AccessRequestCreated {
  request_id: string;
  status: string;
  current_level: number;
  levels: AccessLevelInfo[];
}

export interface ApprovalItem {
  step_id: string;
  level: number;
  request: AccessRequest;
}

// ---------- roles / permissions / grants ----------

export interface Role {
  role_id: string;
  name: string;
  description: string;
  built_in: boolean;
  version: number;
  permissions: string[];
}

// ASSUMPTION: GET /permissions item shape is not specified in the contract;
// the backend seeds (action, resource) pairs so we model that, and the UI
// normalizes plain-string arrays defensively as well.
export interface PermissionInfo {
  action: string;
  resource?: string;
  description?: string;
}

export interface Grant {
  grant_id: string;
  user: UserRef;
  role: RoleRef;
  start_at: string;
  end_at: string;
  status: string;
}

// ---------- PAM / break-glass ----------

export interface PamCheckout {
  checkout_id: string;
  safe_name: string;
  account_id: string;
  credential: string;
  checked_out_at: string;
}

export interface BreakGlassActivated {
  bg_id: string;
  grant_id: string;
  expires_at: string;
}

export interface BreakGlassReview {
  bg_id: string;
  user: { email: string };
  emergency_role: string;
  reason: string;
  incident_ref: string;
  activated_at: string;
  expires_at: string;
  review_status: string;
  verdict: string | null;
}

// ---------- audit ----------

export interface AuditEvent {
  event_id: string;
  ts: string;
  actor_email: string;
  event_type: string;
  resource_type: string;
  resource_id: string;
  severity: string;
  source_ip: string;
  correlation_id: string;
  payload: Record<string, unknown>;
}

// ---------- notifications ----------

export interface AppNotification {
  notification_id: string;
  category: string;
  channel: string;
  payload: { title: string; body: string };
  status: string;
  created_at: string;
}

export interface NotificationPreferences {
  categories: Record<string, boolean>;
}

// ---------- admin / governance ----------

export interface GovernanceSummary {
  active_grants: number;
  pending_approvals: number;
  oldest_age_hours?: number | null;
  grants_expiring_24h: number;
  break_glass_pending_review: number;
  authorization_denials_24h: number;
  recent_break_glass: BreakGlassReview[];
}

export interface IntegrationHealth {
  name: string;
  status: "UP" | "DOWN" | "DEGRADED";
  last_success: string | null;
  detail: string | null;
}

// ASSUMPTION: STP exception item shape is not pinned down in the contract;
// the governance UI renders the known-ish fields when present and falls back
// to JSON for the rest.
export interface StpException {
  exception_id?: string;
  order_id?: string;
  reason?: string;
  status?: string;
  ts?: string;
  [extra: string]: unknown;
}

export interface AdminHealth {
  integrations: IntegrationHealth[];
  outbox_unpublished: number;
  stp_exceptions: StpException[];
}
