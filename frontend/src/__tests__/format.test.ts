import { beforeEach, describe, expect, it } from "vitest";
import {
  fmtDate,
  fmtJpy,
  fmtNum,
  fmtPct,
  fmtSignedJpy,
  fmtTs,
  pnlClass,
} from "../format";
import { LANG_STORAGE_KEY } from "../i18n/lang";

describe("format helpers", () => {
  beforeEach(() => {
    localStorage.removeItem(LANG_STORAGE_KEY); // en-US locale
  });

  describe("fmtJpy (USD money, name kept from the JPY era)", () => {
    it("formats USD with 2 decimals", () => {
      expect(fmtJpy(1234.5)).toBe("$1,234.50");
      expect(fmtJpy(0)).toBe("$0.00");
      expect(fmtJpy(-5)).toBe("-$5.00");
    });

    it("renders a dash for null/undefined/NaN", () => {
      expect(fmtJpy(null)).toBe("—");
      expect(fmtJpy(undefined)).toBe("—");
      expect(fmtJpy(Number.NaN)).toBe("—");
    });

    it("ignores the legacy decimals flag", () => {
      expect(fmtJpy(1234.567, true)).toBe("$1,234.57");
    });
  });

  describe("fmtNum", () => {
    it("formats with grouping by default", () => {
      expect(fmtNum(1234567.891)).toBe("1,234,567.891");
    });

    it("honors an explicit digit count", () => {
      expect(fmtNum(1234.5, 2)).toBe("1,234.50");
      expect(fmtNum(1234.567, 0)).toBe("1,235");
    });

    it("renders a dash for null/undefined/NaN", () => {
      expect(fmtNum(null)).toBe("—");
      expect(fmtNum(undefined)).toBe("—");
      expect(fmtNum(Number.NaN)).toBe("—");
    });

    it("formats zero", () => {
      expect(fmtNum(0)).toBe("0");
      expect(fmtNum(0, 2)).toBe("0.00");
    });
  });

  describe("fmtPct", () => {
    it("appends % with one decimal by default", () => {
      expect(fmtPct(12.345)).toBe("12.3%");
      expect(fmtPct(0)).toBe("0.0%");
    });

    it("honors an explicit digit count", () => {
      expect(fmtPct(-2.5, 2)).toBe("-2.50%");
    });

    it("renders a dash for null/undefined/NaN", () => {
      expect(fmtPct(null)).toBe("—");
      expect(fmtPct(undefined)).toBe("—");
      expect(fmtPct(Number.NaN)).toBe("—");
    });
  });

  describe("fmtTs", () => {
    it("renders MM-DD HH:mm:ss in local time", () => {
      // No timezone suffix → parsed as local time, stable in any TZ.
      expect(fmtTs("2026-07-26T14:03:11")).toBe("07-26 14:03:11");
    });

    it("zero-pads single-digit fields", () => {
      expect(fmtTs("2026-01-02T03:04:05")).toBe("01-02 03:04:05");
    });

    it("renders a dash for empty input", () => {
      expect(fmtTs(null)).toBe("—");
      expect(fmtTs(undefined)).toBe("—");
      expect(fmtTs("")).toBe("—");
    });

    it("returns unparseable input verbatim", () => {
      expect(fmtTs("not-a-date")).toBe("not-a-date");
    });
  });

  describe("fmtDate", () => {
    it("renders a locale date", () => {
      expect(fmtDate("2026-07-26T14:03:11")).toBe("7/26/2026");
    });

    it("renders a dash for empty input and echoes garbage", () => {
      expect(fmtDate(null)).toBe("—");
      expect(fmtDate("")).toBe("—");
      expect(fmtDate("junk")).toBe("junk");
    });
  });

  describe("locale follows the UI language", () => {
    it("switches to ja-JP formatting when the stored lang is ja", () => {
      localStorage.setItem(LANG_STORAGE_KEY, "ja");
      expect(fmtDate("2026-07-26T14:03:11")).toBe("2026/7/26");
    });
  });

  describe("pnlClass", () => {
    it("maps the sign to a coloring class", () => {
      expect(pnlClass(1)).toBe("pos");
      expect(pnlClass(-1)).toBe("neg");
    });

    it("returns empty for zero / null / undefined", () => {
      expect(pnlClass(0)).toBe("");
      expect(pnlClass(null)).toBe("");
      expect(pnlClass(undefined)).toBe("");
    });
  });

  describe("fmtSignedJpy", () => {
    it("always carries an explicit sign", () => {
      expect(fmtSignedJpy(1234.5)).toBe("+$1,234.50");
      expect(fmtSignedJpy(-1234.5)).toBe("-$1,234.50");
      expect(fmtSignedJpy(0)).toBe("$0.00"); // no sign on zero
    });

    it("renders a dash for null/undefined/NaN", () => {
      expect(fmtSignedJpy(null)).toBe("—");
      expect(fmtSignedJpy(undefined)).toBe("—");
      expect(fmtSignedJpy(Number.NaN)).toBe("—");
    });
  });
});
