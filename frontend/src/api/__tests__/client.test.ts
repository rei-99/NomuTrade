import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, getToken, setApiErrorHandler, setToken } from "../client";

/**
 * client.ts is tested against a stubbed global.fetch; Response objects are
 * real (jsdom/undici). window.location.assign cannot be spied in jsdom
 * (non-configurable), so the 401 tests stub the whole `location` global.
 */

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function lastCall(): { url: string; init: RequestInit } {
  const call = vi.mocked(fetch).mock.calls.at(-1);
  if (!call) throw new Error("fetch was not called");
  return { url: String(call[0]), init: (call[1] ?? {}) as RequestInit };
}

describe("api client", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
    localStorage.clear();
    setApiErrorHandler(null);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setApiErrorHandler(null);
    localStorage.clear();
  });

  describe("request building", () => {
    it("attaches the Bearer token from localStorage", async () => {
      setToken("tok-123");
      vi.mocked(fetch).mockResolvedValue(jsonResponse(200, { ok: true }));
      await api("/things");
      const { init } = lastCall();
      expect(init.headers).toMatchObject({ Authorization: "Bearer tok-123" });
    });

    it("omits the Authorization header without a token", async () => {
      vi.mocked(fetch).mockResolvedValue(jsonResponse(200, {}));
      await api("/things");
      const { init } = lastCall();
      expect(init.headers).not.toMatchObject({ Authorization: expect.anything() });
    });

    it("prefixes /api/v1, appends params, skips undefined and empty strings", async () => {
      vi.mocked(fetch).mockResolvedValue(jsonResponse(200, {}));
      await api("/things", { params: { a: "1", b: 2, c: undefined, d: "" } });
      const url = new URL(lastCall().url);
      expect(url.pathname).toBe("/api/v1/things");
      expect(url.searchParams.get("a")).toBe("1");
      expect(url.searchParams.get("b")).toBe("2");
      expect(url.searchParams.has("c")).toBe(false);
      expect(url.searchParams.has("d")).toBe(false);
    });

    it("serializes a JSON body with its content type and method", async () => {
      vi.mocked(fetch).mockResolvedValue(jsonResponse(200, {}));
      await api("/things", { method: "POST", body: { x: 1 } });
      const { init } = lastCall();
      expect(init.method).toBe("POST");
      expect(init.headers).toMatchObject({ "Content-Type": "application/json" });
      expect(init.body).toBe('{"x":1}');
    });

    it("parses a JSON success body and returns undefined for 204", async () => {
      vi.mocked(fetch).mockResolvedValue(jsonResponse(200, { hello: "world" }));
      await expect(api<{ hello: string }>("/things")).resolves.toEqual({ hello: "world" });

      vi.mocked(fetch).mockResolvedValue(new Response(null, { status: 204 }));
      await expect(api("/things", { method: "DELETE" })).resolves.toBeUndefined();
    });

    it("returns the raw Response when raw: true", async () => {
      const res = jsonResponse(200, { a: 1 });
      vi.mocked(fetch).mockResolvedValue(res);
      const out = await api<Response>("/things", { raw: true });
      expect(out).toBe(res);
    });
  });

  describe("error handling", () => {
    it("parses the standard error envelope into ApiError", async () => {
      vi.mocked(fetch).mockResolvedValue(
        jsonResponse(422, {
          error: { code: "VALIDATION", message: "bad order", details: [{ rule: "r" }], traceId: "t-1" },
        }),
      );
      const err = await api("/orders", { method: "POST" }).catch((e: unknown) => e);
      expect(err).toBeInstanceOf(ApiError);
      const apiErr = err as ApiError;
      expect(apiErr.status).toBe(422);
      expect(apiErr.code).toBe("VALIDATION");
      expect(apiErr.message).toBe("bad order");
      expect(apiErr.details).toEqual([{ rule: "r" }]);
      expect(apiErr.traceId).toBe("t-1");
    });

    it("falls back to HTTP_<status> when the body is not an envelope", async () => {
      vi.mocked(fetch).mockResolvedValue(
        new Response("nope", { status: 500, statusText: "Server Error" }),
      );
      const err = (await api("/x").catch((e: unknown) => e)) as ApiError;
      expect(err.code).toBe("HTTP_500");
      expect(err.message).toBe("Server Error");
    });

    it("raises the global error handler (toast) unless skipErrorToast", async () => {
      const handler = vi.fn();
      setApiErrorHandler(handler);
      vi.mocked(fetch).mockResolvedValue(jsonResponse(500, { error: { code: "X", message: "m" } }));

      await expect(api("/x")).rejects.toBeInstanceOf(ApiError);
      expect(handler).toHaveBeenCalledTimes(1);
      expect((handler.mock.calls[0]?.[0] as ApiError).code).toBe("X");

      await expect(api("/x", { skipErrorToast: true })).rejects.toBeInstanceOf(ApiError);
      expect(handler).toHaveBeenCalledTimes(1); // not called again
    });

    it("maps a network failure to a status-0 NETWORK ApiError", async () => {
      const handler = vi.fn();
      setApiErrorHandler(handler);
      vi.mocked(fetch).mockRejectedValue(new TypeError("failed to fetch"));

      const err = (await api("/x").catch((e: unknown) => e)) as ApiError;
      expect(err).toBeInstanceOf(ApiError);
      expect(err.status).toBe(0);
      expect(err.code).toBe("NETWORK");
      expect(handler).toHaveBeenCalledTimes(1);
    });
  });

  describe("401 handling", () => {
    // jsdom's Location.assign is non-configurable (cannot be spied), so the
    // whole `location` global is stubbed to capture the bounce.
    function stubLocation() {
      const assign = vi.fn();
      vi.stubGlobal("location", {
        origin: "http://localhost:3000",
        protocol: "http:",
        host: "localhost:3000",
        pathname: "/",
        assign,
      });
      return assign;
    }

    it("drops the token, bounces to /login and throws UNAUTHORIZED", async () => {
      setToken("expired");
      const assign = stubLocation();
      vi.mocked(fetch).mockResolvedValue(jsonResponse(401, { error: { code: "X", message: "m" } }));

      const err = (await api("/x").catch((e: unknown) => e)) as ApiError;
      expect(err.code).toBe("UNAUTHORIZED");
      expect(getToken()).toBeNull();
      expect(assign).toHaveBeenCalledWith("/login");
    });

    it("does not raise the global error toast for the 401 bounce", async () => {
      setToken("expired");
      const handler = vi.fn();
      setApiErrorHandler(handler);
      stubLocation();
      vi.mocked(fetch).mockResolvedValue(jsonResponse(401, { error: { code: "X", message: "m" } }));

      await expect(api("/x")).rejects.toBeInstanceOf(ApiError);
      expect(handler).not.toHaveBeenCalled();
    });

    it("skipAuthRedirect lets the envelope reach the caller (login form)", async () => {
      setToken("keepme");
      const assign = stubLocation();
      vi.mocked(fetch).mockResolvedValue(
        jsonResponse(401, { error: { code: "BAD_CREDENTIALS", message: "invalid" } }),
      );

      const err = (await api("/auth/login", { skipAuthRedirect: true }).catch((e: unknown) => e)) as ApiError;
      expect(err.code).toBe("BAD_CREDENTIALS");
      expect(getToken()).toBe("keepme");
      expect(assign).not.toHaveBeenCalled();
    });
  });

  describe("token helpers", () => {
    it("setToken stores and clears", () => {
      expect(getToken()).toBeNull();
      setToken("abc");
      expect(getToken()).toBe("abc");
      setToken(null);
      expect(getToken()).toBeNull();
    });
  });
});
