import type { Metadata } from "next";
import React from "react";

import { AmbientWorkspace } from "../../components/chat/AmbientWorkspace";

export const metadata: Metadata = {
  title: "Ambient Workspace | MAYA AI",
  description:
    "Voice-first digital employee ambient workspace with real-time deliverable canvas.",
};

export default function WorkspacePage() {
  return <AmbientWorkspace />;
}
