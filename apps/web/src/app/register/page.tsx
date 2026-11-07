import type { Metadata } from "next";
import { AuthPageLayout } from "../../components/auth/AuthPageLayout";
import { AuthView } from "../../components/auth/AuthView";

export const metadata: Metadata = {
  title: "Create account | MAYA AI",
};

export default function RegisterPage() {
  return (
    <AuthPageLayout>
      <AuthView initialStep="signup_details" />
    </AuthPageLayout>
  );
}
