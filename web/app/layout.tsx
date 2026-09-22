import type { Metadata } from "next";
import { Public_Sans, Source_Serif_4 } from "next/font/google";
import Link from "next/link";
import "./globals.css";
import { Providers } from "./providers";
import { ThemeToggle } from "@/components/ThemeToggle";

const sans = Public_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-sans",
  display: "swap",
});

const serif = Source_Serif_4({
  subsets: ["latin"],
  weight: ["500", "600", "700"],
  variable: "--font-serif",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Scripto",
  description: "Podcast guest research and interview scripts, every line sourced.",
};

// Applies the persisted/system theme before first paint, so the page never
// flashes light and then snaps to dark (layout.tsx is a server component and
// can't read localStorage itself -- this runs inline, ahead of hydration).
const THEME_INIT_SCRIPT = `(function(){try{var t=localStorage.getItem("scripto-theme");if(!t){t=window.matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light";}if(t==="dark"){document.documentElement.classList.add("dark");}}catch(e){}})();`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="en"
      className={`${sans.variable} ${serif.variable}`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body>
        <Providers>
          <div className="min-h-screen">
            <header className="border-b border-line bg-surface/80 backdrop-blur">
              <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
                <Link
                  href="/"
                  className="font-serif text-lg font-semibold tracking-tight"
                >
                  Scripto
                </Link>
                <div className="flex items-center gap-3">
                  <span className="hidden text-caption text-ink-subtle sm:inline">
                    research → dossier → script
                  </span>
                  <ThemeToggle />
                </div>
              </div>
            </header>
            <main className="mx-auto max-w-6xl px-6 py-10">{children}</main>
          </div>
        </Providers>
      </body>
    </html>
  );
}
