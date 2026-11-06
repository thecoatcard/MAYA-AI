import type { Metadata } from "next";
import React from "react";

import "./globals.css";
import "../components/chat/chat.css";

export const metadata: Metadata = {
  title: "MAYA AI | Quiet Instrument",
  description: "A calm workspace for text conversations with MAYA AI.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <div className="maya-app">{children}</div>
      </body>
    </html>
  );
}
