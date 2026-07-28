import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * Absolute, shareable public URL for an app path — includes the /masar basePath.
 * window.location.origin is scheme+host only and drops the basePath, so prefer
 * NEXT_PUBLIC_SITE_URL (e.g. https://learning-rnd.sprints.ai/masar).
 */
export function publicUrl(path: string): string {
  const base =
    process.env.NEXT_PUBLIC_SITE_URL ??
    (typeof window !== "undefined" ? window.location.origin : "");
  return `${base}${path}`;
}
