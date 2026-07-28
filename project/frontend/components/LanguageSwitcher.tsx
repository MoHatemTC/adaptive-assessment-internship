"use client";

import { useI18n } from "@/lib/i18n";

export default function LanguageSwitcher({ className = "" }: { className?: string }) {
  const { locale, setLocale } = useI18n();
  return (
    <button
      type="button"
      onClick={() => setLocale(locale === "ar" ? "en" : "ar")}
      title="Switch language / تبديل اللغة"
      className={`text-sm font-semibold text-muted-foreground hover:text-foreground px-2.5 py-1.5 rounded-lg border border-border hover:bg-secondary transition-colors ${className}`}
    >
      {locale === "ar" ? "EN" : "ع"}
    </button>
  );
}
