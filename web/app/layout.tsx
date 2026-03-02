import type { Metadata } from "next";
import "./globals.css";
import "katex/dist/katex.min.css";
import AppShell from "@/components/AppShell";

export const metadata: Metadata = {
  title: "Research Pipeline",
  description: "AI-powered research paper digest",
  manifest: "/manifest.json",
  themeColor: "#1e3a5f",
  icons: {
    icon: "/icon-192.png",
    apple: "/icon-512.png",
  },
  appleWebApp: {
    capable: true,
    title: "Research Pipeline",
    statusBarStyle: "black-translucent",
  },
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
