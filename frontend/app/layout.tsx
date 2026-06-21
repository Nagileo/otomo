import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Otomo · 番组搭子",
  description: "ACGN Knowledge-Graph Agent",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh">
      <body>{children}</body>
    </html>
  );
}
