import type { Metadata } from "next";

import { AccountForm } from "../../components/auth/AccountForm";

export const metadata: Metadata = {
  title: "Forgot password | MAYA AI",
};

export default function ForgotPasswordPage() {
  return <AccountForm mode="forgot" />;
}
