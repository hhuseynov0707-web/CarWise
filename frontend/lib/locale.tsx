"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import {
  DEFAULT_LOCALE,
  DICTIONARIES,
  isLocale,
  type Locale,
} from "@/lib/i18n";

const STORAGE_KEY = "autointel.locale";

type LocaleValue = {
  locale: Locale;
  setLocale: (next: Locale) => void;
  t: (typeof DICTIONARIES)[Locale];
};

const LocaleContext = createContext<LocaleValue | null>(null);

export function LocaleProvider({ children }: { children: React.ReactNode }) {
  // Server and first client render must agree, so the stored choice is applied
  // in an effect rather than read during render. Anything else is a hydration
  // mismatch that React resolves by throwing away the markup.
  const [locale, setLocaleState] = useState<Locale>(DEFAULT_LOCALE);

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(STORAGE_KEY);
      if (isLocale(stored)) {
        setLocaleState(stored);
        return;
      }
      const preferred = navigator.languages
        ?.map((tag) => tag.slice(0, 2).toLowerCase())
        .find(isLocale);
      if (preferred) setLocaleState(preferred);
    } catch {
      // Private mode, blocked storage, a browser that throws on access — the
      // default locale is a complete answer, so there is nothing to recover.
    }
  }, []);

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // A preference we cannot persist is still a preference for this session.
    }
  }, []);

  const value = useMemo<LocaleValue>(
    () => ({ locale, setLocale, t: DICTIONARIES[locale] }),
    [locale, setLocale],
  );

  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>;
}

export function useLocale(): LocaleValue {
  const value = useContext(LocaleContext);
  if (!value) throw new Error("useLocale must be used inside a LocaleProvider");
  return value;
}
