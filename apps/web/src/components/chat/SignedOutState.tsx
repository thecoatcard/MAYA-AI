import { KeyRound } from "lucide-react";
import Link from "next/link";

export function SignedOutState({
  logoutWarning,
}: {
  logoutWarning?: string | null;
}) {
  return (
    <section className="maya-signedout" aria-labelledby="maya-signedout-title">
      <div className="maya-signedout__icon" aria-hidden="true">
        <KeyRound size={20} />
      </div>
      <h2 id="maya-signedout-title" className="maya-signedout__title">
        Sign in to chat
      </h2>
      <p className="maya-signedout__body">
        Chat needs a MAYA account. Sign in to start a conversation.
      </p>
      <p className="maya-signedout__body">
        Voice input and task execution are also planned and not available.
      </p>
      {logoutWarning ? (
        <p className="maya-signin__error" role="status">
          {logoutWarning}
        </p>
      ) : null}
      <Link href="/login" className="maya-btn maya-btn--primary">
        Sign in
      </Link>
      <Link href="/" className="maya-signedout__link">
        Back to overview
      </Link>
    </section>
  );
}
