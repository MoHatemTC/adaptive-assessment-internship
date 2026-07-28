"use client";

import { useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import type { UserProfile } from "@/lib/types";
import { signOut } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";

interface Props {
  user: UserProfile;
}

export default function UserMenu({ user }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const router = useRouter();
  const { t } = useI18n();

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const handleSignOut = async () => {
    await signOut();
    router.push("/");
    router.refresh();
  };

  const initials = (user.full_name ?? user.email)
    .split(" ")
    .map((w) => w[0])
    .join("")
    .toUpperCase()
    .slice(0, 2);

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 px-3 py-1.5 rounded-lg hover:bg-secondary transition-colors"
      >
        {user.avatar_url ? (
          <img src={user.avatar_url} alt={user.full_name ?? ""} className="w-7 h-7 rounded-full" />
        ) : (
          <div className="w-7 h-7 rounded-full bg-primary/20 flex items-center justify-center text-primary text-xs font-bold">
            {initials}
          </div>
        )}
        <span className="text-sm font-medium text-foreground hidden sm:block">
          {user.full_name?.split(" ")[0] ?? user.email.split("@")[0]}
        </span>
        <span className="text-xs bg-primary/10 text-primary px-2 py-0.5 rounded-full font-medium">
          {user.role}
        </span>
        <svg className="w-4 h-4 text-muted-foreground" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-2 w-56 bg-card border border-border rounded-xl shadow-xl z-50 overflow-hidden animate-in fade-in slide-in-from-top-2 duration-150">
          <div className="px-4 py-3 border-b border-border">
            <p className="text-sm font-semibold text-foreground">{user.full_name ?? t("auth.userMenu.user", "User")}</p>
            <p className="text-xs text-muted-foreground truncate">{user.email}</p>
          </div>
          <div className="p-1">
            {user.role === "admin" && (
              <button
                onClick={() => { router.push("/admin"); setOpen(false); }}
                className="w-full text-left px-3 py-2 text-sm rounded-lg hover:bg-secondary transition-colors"
              >
                {t("auth.userMenu.adminPanel", "Admin Panel")}
              </button>
            )}
            {user.role === "candidate" && (
              <button
                onClick={() => { router.push("/dashboard"); setOpen(false); }}
                className="w-full text-left px-3 py-2 text-sm rounded-lg hover:bg-secondary transition-colors"
              >
                {t("auth.userMenu.myDashboard", "My Dashboard")}
              </button>
            )}
            <button
              onClick={handleSignOut}
              className="w-full text-left px-3 py-2 text-sm rounded-lg hover:bg-destructive/10 text-destructive transition-colors"
            >
              {t("auth.userMenu.signOut", "Sign out")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
