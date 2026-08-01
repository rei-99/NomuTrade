// Language state shared by the React provider and non-hook format helpers.
export type Lang = "en" | "ja";

export const LANG_STORAGE_KEY = "stp_lang";

/** Current language — reads the same localStorage key the provider writes. */
export function getLang(): Lang {
  try {
    return localStorage.getItem(LANG_STORAGE_KEY) === "ja" ? "ja" : "en";
  } catch {
    return "en";
  }
}
