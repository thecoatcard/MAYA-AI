import type { Metadata } from "next";
import React from "react";

import { LegacyRedirect } from "../../components/layout/LegacyRedirect";

export const metadata: Metadata = { title: "Moved | MAYA AI" };

export default function WorkspaceRedirect() {
  return <LegacyRedirect to="/" />;
}
