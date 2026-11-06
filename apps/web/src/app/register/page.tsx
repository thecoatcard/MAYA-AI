import type { Metadata } from "next";

import { UnifiedAuthForm } from "../../components/auth/UnifiedAuthForm";

export const metadata: Metadata = {
  title: "Create account | MAYA AI",
};

export default function RegisterPage() {
  return <UnifiedAuthForm />;
}
