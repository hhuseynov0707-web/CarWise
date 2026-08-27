import type { Metadata, Viewport } from "next";
import "./globals.css";

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
  return (
    <html lang="en">
      <body>
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4
                     focus:z-50 focus:rounded focus:bg-ink focus:px-4 focus:py-2
                     focus:text-sm focus:text-white"
        >
          Skip to content
        </a>
        {children}
      </body>
    </html>
  );
}
