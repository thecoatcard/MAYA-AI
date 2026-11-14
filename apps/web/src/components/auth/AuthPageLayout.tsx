import React from "react";

export interface AuthPageLayoutProps {
  children: React.ReactNode;
  maxWidth?: string;
}

/**
 * Stand-alone frame for the auth pages (login, register, forgot/reset password, verify): one calm
 * card centred in the window on the page background. Tokens only, so it follows the chosen theme.
 * The card sizes to its content; it never stretches to the window height.
 */
export function AuthPageLayout({
  children,
  maxWidth = "max-w-md",
}: AuthPageLayoutProps): React.JSX.Element {
  return (
    <main className="flex min-h-dvh min-w-0 flex-col bg-maya-bg p-4 text-maya-text sm:p-6">
      <div className="m-auto w-full min-w-0 py-6">
        <div
          className={`mx-auto w-full min-w-0 ${maxWidth} rounded-maya-lg border border-maya-border bg-maya-pane p-4 sm:p-6`}
        >
          {children}
        </div>
      </div>
    </main>
  );
}
