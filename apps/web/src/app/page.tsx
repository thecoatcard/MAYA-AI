"use client";

import { Layers3 } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import {
  ThemeToggle,
  readStoredTheme,
  storeTheme,
  type ThemePreference,
} from "../components/chat/ThemeToggle";

const capabilityList = [
  { title: "Accounts and sign-in", status: "Available" },
  { title: "Text chat with history", status: "Available" },
  { title: "Agent tasks with approvals", status: "Planned" },
  { title: "Voice workspace", status: "Planned" },
] as const;

export default function Home() {
  const [theme, setTheme] = useState<ThemePreference>("system");

  useEffect(() => {
    const stored = readStoredTheme();
    setTheme(stored);
    storeTheme(stored);

    const app = document.querySelector<HTMLElement>(".maya-app");
    const updateVisibility = () => {
      if (app !== null) {
        app.dataset.pageHidden = String(document.hidden);
      }
    };
    updateVisibility();
    document.addEventListener("visibilitychange", updateVisibility);
    return () =>
      document.removeEventListener("visibilitychange", updateVisibility);
  }, []);

  const changeTheme = (next: ThemePreference) => {
    setTheme(next);
    storeTheme(next);
  };

  return (
    <div className="maya-chat maya-home">
      <header className="maya-home__header">
        <Link href="/" className="maya-brand" aria-label="MAYA AI home">
          <Layers3 aria-hidden="true" size={18} />
          MAYA AI
        </Link>
        <nav className="maya-home__nav" aria-label="Main navigation">
          <Link className="maya-btn maya-btn--quiet" href="/login">
            Sign in
          </Link>
          <Link
            className="maya-btn maya-btn--primary maya-ripple"
            href="/register"
          >
            Create account
          </Link>
          <ThemeToggle value={theme} onChange={changeTheme} />
        </nav>
      </header>
      <main className="maya-home__main">
        <section aria-labelledby="maya-home-title">
          <p className="maya-home__eyebrow">MAYA AI · Text workspace</p>
          <h1 className="maya-home__title" id="maya-home-title">
            A digital employee that asks before it acts.
          </h1>
          <p className="maya-home__intro">
            Start with an account and a text conversation. Tasks with approvals
            and a voice workspace are planned; they will appear here when they
            are available.
          </p>
          <div className="maya-home__actions">
            <Link
              className="maya-btn maya-btn--primary maya-ripple"
              href="/login"
            >
              Sign in
            </Link>
            <Link className="maya-btn" href="/register">
              Create account
            </Link>
          </div>
        </section>
        <section aria-label="What is available">
          <ol className="maya-home__list">
            {capabilityList.map((item, index) => (
              <li className="maya-home__item" key={item.title}>
                <span className="maya-home__number">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <span>{item.title}</span>
                <span
                  className="maya-home__status"
                  data-state={item.status.toLowerCase()}
                >
                  {item.status}
                </span>
              </li>
            ))}
          </ol>
        </section>
      </main>
      <footer className="maya-home__footer">
        MAYA keeps planned capabilities clearly separate from what works today.
      </footer>
    </div>
  );
}
