import type { Metadata, Viewport } from "next";
import { Fira_Code, Fira_Sans } from "next/font/google";
import { LocaleProvider } from "@/lib/locale";
import { SessionProvider } from "@/lib/session";
import "./globals.css";

/**
 * Fira Sans for text, Fira Code for figures.
 *
 * The report is read down columns of prices and mileages, and Fira Code's
 * tabular digits keep those columns aligned. Loaded through next/font so the
 * files are self-hosted and the layout does not shift when they arrive.
 */
const firaSans = Fira_Sans({
  subsets: ["latin", "latin-ext", "cyrillic"],
  weight: ["300", "400", "500", "600", "700"],
  variable: "--font-sans",
  display: "swap",
});

const firaCode = Fira_Code({
  subsets: ["latin", "latin-ext", "cyrillic"],
  weight: ["400", "500", "600"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "AutoIntel Azerbaijan — Vehicle Market Intelligence",
  description:
    "Know the car. Know the market. Decide yourself. Evidence-based valuation, " +
    "risk analysis and purchase due diligence for the Azerbaijani used-car market.",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#0f1419",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  // lang is corrected on the client once the stored locale is known; Azerbaijani
  // is the default because the market is Azerbaijani.
  return (
    <html lang="az" className={`${firaSans.variable} ${firaCode.variable}`}>
      <body>
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4
                     focus:z-50 focus:rounded focus:bg-ink focus:px-4 focus:py-2
                     focus:text-sm focus:text-white"
        >
          Skip to content
        </a>
        <LocaleProvider>
          <SessionProvider>{children}</SessionProvider>
        </LocaleProvider>
      </body>
    </html>
  );
}
