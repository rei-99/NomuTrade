import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { en } from "../en";
import type { I18nKey } from "../en";
import { ja } from "../ja";
import { I18nProvider, useT } from "../index";
import { getLang, LANG_STORAGE_KEY } from "../lang";

function Probe({ k, vars }: { k: I18nKey; vars?: Record<string, string | number> }) {
  const { t, lang, setLang } = useT();
  return (
    <div>
      <span data-testid="out">{t(k, vars)}</span>
      <span data-testid="lang">{lang}</span>
      <button onClick={() => setLang(lang === "en" ? "ja" : "en")}>switch</button>
    </div>
  );
}

describe("getLang", () => {
  beforeEach(() => localStorage.clear());

  it("defaults to en", () => {
    expect(getLang()).toBe("en");
  });

  it("reads the stored language", () => {
    localStorage.setItem(LANG_STORAGE_KEY, "ja");
    expect(getLang()).toBe("ja");
  });

  it("treats any other stored value as en", () => {
    localStorage.setItem(LANG_STORAGE_KEY, "fr");
    expect(getLang()).toBe("en");
  });
});

describe("dictionaries", () => {
  it("ja covers every en key", () => {
    for (const key of Object.keys(en)) {
      expect(ja[key as I18nKey], key).toBeTypeOf("string");
    }
  });
});

describe("useT / t()", () => {
  beforeEach(() => localStorage.clear());

  it("looks up keys in the active dictionary", () => {
    render(
      <I18nProvider>
        <Probe k="common.save" />
      </I18nProvider>,
    );
    expect(screen.getByTestId("out")).toHaveTextContent("Save");
    expect(screen.getByTestId("lang")).toHaveTextContent("en");
  });

  it("switching EN→JA changes rendered strings and persists the choice", () => {
    render(
      <I18nProvider>
        <Probe k="common.save" />
      </I18nProvider>,
    );
    act(() => screen.getByRole("button", { name: "switch" }).click());
    expect(screen.getByTestId("out")).toHaveTextContent("保存");
    expect(screen.getByTestId("lang")).toHaveTextContent("ja");
    expect(localStorage.getItem(LANG_STORAGE_KEY)).toBe("ja");
    expect(document.documentElement.lang).toBe("ja");
  });

  it("interpolates {placeholder} vars", () => {
    render(
      <I18nProvider>
        <Probe k="admin.restricted.added" vars={{ symbol: "AAPL" }} />
      </I18nProvider>,
    );
    expect(screen.getByTestId("out")).toHaveTextContent("AAPL restricted");
  });

  it("returns the key itself for a missing key (cast past the type)", () => {
    render(
      <I18nProvider>
        <Probe k={"no.such.key" as I18nKey} />
      </I18nProvider>,
    );
    expect(screen.getByTestId("out")).toHaveTextContent("no.such.key");
  });

  it("throws outside the provider", () => {
    function Bare() {
      useT();
      return null;
    }
    // React dev-mode re-reports render errors through a fake DOM event; swallow
    // the jsdom "uncaught error" report for this expected throw.
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    const swallow = (e: Event) => e.preventDefault();
    window.addEventListener("error", swallow);
    expect(() => render(<Bare />)).toThrow("useT must be used within I18nProvider");
    window.removeEventListener("error", swallow);
    spy.mockRestore();
  });
});
