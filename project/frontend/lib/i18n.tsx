"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import en from "@/messages/en.json";
import ar from "@/messages/ar.json";

export type Locale = "en" | "ar";
const CATALOGS: Record<Locale, Record<string, string>> = { en, ar };
const RTL_LOCALES: Locale[] = ["ar"];
const COOKIE = "masar_locale";

interface Ctx {
  locale: Locale;
  dir: "ltr" | "rtl";
  setLocale: (l: Locale) => void;
  t: (key: string, fallback?: string) => string;
}

const I18nContext = createContext<Ctx | null>(null);

function readInitialLocale(): Locale {
  if (typeof document === "undefined") return "en";
  const m = document.cookie.match(/(?:^|;\s*)masar_locale=(en|ar)/);
  return (m?.[1] as Locale) || "en";
}

export function LocaleProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>("en");

  // Hydrate from cookie after mount (avoids SSR/client mismatch)
  useEffect(() => { setLocaleState(readInitialLocale()); }, []);

  // Reflect language + direction on <html>
  useEffect(() => {
    const dir = RTL_LOCALES.includes(locale) ? "rtl" : "ltr";
    document.documentElement.setAttribute("lang", locale);
    document.documentElement.setAttribute("dir", dir);
  }, [locale]);

  const setLocale = useCallback((l: Locale) => {
    document.cookie = `${COOKIE}=${l}; path=/; max-age=31536000; samesite=lax`;
    setLocaleState(l);
  }, []);

  const t = useCallback(
    (key: string, fallback?: string) => CATALOGS[locale]?.[key] ?? CATALOGS.en[key] ?? fallback ?? key,
    [locale],
  );

  const dir: "ltr" | "rtl" = RTL_LOCALES.includes(locale) ? "rtl" : "ltr";
  return <I18nContext.Provider value={{ locale, dir, setLocale, t }}>{children}</I18nContext.Provider>;
}

export function useI18n(): Ctx {
  const ctx = useContext(I18nContext);
  // Safe default if a component renders outside the provider.
  if (!ctx) return { locale: "en", dir: "ltr", setLocale: () => {}, t: (_k, f) => f ?? _k };
  return ctx;
}
