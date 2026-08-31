"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { LOCALES, LOCALE_NAMES, LOCALE_SHORT, type Locale } from "@/lib/i18n";
import { useLocale } from "@/lib/locale";
import { DEFAULT_TAB, isTabId, TAB_IDS, type TabId } from "@/lib/tabs";

/**
 * Header, tab bar and language switcher.
 *
 * The active tab lives in the URL fragment. That makes every tab linkable and
 * makes the browser back button move between them, which is what a person
 * expects from something that looks like navigation. A tab held only in React
 * state silently turns Back into "leave the site".
 */
export function AppShell({
  active,
  onSelect,
  children,
}: {
  active: TabId;
  onSelect: (tab: TabId) => void;
  children: React.ReactNode;
}) {
  const { t } = useLocale();
  const tabRefs = useRef<Partial<Record<TabId, HTMLButtonElement | null>>>({});

  // Left/right arrows move between tabs, Home/End jump to the ends. Without
  // this a tab bar is a row of buttons that a keyboard user must tab through
  // one at a time to reach the panel behind them.
  const onKeyDown = useCallback(
    (event: React.KeyboardEvent) => {
      const index = TAB_IDS.indexOf(active);
      let next: TabId | undefined;
      if (event.key === "ArrowRight") next = TAB_IDS[(index + 1) % TAB_IDS.length];
      else if (event.key === "ArrowLeft")
        next = TAB_IDS[(index - 1 + TAB_IDS.length) % TAB_IDS.length];
      else if (event.key === "Home") next = TAB_IDS[0];
      else if (event.key === "End") next = TAB_IDS[TAB_IDS.length - 1];
      if (!next) return;
      const target = next;
      event.preventDefault();
      onSelect(target);
      tabRefs.current[target]?.focus();
    },
    [active, onSelect],
  );

  return (
    <div className="min-h-screen bg-surface-sunken">
      <header className="border-b border-surface-border bg-surface">
        <div className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <p className="text-xs font-semibold uppercase tracking-widest text-ink-muted">
            {t.brand}
          </p>
          <LanguageSwitcher />
        </div>

        <nav aria-label={t.brand} className="mx-auto max-w-5xl overflow-x-auto px-4 sm:px-6">
          <div role="tablist" onKeyDown={onKeyDown} className="flex gap-1 whitespace-nowrap">
            {TAB_IDS.map((id) => {
              const selected = id === active;
              return (
                <button
                  key={id}
                  ref={(node) => {
                    tabRefs.current[id] = node;
                  }}
                  role="tab"
                  id={`tab-${id}`}
                  type="button"
                  aria-selected={selected}
                  aria-controls={`panel-${id}`}
                  tabIndex={selected ? 0 : -1}
                  title={t.navHint[id]}
                  onClick={() => onSelect(id)}
                  className={[
                    // 44px minimum target, and the underline carries the state
                    // as well as the colour does.
                    "min-h-[44px] cursor-pointer rounded-t-md border-b-2 px-3 text-sm",
                    "transition-colors duration-200 sm:px-4",
                    selected
                      ? "border-accent font-semibold text-ink"
                      : "border-transparent text-ink-muted hover:border-surface-border hover:text-ink-soft",
                  ].join(" ")}
                >
                  {t.nav[id]}
                </button>
              );
            })}
          </div>
        </nav>
      </header>

      <main id="main" className="mx-auto max-w-5xl px-4 py-8 sm:px-6 sm:py-10">
        {children}
      </main>

      <footer className="mx-auto max-w-5xl border-t border-surface-border px-4 py-6 text-xs leading-relaxed text-ink-muted sm:px-6">
        {t.disclaimer}
      </footer>
    </div>
  );
}

function LanguageSwitcher() {
  const { locale, setLocale, t } = useLocale();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent && event.key !== "Escape") return;
      setOpen(false);
    };
    document.addEventListener("click", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("click", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);

  return (
    <div className="relative">
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={t.actions.language}
        onClick={(event) => {
          event.stopPropagation();
          setOpen((value) => !value);
        }}
        className="min-h-[44px] cursor-pointer rounded-md border border-surface-border px-3
                   text-sm font-medium text-ink-soft transition-colors duration-200
                   hover:bg-surface-sunken"
      >
        {LOCALE_SHORT[locale]}
      </button>

      {open ? (
        <ul
          role="listbox"
          aria-label={t.actions.language}
          className="absolute right-0 z-20 mt-1 w-44 overflow-hidden rounded-md border
                     border-surface-border bg-surface shadow-lg"
        >
          {LOCALES.map((code: Locale) => (
            <li key={code}>
              <button
                type="button"
                role="option"
                aria-selected={code === locale}
                onClick={() => {
                  setLocale(code);
                  setOpen(false);
                }}
                className={[
                  "flex min-h-[44px] w-full cursor-pointer items-center justify-between px-3",
                  "text-left text-sm transition-colors duration-200 hover:bg-surface-sunken",
                  code === locale ? "font-semibold text-ink" : "text-ink-soft",
                ].join(" ")}
              >
                {LOCALE_NAMES[code]}
                <span className="text-xs text-ink-faint">{LOCALE_SHORT[code]}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/**
 * Reads and writes the active tab in the URL fragment.
 *
 * A tab may carry one argument after a colon — ``#chat:cfg_abc`` — which is
 * how "discuss this one with the expert" arrives at a tab that would otherwise
 * have no idea which car was meant. Keeping it in the fragment means that link
 * is shareable and survives a reload, which shared React state would not.
 */
export function useTabFromHash(): [TabId, string | null, (tab: TabId, arg?: string) => void] {
  const [tab, setTab] = useState<TabId>(DEFAULT_TAB);
  const [arg, setArg] = useState<string | null>(null);

  useEffect(() => {
    const read = () => {
      const raw = window.location.hash.replace(/^#/, "");
      const [name, payload] = raw.split(":", 2);
      setTab(isTabId(name) ? name : DEFAULT_TAB);
      setArg(payload || null);
    };
    read();
    window.addEventListener("hashchange", read);
    return () => window.removeEventListener("hashchange", read);
  }, []);

  const select = useCallback((next: TabId, payload?: string) => {
    const target = payload ? `${next}:${payload}` : next;
    setTab(next);
    setArg(payload ?? null);
    if (window.location.hash.replace(/^#/, "") !== target) {
      window.location.hash = target;
    }
  }, []);

  return [tab, arg, select];
}
