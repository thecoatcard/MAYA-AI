import type { Metadata } from "next";
import React from "react";

import { ChatWorkspace } from "../../components/chat/ChatWorkspace";

export const metadata: Metadata = {
  title: "Chat | MAYA AI",
  description:
    "Text chat with MAYA AI. Requires sign-in, which is not built yet.",
};

/** Prerendered statically; the page never calls the API at build time. */
export default function ChatPage() {
  return <ChatWorkspace />;
}
