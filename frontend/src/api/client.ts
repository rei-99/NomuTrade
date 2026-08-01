import type { ErrorBody } from "./types";

const BASE = "/api/v1";
const TOKEN_KEY = "stp_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

/** Parsed standard error envelope: {"error":{code,message,details,traceId}}. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;
  readonly traceId?: string;

  constructor(status: number, body: ErrorBody) {
    super(body.message);
    this.name = "ApiError";
    this.status = status;
    this.code = body.code;
    this.details = body.details;
    this.traceId = body.traceId;
  }
}

export type ApiErrorHandler = (err: ApiError) => void;
let errorHandler: ApiErrorHandler | null = null;

/** The toast system registers here so every API failure surfaces a banner. */
export function setApiErrorHandler(fn: ApiErrorHandler | null): void {
  errorHandler = fn;
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  params?: Record<string, string | number | undefined>;
  headers?: Record<string, string>;
  /** Return the raw Response (for blobs / status-code-sensitive calls). */
  raw?: boolean;
  /** Do not raise the global error toast for this call. */
  skipErrorToast?: boolean;
  /**
   * Do not treat 401 as session-expiry (token clear + redirect to /login).
   * Needed by the login form itself, where 401 means "invalid credentials"
   * and the envelope must reach the caller intact.
   */
  skipAuthRedirect?: boolean;
}

export async function api<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const url = new URL(BASE + path, window.location.origin);
  if (opts.params) {
    for (const [k, v] of Object.entries(opts.params)) {
      if (v !== undefined && v !== "") url.searchParams.set(k, String(v));
    }
  }

  const headers: Record<string, string> = { ...opts.headers };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  let body: BodyInit | undefined;
  if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }

  let res: Response;
  try {
    res = await fetch(url.toString(), { method: opts.method ?? "GET", headers, body });
  } catch {
    const err = new ApiError(0, { code: "NETWORK", message: "Network error — is the backend running on :8000?" });
    if (!opts.skipErrorToast) errorHandler?.(err);
    throw err;
  }

  if (res.status === 401 && !opts.skipAuthRedirect) {
    // Session expired / invalid token: drop it and bounce to login.
    setToken(null);
    if (!window.location.pathname.startsWith("/login")) {
      window.location.assign("/login");
    }
    throw new ApiError(401, { code: "UNAUTHORIZED", message: "Session expired — please sign in again." });
  }

  const contentType = res.headers.get("content-type") ?? "";

  if (!res.ok) {
    let errBody: ErrorBody = { code: `HTTP_${res.status}`, message: res.statusText || `Request failed (${res.status})` };
    if (contentType.includes("application/json")) {
      try {
        const parsed: unknown = await res.json();
        if (parsed && typeof parsed === "object" && "error" in parsed) {
          const env = (parsed as { error: Partial<ErrorBody> }).error;
          errBody = {
            code: typeof env.code === "string" ? env.code : errBody.code,
            message: typeof env.message === "string" ? env.message : errBody.message,
            details: env.details,
            traceId: typeof env.traceId === "string" ? env.traceId : undefined,
          };
        }
      } catch {
        // keep the HTTP fallback body
      }
    }
    const err = new ApiError(res.status, errBody);
    if (!opts.skipErrorToast) errorHandler?.(err);
    throw err;
  }

  if (opts.raw) return res as unknown as T;
  if (res.status === 204) return undefined as T;
  if (contentType.includes("application/json")) return (await res.json()) as T;
  return undefined as T;
}

/** Authenticated blob download with save-as. */
export async function downloadFile(
  path: string,
  params: Record<string, string | number | undefined>,
  fallbackFilename: string,
): Promise<void> {
  const res = await api<Response>(path, { params, raw: true });
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") ?? "";
  const match = /filename\*?=(?:UTF-8''|"?)([^";]+)/i.exec(cd);
  const filename = match ? decodeURIComponent(match[1].replace(/"$/, "")) : fallbackFilename;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
