import type { Metadata } from "next";
import "./globals.css";
import "katex/dist/katex.min.css";
import AppShell from "@/components/AppShell";

export const metadata: Metadata = {
  title: "Research Pipeline",
  description: "AI-powered research paper digest",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="antialiased">
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
