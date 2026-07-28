"use client";

import { supabase } from "./supabaseClient";
import type { UserProfile } from "./types";

export async function signInWithGoogle() {
  const { error } = await supabase.auth.signInWithOAuth({
    provider: "google",
    options: {
      // On the deployed app the callback lives under the /masar basePath, which
      // window.location.origin (scheme+host only) omits. NEXT_PUBLIC_SITE_URL
      // carries the full origin+basePath; falls back to origin for local dev.
      redirectTo: `${process.env.NEXT_PUBLIC_SITE_URL ?? window.location.origin}/auth/callback`,
    },
  });
  if (error) throw error;
}

export async function signOut() {
  await supabase.auth.signOut();
}

export async function getCurrentUser(): Promise<UserProfile | null> {
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) return null;

  const email = user.email ?? "";
  const role = email.endsWith("@sprints.ai") ? "admin" : "candidate";

  return {
    id: user.id,
    role,
    full_name: user.user_metadata?.full_name ?? null,
    avatar_url: user.user_metadata?.avatar_url ?? null,
    email,
  };
}

export function isAdmin(user: UserProfile | null): boolean {
  return user?.role === "admin";
}
