import type { Metadata } from "next";

import { UnifiedAuthForm } from "../../components/auth/UnifiedAuthForm";

export const metadata: Metadata = {
  title: "Sign in | MAYA AI",
};

export default function LoginPage() {
  return <UnifiedAuthForm />;
}
