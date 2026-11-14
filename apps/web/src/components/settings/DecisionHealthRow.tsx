"use client";

import React, { useEffect, useState } from "react";

import {
  getDecisionStatus,
  type DecisionStatus,
} from "../../lib/decisions-client";

/** One small row: is the decision model on, did its startup check pass, and when. */
export function decisionSummary(status: DecisionStatus): {
  readonly label: string;
  readonly detail: string;
} {
  if (!status.enabled) {
    return { label: "Off", detail: "MAYA uses its fixed rules only." };
  }
  const check = status.startup_check;
  const when = check?.checked_at ? ` Last check: ${check.checked_at}.` : "";
  if (!check || check.state === "pending") {
    return { label: "Checking", detail: `Model ${status.model}.` };
  }
  if (check.state === "ok") {
    return {
      label: "OK",
      detail: `Model ${status.model}, circuit ${status.circuit}.${when}`,
    };
  }
  return {
    label: "Failed",
    detail: `${check.reason || "No answer"}. MAYA falls back to fixed rules.${when}`,
  };
}

export function DecisionHealthRow(): React.ReactElement {
  const [status, setStatus] = useState<DecisionStatus | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    getDecisionStatus()
      .then((value) => {
        if (live) setStatus(value);
      })
      .catch(() => {
        if (live) setFailed(true);
      });
    return () => {
      live = false;
    };
  }, []);

  const summary = status ? decisionSummary(status) : null;
  return (
    <div
      className="flex items-center justify-between gap-3 rounded-maya-md border border-maya-border px-4 py-3 text-maya-body"
      data-testid="decision-health"
    >
      <span className="font-medium text-maya-text">Decision model</span>
      <span className="text-maya-text-muted">
        {failed
          ? "Status unavailable"
          : summary
            ? `${summary.label}. ${summary.detail}`
            : "Loading"}
      </span>
    </div>
  );
}
