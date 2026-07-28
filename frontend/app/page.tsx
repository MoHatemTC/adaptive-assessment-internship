"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Menu, X, FilePlus, Users, Database, Settings } from "lucide-react";
import { getCurrentUser } from "@/lib/auth";
import LoginButton from "@/components/auth/LoginButton";
import UserMenu from "@/components/auth/UserMenu";
import LanguageSwitcher from "@/components/LanguageSwitcher";
import Logo from "@/components/Logo";
import { useI18n } from "@/lib/i18n";
import type { UserProfile } from "@/lib/types";

const FEATURES = [
  {
    id: "adaptive",
    icon: "🧠",
    title: "Adaptive Intelligence",
    description: "Questions evolve based on your answers. We dig deeper into gaps and skip what you already know.",
  },
  {
    id: "proctoring",
    icon: "📹",
    title: "Integrity Monitoring",
    description: "Fair, continuous integrity checks with AI camera analysis and per-assessment analytics.",
  },
  {
    id: "formats",
    icon: "⚡",
    title: "6 Question Formats",
    description: "MCQ, live voice interviews, live video interviews, coding challenges, real tasks, and data-visualization analysis.",
  },
  {
    id: "cv",
    icon: "📊",
    title: "Personalized to You",
    description: "Optionally add your CV and our AI tailors every question to your background — no CV required.",
  },
  {
    id: "memory",
    icon: "🗺️",
    title: "Memory-Driven Adaptation",
    description: "Memory cards track what you know vs. gaps. No wasted time revisiting mastered topics.",
  },
  {
    id: "report",
    icon: "📧",
    title: "Detailed Report",
    description: "A full skill breakdown, placement, and personalized recommendations — emailed after completion.",
  },
];

const SKILLS = [
  { id: "critical_thinking", name: "Critical Thinking", color: "bg-blue-500" },
  { id: "soft_skills", name: "Soft Skills", color: "bg-purple-500" },
  { id: "work_readiness", name: "Work Readiness", color: "bg-green-500" },
  { id: "digital_ai", name: "Digital & AI", color: "bg-orange-500" },
  { id: "growth_mindset", name: "Growth Mindset", color: "bg-pink-500" },
];

