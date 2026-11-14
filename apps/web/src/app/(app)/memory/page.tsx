import type { Metadata } from "next";
import React from "react";

import { MemoryView } from "../../../components/memory/MemoryView";

export const metadata: Metadata = {
  title: "Memory | MAYA AI",
  description: "What MAYA remembers, with sources.",
};

export default function MemoryPage() {
  return <MemoryView />;
}
