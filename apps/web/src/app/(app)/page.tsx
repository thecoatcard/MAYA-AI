import type { Metadata } from "next";
import React from "react";

import { ConversationView } from "../../components/chat/ConversationView";

export const metadata: Metadata = {
  title: "Conversation | MAYA AI",
  description: "Talk or type to MAYA.",
};

export default function ConversationPage() {
  return <ConversationView />;
}
