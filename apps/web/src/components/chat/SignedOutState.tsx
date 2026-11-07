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
        Voice interaction, autonomous multi-agent task execution, and
        deliverables are ready.
      </p>
      {logoutWarning ? (
        <p className="maya-signin__error" role="status">
          {logoutWarning}
        </p>
      ) : null}
      <div className="p-3 my-2 rounded border border-zinc-800 bg-zinc-900/60 text-xs text-zinc-400 text-left max-w-sm">
        <p className="font-semibold text-zinc-300">
          Local Development Account:
        </p>
        <p className="mt-1 font-mono text-[11px] text-teal-300">
          User: <span className="text-zinc-200">admin</span> • Pass:{" "}
          <span className="text-zinc-200">AdminPassword123!</span>
        </p>
      </div>
      <Link href="/login" className="maya-btn maya-btn--primary">
        Sign in
      </Link>
      <Link href="/" className="maya-signedout__link">
        Back to overview
      </Link>
    </section>
  );
}
