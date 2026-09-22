import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          DEFAULT: "var(--ink)",
          muted: "var(--ink-muted)",
          subtle: "var(--ink-subtle)",
        },
        paper: "var(--paper)",
        surface: {
          DEFAULT: "var(--surface)",
          muted: "var(--surface-muted)",
        },
        line: {
          DEFAULT: "var(--border)",
          strong: "var(--border-strong)",
        },
        accent: {
          DEFAULT: "var(--accent)",
          contrast: "var(--accent-contrast)",
        },
        status: {
          success: {
            DEFAULT: "var(--status-success-text)",
            bg: "var(--status-success-bg)",
            border: "var(--status-success-border)",
          },
          info: {
            DEFAULT: "var(--status-info-text)",
            bg: "var(--status-info-bg)",
            border: "var(--status-info-border)",
          },
          warning: {
            DEFAULT: "var(--status-warning-text)",
            bg: "var(--status-warning-bg)",
            border: "var(--status-warning-border)",
          },
          danger: {
            DEFAULT: "var(--status-danger-text)",
            bg: "var(--status-danger-bg)",
            border: "var(--status-danger-border)",
          },
        },
      },
      fontFamily: {
        serif: ["var(--font-serif)", "Georgia", "serif"],
        sans: [
          "var(--font-sans)",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "sans-serif",
        ],
      },
      fontSize: {
        display: ["1.75rem", { lineHeight: "1.15", letterSpacing: "-0.01em" }],
        heading: ["1rem", { lineHeight: "1.3", letterSpacing: "-0.005em" }],
        body: ["0.875rem", { lineHeight: "1.65" }],
        ui: ["0.8125rem", { lineHeight: "1.3" }],
        caption: ["0.75rem", { lineHeight: "1.45" }],
      },
      borderRadius: {
        sm: "var(--radius-sm)",
        md: "var(--radius-md)",
        lg: "var(--radius-lg)",
      },
      boxShadow: {
        raised: "var(--shadow-raised)",
        overlay: "var(--shadow-overlay)",
      },
    },
  },
  plugins: [],
};
export default config;
