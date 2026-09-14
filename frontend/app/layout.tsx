import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "Closed-Knowledge Lab",
  description: "A learning lab for closed-knowledge AI assistants.",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en-US">
      <body>{children}</body>
    </html>
  );
}
