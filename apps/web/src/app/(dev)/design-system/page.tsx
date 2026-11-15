import { notFound } from "next/navigation";
import React from "react";

import { DesignSystemShowcase } from "../../../components/ui/DesignSystemShowcase";

/**
 * QA showcase of every token and primitive. Not linked from the product. It only renders in
 * development (`next dev`); every production build, including the desktop export, answers 404
 * for this path.
 */
export default function DesignSystemPage(): React.JSX.Element {
  if (process.env.NODE_ENV === "production") {
    notFound();
  }
  return <DesignSystemShowcase />;
}
