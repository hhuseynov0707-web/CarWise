/**
 * The application's destinations, and which of them actually work.
 *
 * ``status`` is here so an unfinished tab renders an honest explanation rather
 * than an empty panel or, worse, plausible-looking placeholder data. A market
 * tool that invents a deal list to fill a screen has broken the one promise it
 * makes on every report.
 */

export const TAB_IDS = ["analyse", "discover", "deals", "chat", "saved", "profile"] as const;
export type TabId = (typeof TAB_IDS)[number];

export type TabStatus = "ready" | "needs-account" | "needs-key" | "not-built";

export const TAB_STATUS: Record<TabId, TabStatus> = {
  analyse: "ready",
  discover: "ready",
  deals: "ready",
  chat: "needs-key",
  saved: "ready",
  profile: "ready",
};

export const DEFAULT_TAB: TabId = "analyse";

export function isTabId(value: string | null | undefined): value is TabId {
  return !!value && (TAB_IDS as readonly string[]).includes(value);
}
