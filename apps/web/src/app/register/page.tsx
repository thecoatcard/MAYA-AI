import type { Metadata } from "next";

import { AccountForm } from "../../components/auth/AccountForm";

export const metadata: Metadata = {
  title: "Create account | MAYA AI",
};

export default function RegisterPage() {
  return <AccountForm mode="register" />;
}
