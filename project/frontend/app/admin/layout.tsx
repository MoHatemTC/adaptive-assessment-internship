"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Menu, X } from "lucide-react";
import { getCurrentUser } from "@/lib/auth";
import UserMenu from "@/components/auth/UserMenu";
import Logo from "@/components/Logo";
import LanguageSwitcher from "@/components/LanguageSwitcher";
import { useI18n } from "@/lib/i18n";
import type { UserProfile } from "@/lib/types";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<UserProfile | null>(null);
  const [checking, setChecking] = useState(true);
  const [menuOpen, setMenuOpen] = useState(false);
  const { t } = useI18n();

  const navLinks = [
    { href: "/admin", key: "admin.nav.assessments" },
    { href: "/admin/sessions", key: "admin.nav.candidates" },
    { href: "/admin/pipelines", key: "admin.nav.pipeline" },
    { href: "/admin/question-bank", key: "admin.nav.questionBank" },
    { href: "/admin/proctoring", key: "admin.nav.proctoring" },
    { href: "/admin/email-templates", key: "admin.nav.emailTemplate" },
  ];

  useEffect(() => {
    getCurrentUser().then((u) => {
      if (!u) { router.push("/"); return; }
      if (u.role !== "admin") { router.push("/dashboard"); return; }
      setUser(u);
      setChecking(false);
    });
  }, [router]);

  if (checking) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="w-8 h-8 rounded-full border-4 border-primary/20 border-t-primary animate-spin" />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-secondary/30 flex flex-col">
      <header className="bg-background border-b border-border px-4 sm:px-6 py-3">
        <div className="flex items-center gap-4 sm:gap-6">
          <div className="flex items-center gap-2 shrink-0">
            <Logo size={28} />
            <span className="font-semibold text-foreground text-sm">{t("admin.brand")}</span>
          </div>
          <nav className="hidden md:flex flex-wrap gap-1 ml-4">
            {navLinks.map(({ href, key }) => (
              <Link key={href} href={href} className="text-sm text-muted-foreground hover:text-foreground px-3 py-1.5 rounded-md hover:bg-secondary transition-colors">
                {t(key)}
              </Link>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3 sm:gap-4">
            <LanguageSwitcher />
            <Link href="/" className="hidden md:inline text-xs text-muted-foreground hover:text-foreground transition-colors">{t("admin.home")}</Link>
            {user && <UserMenu user={user} />}
            <button
              type="button"
              onClick={() => setMenuOpen((o) => !o)}
              aria-label={t("admin.menu", "Menu")}
              aria-expanded={menuOpen}
              className="md:hidden inline-flex items-center justify-center w-8 h-8 rounded-md text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
            >
              {menuOpen ? <X size={18} /> : <Menu size={18} />}
            </button>
          </div>
        </div>
        {menuOpen && (
          <nav className="md:hidden flex flex-col gap-1 mt-3 pt-3 border-t border-border">
            {navLinks.map(({ href, key }) => (
              <Link key={href} href={href} onClick={() => setMenuOpen(false)} className="text-sm text-muted-foreground hover:text-foreground px-3 py-2 rounded-md hover:bg-secondary transition-colors">
                {t(key)}
              </Link>
            ))}
            <Link href="/" onClick={() => setMenuOpen(false)} className="text-sm text-muted-foreground hover:text-foreground px-3 py-2 rounded-md hover:bg-secondary transition-colors">
              {t("admin.home")}
            </Link>
          </nav>
        )}
      </header>
      <main className="flex-1 max-w-5xl w-full mx-auto px-6 py-8">{children}</main>
    </div>
  );
}
