import type { Metadata } from "next";
import { AccountForm } from "../../components/auth/AccountForm";
import { AuthPageLayout } from "../../components/auth/AuthPageLayout";

export const metadata: Metadata = {
  title: "Forgot password | MAYA AI",
};

export default function ForgotPasswordPage() {
  return (
    <AuthPageLayout>
      <AccountForm mode="forgot" embedded />
    </AuthPageLayout>
  );
}
