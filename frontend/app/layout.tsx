import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "LineupOpt — DraftKings NFL DFS",
  description: "Projection, simulation & lineup optimization platform",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-surface text-slate-100 antialiased">{children}</body>
    </html>
  );
}
