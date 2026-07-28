"use client";

/** Masar logo — the brand Arabic-calligraphy mark (blue, transparent PNG) plus an
 *  optional theme-aware wordmark. The mark reads on both light and dark themes;
 *  the wordmark stays as foreground-colored text so it adapts to the theme.
 *  Asset lives at public/logo-mark.png → served under the /masar basePath. */
export default function Logo({ size = 32, wordmark = false, className = "" }: { size?: number; wordmark?: boolean; className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2.5 ${className}`}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src="/masar/logo-mark.png"
        alt="Masar"
        width={size}
        height={size}
        style={{ width: size, height: size, objectFit: "contain" }}
      />
      {wordmark && (
        <span className="font-bold tracking-tight text-foreground" style={{ fontSize: Math.round(size * 0.52) }}>Masar</span>
      )}
    </span>
  );
}
