import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "Scripto",
  description: "Podcast guest research and interview scripts, every line sourced.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <Providers>
          <div className="min-h-screen">
            <header className="border-b border-black/10 bg-white/70 backdrop-blur">
              <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
                <Link href="/" className="text-lg font-semibold tracking-tight">
                  Scripto
                </Link>
                <span className="text-xs text-black/50">
                  research → dossier → script
                </span>
              </div>
            </header>
            <main className="mx-auto max-w-5xl px-6 py-10">{children}</main>
          </div>
        </Providers>
      </body>
    </html>
  );
}
