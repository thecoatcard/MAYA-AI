import { Layers3, MessageSquare, Mic, Workflow } from "lucide-react";
import Link from "next/link";
import React from "react";

const roadmap = [
  {
    title: "Conversations",
    description: "Secure accounts and persisted text chat.",
    icon: MessageSquare,
  },
  {
    title: "Task orchestration",
    description: "Verified work with human approval at the right boundaries.",
    icon: Workflow,
  },
  {
    title: "Voice workspace",
    description: "A voice-first interface connected to the task engine.",
    icon: Mic,
  },
];

export default function Home() {
  return (
    <div className="mx-auto min-h-screen max-w-6xl px-6 py-8 sm:px-12">
      <header className="flex flex-wrap items-center justify-between gap-6 border-b border-white/10 pb-6">
        <Link
          href="/"
          className="flex items-center gap-3 font-semibold tracking-widest"
        >
          <Layers3 aria-hidden="true" className="text-teal-300" />
          MAYA AI
        </Link>
        <nav
          aria-label="Main navigation"
          className="flex gap-6 text-sm text-slate-300"
        >
          <Link href="/" aria-current="page">
            Overview
          </Link>
          <a href="#roadmap">Roadmap</a>
        </nav>
      </header>
      <main>
        <section aria-labelledby="welcome-heading" className="py-20 sm:py-28">
          <p className="mb-6 inline-block rounded-full border border-teal-300/30 px-4 py-2 text-sm text-teal-200">
            Foundation only
          </p>
          <h1
            id="welcome-heading"
            className="max-w-3xl text-4xl font-semibold leading-tight tracking-tight sm:text-6xl"
          >
            A foundation for what comes next.
          </h1>
          <p className="mt-6 max-w-2xl text-lg leading-relaxed text-slate-300">
            MAYA AI is taking shape. This local scaffold establishes the web and
            API foundations. Accounts, chat, agents, and voice are not available
            yet.
          </p>
        </section>
        <section
          id="roadmap"
          aria-labelledby="roadmap-heading"
          className="pb-20"
        >
          <h2 id="roadmap-heading" className="mb-6 text-xl font-semibold">
            On the roadmap
          </h2>
          <div className="grid gap-5 md:grid-cols-3">
            {roadmap.map(({ title, description, icon: Icon }) => (
              <article
                key={title}
                className="rounded-2xl border border-white/10 bg-white/5 p-6"
              >
                <Icon aria-hidden="true" className="mb-6 text-teal-300" />
                <h3 className="font-semibold">{title}</h3>
                <p className="mt-3 text-sm leading-relaxed text-slate-300">
                  {description}
                </p>
                <p className="mt-6 text-xs uppercase tracking-widest text-slate-400">
                  Planned
                </p>
              </article>
            ))}
          </div>
        </section>
      </main>
      <footer className="border-t border-white/10 py-6 text-sm text-slate-400">
        Local development foundation. Not a production-ready product.
      </footer>
    </div>
  );
}
