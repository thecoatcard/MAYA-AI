import { KeyRound } from "lucide-react";
import Link from "next/link";

/**
 * Shown when no access token is held in memory. There is no sign-in screen in
 * the product yet, so this says so plainly and offers nothing that pretends to
 * work.
 */
export function SignedOutState() {
  return (
    <section className="maya-signedout" aria-labelledby="maya-signedout-title">
      <div className="maya-signedout__icon" aria-hidden="true">
        <KeyRound size={20} />
      </div>
      <p className="maya-tag">Planned</p>
      <h2 id="maya-signedout-title" className="maya-signedout__title">
        Sign-in is not built yet
      </h2>
      <p className="maya-signedout__body">
        Chat needs a signed-in account, and there is no sign-in screen in the
        product yet. Nothing on this page sends a message until there is one.
      </p>
      <p className="maya-signedout__body">
        Voice input and task execution are also planned and not available.
      </p>
      <Link href="/" className="maya-signedout__link">
        Back to overview
      </Link>
    </section>
  );
}
