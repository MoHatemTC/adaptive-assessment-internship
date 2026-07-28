import type { Metadata } from "next";
import "./globals.css";
import { LocaleProvider } from "@/lib/i18n";

// Inter is loaded at runtime via the @import in globals.css and Tailwind's
// `font-sans` stack (applied to <body> in globals.css). We intentionally avoid
// next/font/google here because it fetches the font at build time, which fails
// on hosts without outbound access to fonts.googleapis.com.

export const metadata: Metadata = {
  title: "Masar — AI Adaptive Assessment",
  description: "Your AI Assessment Agent",
  // Favicon comes from the app/icon.png convention (the Masar mark), which Next
  // serves with the correct basePath automatically.
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <LocaleProvider>{children}</LocaleProvider>
      </body>
    </html>
  );
}