export default function LandingPage() {
  const [user, setUser] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [mobileOpen, setMobileOpen] = useState(false);
  const { t } = useI18n();

  const isAdmin = user?.role === "admin";

  useEffect(() => {
    getCurrentUser().then((u) => {
      setUser(u);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  return (
    <div className="min-h-screen bg-background relative overflow-x-hidden">
      {/* Dot grid background */}
      <div className="absolute inset-0 bg-dot-grid opacity-40 pointer-events-none" />
      <div className="absolute top-0 -left-64 w-[500px] h-[500px] rounded-full bg-primary/10 blur-3xl pointer-events-none" />
      <div className="absolute top-1/3 -right-64 w-[500px] h-[500px] rounded-full bg-primary/8 blur-3xl pointer-events-none" />

      {/* ── Nav bar ───────────────────────────────────── */}
      <nav className="relative z-20 border-b border-border/60 bg-background/80 backdrop-blur-sm">
        <div className="max-w-6xl mx-auto px-6 py-4 flex items-center gap-6">
          {/* Logo */}
          <Link href="/" className="flex-shrink-0">
            <Logo size={32} wordmark />
          </Link>

          {/* Nav links (marketing sections — hidden for admins) */}
          {!isAdmin && (
            <div className="hidden md:flex items-center gap-6 flex-1">
              <a href="#how-it-works" className="text-sm text-muted-foreground hover:text-foreground transition-colors">{t("nav.howItWorks")}</a>
              <a href="#features" className="text-sm text-muted-foreground hover:text-foreground transition-colors">{t("nav.features")}</a>
              <a href="#skills" className="text-sm text-muted-foreground hover:text-foreground transition-colors">{t("nav.skills")}</a>
            </div>
          )}

          {/* Auth */}
          <div className="ml-auto flex items-center gap-3">
            <LanguageSwitcher />
            {loading ? (
              <div className="w-20 h-8 rounded-lg bg-secondary animate-pulse" />
            ) : user ? (
              <>
                {user.role === "admin" && (
                  <Link href="/admin" className="text-sm text-muted-foreground hover:text-foreground transition-colors hidden sm:block">
                    {t("landing.nav_admin_panel", "Admin Panel")}
                  </Link>
                )}
                {user.role === "candidate" && (
                  <Link href="/dashboard" className="text-sm text-muted-foreground hover:text-foreground transition-colors hidden sm:block">
                    {t("landing.nav_dashboard", "Dashboard")}
                  </Link>
                )}
                <UserMenu user={user} />
              </>
            ) : (
              <LoginButton variant="nav" />
            )}

            {/* Mobile menu toggle */}
            <button
              type="button"
              onClick={() => setMobileOpen((o) => !o)}
              aria-label={t("landing.nav_menu", "Menu")}
              aria-expanded={mobileOpen}
              className="md:hidden inline-flex items-center justify-center w-9 h-9 rounded-lg border border-border text-foreground hover:bg-secondary transition-colors"
            >
              {mobileOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
            </button>
          </div>
        </div>

        {/* Mobile dropdown */}
        {mobileOpen && (
          <div className="md:hidden border-t border-border/60 bg-background/95 backdrop-blur-sm">
            <div className="max-w-6xl mx-auto px-6 py-3 flex flex-col">
              {!isAdmin && (
                <>
                  <a href="#how-it-works" onClick={() => setMobileOpen(false)} className="py-2.5 text-sm text-muted-foreground hover:text-foreground transition-colors">{t("nav.howItWorks")}</a>
                  <a href="#features" onClick={() => setMobileOpen(false)} className="py-2.5 text-sm text-muted-foreground hover:text-foreground transition-colors">{t("nav.features")}</a>
                  <a href="#skills" onClick={() => setMobileOpen(false)} className="py-2.5 text-sm text-muted-foreground hover:text-foreground transition-colors">{t("nav.skills")}</a>
                </>
              )}
              {user?.role === "admin" && (
                <Link href="/admin" onClick={() => setMobileOpen(false)} className="py-2.5 text-sm text-muted-foreground hover:text-foreground transition-colors">{t("landing.nav_admin_panel", "Admin Panel")}</Link>
              )}
              {user?.role === "candidate" && (
                <Link href="/dashboard" onClick={() => setMobileOpen(false)} className="py-2.5 text-sm text-muted-foreground hover:text-foreground transition-colors">{t("landing.nav_dashboard", "Dashboard")}</Link>
              )}
              {!user && !loading && (
                <div className="pt-2">
                  <LoginButton variant="nav" />
                </div>
              )}
            </div>
          </div>
        )}
      </nav>

      {isAdmin ? (
        /* ── Admin welcome panel ─────────────────────── */
        <section className="relative z-10 max-w-5xl mx-auto px-6 pt-24 pb-20 flex flex-col gap-8">
          <div className="flex flex-col gap-4">
            <div className="inline-flex items-center gap-2 self-start px-3 py-1 rounded-full border border-primary/30 bg-primary/5 text-primary text-xs font-medium">
              <span className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />
              {t("landing.admin_badge", "Admin workspace")}
            </div>
            <h1 className="text-3xl sm:text-4xl md:text-5xl font-extrabold text-foreground tracking-tight leading-tight">
              {t("landing.admin_welcome", "Welcome back")}{user?.full_name ? `, ${user.full_name}` : ""}
            </h1>
            <p className="text-lg text-muted-foreground leading-relaxed max-w-2xl">
              {t("landing.admin_subtitle", "Manage assessments, review candidates, and configure your Masar workspace.")}
            </p>
          </div>

          {/* Quick links */}
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-4">
            {[
              { href: "/admin/assessment/new", icon: FilePlus, key: "admin_ql_new", title: "New Assessment", desc: "Create an assessment" },
              { href: "/admin/sessions", icon: Users, key: "admin_ql_candidates", title: "Candidates", desc: "Review sessions & results" },
              { href: "/admin/question-bank", icon: Database, key: "admin_ql_qbank", title: "Question Bank", desc: "Manage the question library" },
              { href: "/admin", icon: Settings, key: "admin_ql_settings", title: "Settings", desc: "Configure your workspace" },
            ].map((c) => {
              const Icon = c.icon;
              return (
                <Link
                  key={c.href}
                  href={c.href}
                  className="group flex flex-col gap-3 p-5 rounded-2xl border border-border bg-card hover:border-primary/50 hover:bg-secondary/40 transition-all"
                >
                  <div className="w-10 h-10 rounded-xl flex items-center justify-center bg-primary/10 text-primary group-hover:bg-primary/15 transition-colors">
                    <Icon className="w-5 h-5" />
                  </div>
                  <div>
                    <p className="text-sm font-semibold text-foreground">{t("landing." + c.key + "_title", c.title)}</p>
                    <p className="text-xs text-muted-foreground mt-0.5">{t("landing." + c.key + "_desc", c.desc)}</p>
                  </div>
                </Link>
              );
            })}
          </div>

          <div className="flex flex-col sm:flex-row gap-4 items-start">
            <Link
              href="/admin"
              className="inline-flex items-center gap-2 px-8 py-4 rounded-xl bg-primary text-white font-semibold text-base hover:bg-primary/90 transition-all hover:shadow-lg hover:shadow-primary/20"
            >
              {t("landing.hero_open_admin", "Open Admin Panel")} →
            </Link>
          </div>
        </section>
      ) : (
      <>
      {/* ── Hero ──────────────────────────────────────── */}
      <section className="relative z-10 max-w-5xl mx-auto px-6 pt-24 pb-20 flex flex-col items-center text-center gap-8">
        {/* Pulsing orb */}
        <div className="agent-orb-pulse">
          <Logo size={96} />
        </div>

        <div className="flex flex-col gap-4 max-w-3xl">
          <div className="inline-flex items-center gap-2 self-center px-3 py-1 rounded-full border border-primary/30 bg-primary/5 text-primary text-xs font-medium">
            <span className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />
            {t("hero.badge")}
          </div>
          <h1 className="text-4xl sm:text-5xl md:text-6xl font-extrabold text-foreground tracking-tight leading-tight">
            {t("hero.titleA")}<br />
            <span className="text-primary">{t("hero.titleB")}</span>
          </h1>
          <p className="text-lg text-muted-foreground leading-relaxed max-w-2xl mx-auto">
            {t("hero.subtitle")}
          </p>
        </div>

        <div className="flex flex-col sm:flex-row gap-4 items-center">
          {user ? (
            user.role === "admin" ? (
              <Link
                href="/admin"
                className="inline-flex items-center gap-2 px-8 py-4 rounded-xl bg-primary text-white font-semibold text-base hover:bg-primary/90 transition-all hover:shadow-lg hover:shadow-primary/20"
              >
                {t("landing.hero_open_admin", "Open Admin Panel")} →
              </Link>
            ) : (
              <Link
                href="/onboarding"
                className="inline-flex items-center gap-2 px-8 py-4 rounded-xl bg-primary text-white font-semibold text-base hover:bg-primary/90 transition-all hover:shadow-lg hover:shadow-primary/20"
              >
                {t("landing.get_started", "Get Started")} →
              </Link>
            )
          ) : (
            <LoginButton variant="hero" />
          )}
          <a
            href="#how-it-works"
            className="inline-flex items-center gap-2 px-8 py-4 rounded-xl border border-border text-foreground font-medium text-base hover:bg-secondary transition-colors"
          >
            {t("landing.hero_secondary", "How it works")}
          </a>
        </div>

        {/* Skill pills */}
        <div className="flex flex-wrap gap-2 justify-center mt-2">
          {SKILLS.map((s) => (
            <span key={s.id} className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-secondary border border-border text-xs font-medium text-foreground">
              <span className={`w-2 h-2 rounded-full ${s.color}`} />
              {t("landing.skill_" + s.id + "_name", s.name)}
            </span>
          ))}
        </div>
      </section>

      {/* ── How it works ──────────────────────────────── */}
      <section id="how-it-works" className="relative z-10 max-w-5xl mx-auto px-6 py-20">
        <div className="text-center mb-12">
          <h2 className="text-3xl font-bold text-foreground">{t("landing.how_title", "How Masar Works")}</h2>
          <p className="text-muted-foreground mt-2">{t("landing.how_subtitle", "From login to placement in under an hour")}</p>
        </div>

        <div className="grid md:grid-cols-4 gap-6">
          {[
            { id: "login_cv", step: "01", title: "Sign In & Start", desc: "Sign in with Google and pick an assessment. Optionally add your CV to personalize." },
            { id: "planning", step: "02", title: "Smart Planning", desc: "Our AI reads the assessment setup (and your CV if provided) to build a personalized plan." },
            { id: "assessment", step: "03", title: "Adaptive Assessment", desc: "Multiple assessment types and 6 question formats, adaptive difficulty, integrity monitoring." },
            { id: "report", step: "04", title: "Detailed Report", desc: "A full competency breakdown, placement, and personalized recommendations — emailed to you." },
          ].map((item) => (
            <div key={item.step} className="relative bg-card border border-border rounded-2xl p-6 flex flex-col gap-3">
              <div className="text-4xl font-black text-primary/15">{item.step}</div>
              <h3 className="text-base font-semibold text-foreground">{t("landing.step_" + item.id + "_title", item.title)}</h3>
              <p className="text-sm text-muted-foreground leading-relaxed">{t("landing.step_" + item.id + "_desc", item.desc)}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ── Features ──────────────────────────────────── */}
      <section id="features" className="relative z-10 max-w-5xl mx-auto px-6 py-20">
        <div className="text-center mb-12">
          <h2 className="text-3xl font-bold text-foreground">{t("landing.features_title", "Built for Serious Assessment")}</h2>
          <p className="text-muted-foreground mt-2">{t("landing.features_subtitle", "Every feature designed for accuracy and engagement")}</p>
        </div>

        <div className="grid md:grid-cols-3 gap-5">
          {FEATURES.map((f) => (
            <div key={f.id} className="bg-card border border-border rounded-2xl p-6 flex flex-col gap-3 hover:border-primary/30 transition-colors">
              <span className="text-3xl">{f.icon}</span>
              <h3 className="text-base font-semibold text-foreground">{t("landing.feat_" + f.id + "_title", f.title)}</h3>
              <p className="text-sm text-muted-foreground leading-relaxed">{t("landing.feat_" + f.id + "_desc", f.description)}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ── Skills section ────────────────────────────── */}
      <section id="skills" className="relative z-10 max-w-5xl mx-auto px-6 py-20">
        <div className="text-center mb-12">
          <h2 className="text-3xl font-bold text-foreground">{t("landing.skills_title", "5 Dimensions of Excellence")}</h2>
          <p className="text-muted-foreground mt-2">{t("landing.skills_subtitle", "Scored 0–5 each, max total 25 points")}</p>
        </div>

        <div className="grid md:grid-cols-5 gap-4">
          {[
            { id: "thinking", skill: "Thinking", desc: "Critical reasoning, problem-solving, analytical depth", color: "from-blue-500/20 to-blue-500/5", border: "border-blue-500/30", text: "text-blue-600" },
            { id: "soft", skill: "Soft", desc: "Communication, empathy, teamwork, leadership potential", color: "from-purple-500/20 to-purple-500/5", border: "border-purple-500/30", text: "text-purple-600" },
            { id: "work", skill: "Work", desc: "Execution quality, delivery focus, professional readiness", color: "from-green-500/20 to-green-500/5", border: "border-green-500/30", text: "text-green-600" },
            { id: "digital", skill: "Digital & AI", desc: "Tech fluency, AI tools mastery, digital literacy", color: "from-orange-500/20 to-orange-500/5", border: "border-orange-500/30", text: "text-orange-600" },
            { id: "growth", skill: "Growth", desc: "Learning agility, curiosity, adaptability under pressure", color: "from-pink-500/20 to-pink-500/5", border: "border-pink-500/30", text: "text-pink-600" },
          ].map((s) => (
            <div key={s.id} className={`bg-gradient-to-b ${s.color} border ${s.border} rounded-2xl p-5 flex flex-col gap-2`}>
              <p className={`text-sm font-bold ${s.text}`}>{t("landing.dim_" + s.id + "_name", s.skill)}</p>
              <p className="text-xs text-muted-foreground leading-relaxed">{t("landing.dim_" + s.id + "_desc", s.desc)}</p>
            </div>
          ))}
        </div>

        <div className="mt-8 bg-card border border-border rounded-2xl p-6 flex flex-col md:flex-row items-center gap-4">
          <div className="flex-1">
            <p className="text-sm font-semibold text-foreground">{t("landing.placement_title", "Placement Decision")}</p>
            <p className="text-xs text-muted-foreground mt-1">{t("landing.placement_rule_a", "Total ≥ 18 AND Thinking ≥ 3 →")} <span className="text-primary font-semibold">PRO</span> {t("landing.placement_rule_b", "· Otherwise →")} <span className="text-muted-foreground font-semibold">BEGINNER</span></p>
          </div>
          {!user && <LoginButton variant="nav" />}
        </div>
      </section>

      {/* ── Get started (generic entry) ─────────────────── */}
      <section id="start" className="relative z-10 max-w-3xl mx-auto px-6 py-20 text-center">
        <h2 className="text-3xl font-bold text-foreground">{t("landing.start_title", "Ready to begin?")}</h2>
        <p className="text-muted-foreground mt-2 mb-8">{t("landing.start_subtitle", "Start your adaptive assessment, or let Masar guide you to your best-fit path.")}</p>
        <div className="flex flex-col sm:flex-row gap-3 justify-center items-center">
          {user?.role === "candidate" ? (
            <>
              <Link href="/onboarding" className="inline-flex items-center gap-2 px-8 py-4 rounded-xl bg-primary text-white font-semibold text-base hover:bg-primary/90 transition-all hover:shadow-lg hover:shadow-primary/20">
                {t("landing.start_cta", "Start assessment")} →
              </Link>
              <Link href="/onboarding?track=discover" className="inline-flex items-center gap-2 px-8 py-4 rounded-xl border border-border text-foreground font-medium text-base hover:bg-secondary transition-colors">
                {t("landing.discover_cta", "Discover my path")}
              </Link>
            </>
          ) : !user ? (
            <LoginButton variant="hero" />
          ) : null}
        </div>
      </section>

      </>
      )}

      {/* ── Footer ────────────────────────────────────── */}
      <footer className="relative z-10 border-t border-border/60 bg-background/60 backdrop-blur-sm">
        <div className="max-w-5xl mx-auto px-6 py-8 flex flex-col md:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <Logo size={24} wordmark />
            <span className="text-xs text-muted-foreground">{t("landing.footer_tagline", "· AI Adaptive Assessment")}</span>
          </div>
          <p className="text-xs text-muted-foreground">{t("landing.footer_powered", "Built by Sprints.ai")}</p>
        </div>
      </footer>
    </div>
  );
}
