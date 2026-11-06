import type { Metadata } from "next";

import { SignInForm } from "../../components/auth/SignInForm";

export const metadata: Metadata = {
  title: "Sign in | MAYA AI",
};

export default function LoginPage() {
  return <SignInForm />;
}
