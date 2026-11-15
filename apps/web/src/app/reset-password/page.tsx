import type { Metadata } from "next";
import { AccountForm } from "../../components/auth/AccountForm";
import { AuthPageLayout } from "../../components/auth/AuthPageLayout";

export const metadata: Metadata = {
  title: "Reset password | MAYA AI",
};

export default function ResetPasswordPage() {
  return (
    <AuthPageLayout>
      <AccountForm mode="reset" embedded />
    </AuthPageLayout>
  );
}
