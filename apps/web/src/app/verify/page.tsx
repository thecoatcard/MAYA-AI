import type { Metadata } from "next";

import { AccountForm } from "../../components/auth/AccountForm";

export const metadata: Metadata = {
  title: "Verify email | MAYA AI",
};

export default function VerifyPage() {
  return <AccountForm mode="verify" />;
}
