import type { Metadata } from "next";
import { AccountForm } from "../../components/auth/AccountForm";
import { AuthPageLayout } from "../../components/auth/AuthPageLayout";

export const metadata: Metadata = {
  title: "Verify email | MAYA AI",
};

export default function VerifyPage() {
  return (
    <AuthPageLayout>
      <AccountForm mode="verify" embedded />
    </AuthPageLayout>
  );
}
