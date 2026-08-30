import type { Config } from "tailwindcss";

/**
 * Palette notes.
 *
 * Deal ratings must be readable as a scale, and they must not rely on colour
 * alone — every rating is also rendered with its label and its percentage, so
 * the badge is a shortcut rather than the message. Red/green alone would fail
 * roughly one in twelve male readers.
 */
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          DEFAULT: "#0f1419",
          soft: "#3d4852",
          muted: "#6b7684",
          faint: "#96a0ad",
        },
        surface: {
          DEFAULT: "#ffffff",
          sunken: "#f6f8fa",
          raised: "#ffffff",
          border: "#e3e8ee",
        },
        rating: {
          great: "#0f7b4f",
          good: "#3d8f5e",
          fair: "#7a7f87",
          high: "#b8722a",
          over: "#b3402f",
          suspect: "#8a3d8f",
        },
        risk: {
          low: "#0f7b4f",
          moderate: "#b8722a",
          high: "#b3402f",
          critical: "#8a1f14",
        },
        accent: "#1a5490",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      fontSize: {
        "figure-xl": ["2.75rem", { lineHeight: "1.05", letterSpacing: "-0.02em" }],
        "figure-lg": ["1.875rem", { lineHeight: "1.1", letterSpacing: "-0.015em" }],
      },
    },
  },
  plugins: [],
};

export default config;
