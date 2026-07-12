import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "./providers";
import { themeInitScript } from "@/components/ThemeToggle";

export const metadata: Metadata = {
  title: "MootLoop — Matter Cockpit",
  description: "The attorney's command surface for MootLoop runs, decisions, and attestation.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Apply the stored/OS theme before paint to avoid a flash (FD-9 both-themes). */}
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body className="font-serif text-ink antialiased">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:bg-accent focus:px-4 focus:py-2 focus:text-paper"
        >
          Skip to content
        </a>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
