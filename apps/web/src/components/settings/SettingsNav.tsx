"use client";

import React, { useRef } from "react";

import { cx } from "../ui";
import { SECTIONS, type SectionKey } from "./sections";

/**
 * Section navigation. From 768 px up it is a sticky list of links (Up/Down/Home/End move focus,
 * Enter follows the link); below that it is one labelled select, so 360 px windows keep the
 * whole page width for the settings. Both write the same hash.
 */
export function SettingsNav({
  active,
  onSelect,
}: {
  readonly active: SectionKey;
  readonly onSelect: (key: SectionKey) => void;
}): React.JSX.Element {
  const list = useRef<HTMLUListElement>(null);

  const onKeyDown = (event: React.KeyboardEvent<HTMLUListElement>): void => {
    const keys = ["ArrowDown", "ArrowUp", "Home", "End"];
    if (!keys.includes(event.key)) return;
    const links = Array.from(
      list.current?.querySelectorAll<HTMLAnchorElement>("a") ?? [],
    );
    if (links.length === 0) return;
    const at = links.findIndex((link) => link === document.activeElement);
    let next = at;
    if (event.key === "ArrowDown") next = (at + 1) % links.length;
    if (event.key === "ArrowUp") next = (at - 1 + links.length) % links.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = links.length - 1;
    event.preventDefault();
    links[next]?.focus();
  };

  return (
    <>
      <div className="md:hidden">
        <label
          htmlFor="settings-section-select"
          className="mb-1 block text-maya-caption text-maya-muted"
        >
          Settings section
        </label>
        <select
          id="settings-section-select"
          value={active}
          onChange={(event) => onSelect(event.target.value as SectionKey)}
          className="min-h-9 w-full min-w-0 rounded-maya-md border border-maya-control-border bg-maya-pane px-3 py-2 text-maya-small text-maya-text hover:border-maya-border-strong focus:border-maya-focus"
        >
          {SECTIONS.map((section) => (
            <option key={section.key} value={section.key}>
              {section.label}
            </option>
          ))}
        </select>
      </div>
      <nav
        aria-label="Settings sections"
        className="sticky top-0 hidden self-start md:block"
      >
        <ul
          ref={list}
          onKeyDown={onKeyDown}
          className="m-0 grid list-none gap-0.5 p-0"
        >
          {SECTIONS.map((section) => {
            const current = section.key === active;
            return (
              <li key={section.key}>
                <a
                  href={`#${section.id}`}
                  aria-current={current ? "page" : undefined}
                  onClick={(event) => {
                    event.preventDefault();
                    onSelect(section.key);
                  }}
                  className={cx(
                    "maya-row motion-interactive motion-press flex min-h-9 items-center rounded-maya-md border-l-2 px-3 py-2 text-maya-small pointer-coarse:min-h-11",
                    current
                      ? "border-maya-accent bg-maya-raised font-medium text-maya-text"
                      : "border-transparent text-maya-secondary hover:bg-maya-hover hover:text-maya-text",
                  )}
                >
                  {section.label}
                </a>
              </li>
            );
          })}
        </ul>
      </nav>
    </>
  );
}
