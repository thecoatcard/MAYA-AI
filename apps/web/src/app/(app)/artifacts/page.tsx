import type { Metadata } from "next";
import React from "react";

import { ArtifactsManagerView } from "../../../components/artifacts/ArtifactsManagerView";

export const metadata: Metadata = {
  title: "Artifacts | MAYA AI",
  description: "Documents, spreadsheets and other deliverables MAYA produced.",
};

export default function ArtifactsPage() {
  return <ArtifactsManagerView />;
}
