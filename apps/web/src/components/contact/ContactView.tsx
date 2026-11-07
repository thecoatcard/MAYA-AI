"use client";

import {
  Building2,
  CheckCircle2,
  Mail,
  MessageSquare,
  Send,
  Shield,
} from "lucide-react";
import React, { useState } from "react";
import { PublicFooter } from "../layout/PublicFooter";
import { PublicNav } from "../layout/PublicNav";

export function ContactView(): React.JSX.Element {
  const [submitted, setSubmitted] = useState<boolean>(false);
  const [form, setForm] = useState({
    name: "",
    email: "",
    organization: "",
    topic: "general",
    message: "",
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitted(true);
  };

  return (
    <div className="min-h-screen flex flex-col bg-zinc-950 text-zinc-100 font-sans relative overflow-x-hidden">
      {/* Background radial gradients */}
      <div className="absolute top-0 left-1/4 w-[600px] h-[600px] bg-teal-500/10 rounded-full blur-[140px] pointer-events-none" />
      <div className="absolute bottom-1/4 right-1/4 w-[500px] h-[500px] bg-cyan-500/10 rounded-full blur-[120px] pointer-events-none" />

      <PublicNav />

      <main className="flex-1 max-w-5xl mx-auto w-full px-6 py-16 relative z-10">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-12 items-start">
          {/* Info Side */}
          <div>
            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-teal-500/30 bg-teal-500/10 text-teal-300 text-xs font-mono mb-4">
              <Mail className="w-3.5 h-3.5" />
              <span>Direct Engineering Channel</span>
            </div>
            <h1 className="text-4xl sm:text-5xl font-extrabold tracking-tight text-white mb-6">
              Connect with <br />
              <span className="text-transparent bg-clip-text bg-gradient-to-r from-teal-400 via-cyan-300 to-emerald-400">
                MAYA AI Team.
              </span>
            </h1>
            <p className="text-sm text-zinc-400 leading-relaxed mb-8">
              Whether you are architecting a sovereign multi-agent deployment,
              configuring custom MCP tool connectors, or reporting a security
              observation, our core team is here to assist.
            </p>

            <div className="space-y-4 text-xs text-zinc-300">
              <div className="p-4 rounded-xl border border-white/5 bg-zinc-900/40 backdrop-blur-md flex items-start gap-3">
                <Shield className="w-5 h-5 text-teal-400 shrink-0 mt-0.5" />
                <div>
                  <h2 className="font-semibold text-white">
                    Responsible Security Disclosure
                  </h2>
                  <p className="text-zinc-400 mt-1">
                    Security researchers may reach out directly. We acknowledge
                    disclosures within 24 hours with cryptographic triage.
                  </p>
                </div>
              </div>

              <div className="p-4 rounded-xl border border-white/5 bg-zinc-900/40 backdrop-blur-md flex items-start gap-3">
                <Building2 className="w-5 h-5 text-cyan-400 shrink-0 mt-0.5" />
                <div>
                  <h2 className="font-semibold text-white">
                    Enterprise Deployments
                  </h2>
                  <p className="text-zinc-400 mt-1">
                    Assistance with air-gapped private cluster deployment,
                    SSO/SAML integration, and custom LLM gateway routing.
                  </p>
                </div>
              </div>

              <div className="p-4 rounded-xl border border-white/5 bg-zinc-900/40 backdrop-blur-md flex items-start gap-3">
                <MessageSquare className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
                <div>
                  <h2 className="font-semibold text-white">
                    MCP Server Ecosystem
                  </h2>
                  <p className="text-zinc-400 mt-1">
                    Guidance on certifying internal proprietary tools against
                    MAYA AI schema poisoning defenses.
                  </p>
                </div>
              </div>
            </div>
          </div>

          {/* Form Side */}
          <div className="rounded-2xl border border-white/10 bg-zinc-900/60 backdrop-blur-xl p-8 shadow-2xl">
            {submitted ? (
              <div className="text-center py-12">
                <div className="w-12 h-12 rounded-2xl bg-teal-500/10 border border-teal-500/30 flex items-center justify-center text-teal-400 mx-auto mb-4">
                  <CheckCircle2 className="w-6 h-6" />
                </div>
                <h2 className="text-xl font-bold text-white mb-2">
                  Message Dispatched
                </h2>
                <p className="text-xs text-zinc-400 max-w-xs mx-auto leading-relaxed mb-6">
                  Thank you for reaching out. An engineer will follow up via
                  your verified address shortly.
                </p>
                <button
                  type="button"
                  onClick={() => setSubmitted(false)}
                  className="px-4 py-2 rounded-xl border border-white/10 bg-white/5 hover:bg-white/10 text-xs font-semibold text-zinc-200 transition-colors"
                >
                  Send Another Inquiry
                </button>
              </div>
            ) : (
              <form onSubmit={handleSubmit} className="space-y-4">
                <h2 className="text-lg font-bold text-white mb-1">
                  Send a Message
                </h2>
                <p className="text-xs text-zinc-400 mb-4">
                  Fill in your details below and an engineer will reply.
                </p>

                <div>
                  <label
                    htmlFor="contact-name"
                    className="block text-xs font-medium text-zinc-300 mb-1"
                  >
                    Your Name
                  </label>
                  <input
                    id="contact-name"
                    type="text"
                    required
                    value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                    placeholder="Ada Lovelace"
                    className="w-full px-3.5 py-2.5 rounded-xl border border-white/10 bg-black/40 text-xs text-zinc-100 placeholder:text-zinc-600 focus:outline-none focus:border-teal-500/50 focus:ring-1 focus:ring-teal-500/30 transition-all"
                  />
                </div>

                <div>
                  <label
                    htmlFor="contact-email"
                    className="block text-xs font-medium text-zinc-300 mb-1"
                  >
                    Work Email
                  </label>
                  <input
                    id="contact-email"
                    type="email"
                    required
                    value={form.email}
                    onChange={(e) =>
                      setForm({ ...form, email: e.target.value })
                    }
                    placeholder="ada@example.com"
                    className="w-full px-3.5 py-2.5 rounded-xl border border-white/10 bg-black/40 text-xs text-zinc-100 placeholder:text-zinc-600 focus:outline-none focus:border-teal-500/50 focus:ring-1 focus:ring-teal-500/30 transition-all"
                  />
                </div>

                <div>
                  <label
                    htmlFor="contact-org"
                    className="block text-xs font-medium text-zinc-300 mb-1"
                  >
                    Organization / Company
                  </label>
                  <input
                    id="contact-org"
                    type="text"
                    value={form.organization}
                    onChange={(e) =>
                      setForm({ ...form, organization: e.target.value })
                    }
                    placeholder="Acme Aerospace"
                    className="w-full px-3.5 py-2.5 rounded-xl border border-white/10 bg-black/40 text-xs text-zinc-100 placeholder:text-zinc-600 focus:outline-none focus:border-teal-500/50 focus:ring-1 focus:ring-teal-500/30 transition-all"
                  />
                </div>

                <div>
                  <label
                    htmlFor="contact-topic"
                    className="block text-xs font-medium text-zinc-300 mb-1"
                  >
                    Topic
                  </label>
                  <select
                    id="contact-topic"
                    value={form.topic}
                    onChange={(e) =>
                      setForm({ ...form, topic: e.target.value })
                    }
                    className="w-full px-3.5 py-2.5 rounded-xl border border-white/10 bg-zinc-900 text-xs text-zinc-100 focus:outline-none focus:border-teal-500/50 focus:ring-1 focus:ring-teal-500/30 transition-all"
                  >
                    <option value="general">
                      General Architecture Inquiry
                    </option>
                    <option value="enterprise">
                      Enterprise Sovereign Deployment
                    </option>
                    <option value="mcp">MCP Integration / Custom Tools</option>
                    <option value="security">
                      Vulnerability Disclosure (Confidential)
                    </option>
                  </select>
                </div>

                <div>
                  <label
                    htmlFor="contact-message"
                    className="block text-xs font-medium text-zinc-300 mb-1"
                  >
                    Message
                  </label>
                  <textarea
                    id="contact-message"
                    required
                    rows={4}
                    value={form.message}
                    onChange={(e) =>
                      setForm({ ...form, message: e.target.value })
                    }
                    placeholder="Describe your technical requirements or deployment questions..."
                    className="w-full px-3.5 py-2.5 rounded-xl border border-white/10 bg-black/40 text-xs text-zinc-100 placeholder:text-zinc-600 focus:outline-none focus:border-teal-500/50 focus:ring-1 focus:ring-teal-500/30 transition-all resize-none"
                  />
                </div>

                <button
                  type="submit"
                  className="w-full inline-flex items-center justify-center gap-2 py-3 px-4 rounded-xl bg-teal-500 hover:bg-teal-400 text-zinc-950 font-bold text-xs shadow-lg shadow-teal-500/20 transition-all hover:scale-[1.01]"
                >
                  <Send className="w-4 h-4" />
                  <span>Send Message to Engineering</span>
                </button>
              </form>
            )}
          </div>
        </div>
      </main>

      <PublicFooter />
    </div>
  );
}
