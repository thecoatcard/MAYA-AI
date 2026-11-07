import type { Metadata } from "next";
import React from "react";

import { ApprovalCenterView } from "../../components/approvals/ApprovalCenterView";
import { AppShell } from "../../components/layout/AppShell";

export const metadata: Metadata = {
  title: "Approval Center | MAYA AI",
  description:
    "Security governance dashboard for sensitive action authorizations with single-use cryptographic tokens.",
};

export default function ApprovalsPage() {
  return (
    <AppShell title="Approval Center" breadcrumb="Governance">
      <ApprovalCenterView />
    </AppShell>
  );
}
