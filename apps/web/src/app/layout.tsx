import type { Metadata } from "next";
import React from "react";

import "./globals.css";
import "../components/chat/chat.css";
import { AuthProvider } from "../hooks/useAuth";

export const metadata: Metadata = {
  title: "MAYA AI | Autonomous Digital Employee",
  description:
    "A voice-first digital employee that asks before it acts. Engineered with cryptographic guardrails.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <div className="maya-app">
          <AuthProvider>{children}</AuthProvider>
        </div>
      </body>
    </html>
  );
}
