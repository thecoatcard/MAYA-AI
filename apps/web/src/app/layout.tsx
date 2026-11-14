import type { Metadata } from "next";
import React from "react";

import "./globals.css";
import "./ui-surfaces.css";
import "../components/chat/chat.css";
import { AuthProvider } from "../hooks/useAuth";
import { DEFAULT_THEME, THEME_BOOTSTRAP_SCRIPT } from "../lib/theme";

export const metadata: Metadata = {
  title: "MAYA AI",
  description: "A voice-first digital employee that asks before it acts.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    // data-theme is rendered as dark and corrected before first paint by the inline script below
    // (from the per-device choice), so suppressHydrationWarning covers that one attribute.
    <html lang="en" data-theme={DEFAULT_THEME} suppressHydrationWarning>
      <head>
        <script>{THEME_BOOTSTRAP_SCRIPT}</script>
      </head>
      <body>
        <div className="maya-app">
          <AuthProvider>{children}</AuthProvider>
        </div>
      </body>
    </html>
  );
}
