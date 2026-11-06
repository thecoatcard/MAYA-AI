import type { Metadata } from "next";

import { AccountForm } from "../../components/auth/AccountForm";

export const metadata: Metadata = {
  title: "Reset password | MAYA AI",
};

export default function ResetPasswordPage() {
  return <AccountForm mode="reset" />;
}
