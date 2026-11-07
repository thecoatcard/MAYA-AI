import type { Metadata } from "next";
import React from "react";
import { ContactView } from "../../components/contact/ContactView";

export const metadata: Metadata = {
  title: "Contact & Security Disclosure | MAYA AI",
  description:
    "Get in touch with the MAYA AI engineering team for enterprise inquiries, architecture consultations, and security vulnerability disclosures.",
};

export default function ContactPage(): React.JSX.Element {
  return <ContactView />;
}
