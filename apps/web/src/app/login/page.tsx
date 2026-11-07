import type { Metadata } from "next";
import { AuthPageLayout } from "../../components/auth/AuthPageLayout";
import { AuthView } from "../../components/auth/AuthView";

export const metadata: Metadata = {
  title: "Sign in | MAYA AI",
};

export default function LoginPage() {
  return (
    <AuthPageLayout>
      <AuthView initialStep="password" />
    </AuthPageLayout>
  );
}
