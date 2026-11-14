import React from "react";

import { ConversationRuntime } from "../../components/chat/ConversationRuntime";
import { AppGate } from "../../components/layout/AppGate";
import { AppShell } from "../../components/layout/AppShell";
import { WorkspaceProvider } from "../../components/layout/WorkspaceProvider";

/**
 * One persistent shell for every in-app route. The provider, gate, conversation
 * runtime (chat + voice) and shell live in this layout, so they stay mounted
 * while the page below changes.
 */
export default function AppLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <WorkspaceProvider>
      <AppGate>
        <ConversationRuntime>
          <AppShell>{children}</AppShell>
        </ConversationRuntime>
      </AppGate>
    </WorkspaceProvider>
  );
}
