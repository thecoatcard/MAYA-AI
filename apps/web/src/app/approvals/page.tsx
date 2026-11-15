import type { Metadata } from "next";
import React from "react";

import { LegacyRedirect } from "../../components/layout/LegacyRedirect";

export const metadata: Metadata = { title: "Moved | MAYA AI" };

/** Approvals are decided on the task card now. An old link opens Tasks (the query string stays). */
export default function ApprovalsRedirect() {
  return <LegacyRedirect to="/tasks" />;
}
