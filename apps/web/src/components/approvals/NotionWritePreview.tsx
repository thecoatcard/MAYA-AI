import React from "react";

import { WrapText } from "../ui";
import { NOTION_BOOK_TOOL, NotionBookPreview } from "./NotionBookPreview";

/** Tool names of the Notion writes (backend app/mcp/notion_write.py). */
export const NOTION_WRITE_TOOLS: readonly string[] = [
  "notion_create_page",
  "notion_append_blocks",
  "notion_add_row",
  "notion_update_row",
  // Edits of existing content (backend app/mcp/notion_edit.py).
  "notion_update_page",
  "notion_archive_page",
  "notion_update_block",
  "notion_delete_block",
  "notion_add_comment",
  "notion_replace_section",
  // A whole book written by a job (backend app/mcp/notion_book.py).
  NOTION_BOOK_TOOL,
];

type Json = Record<string, unknown>;

const isObj = (v: unknown): v is Json =>
  v !== null && typeof v === "object" && !Array.isArray(v);

const plain = (parts: unknown): string =>
  Array.isArray(parts)
    ? parts
        .map((p) =>
          isObj(p) && isObj(p.text) && typeof p.text.content === "string"
            ? p.text.content
            : "",
        )
        .join("")
    : "";

/** A short readable value of one Notion property payload. */
export function propertyText(payload: unknown): string {
  if (!isObj(payload)) return "";
  const [kind] = Object.keys(payload);
  if (kind === undefined) return "";
  const value = payload[kind];
  if (kind === "title" || kind === "rich_text") return plain(value);
  if (kind === "select" || kind === "status")
    return isObj(value) ? String(value.name ?? "") : "";
  if (kind === "multi_select")
    return Array.isArray(value)
      ? value.map((o) => (isObj(o) ? String(o.name ?? "") : "")).join(", ")
      : "";
  if (kind === "date")
    return isObj(value)
      ? [value.start, value.end]
          .filter((x) => typeof x === "string")
          .join(" to ")
      : "";
  return value === null || value === undefined ? "" : String(value);
}

/** One line describing a block payload. */
export function blockText(block: unknown): string {
  if (!isObj(block)) return "";
  const kind = String(block.type ?? "");
  const data = isObj(block[kind]) ? (block[kind] as Json) : {};
  if (kind === "image") {
    const ext = isObj(data.external) ? data.external : {};
    return `image ${String(ext.url ?? "")}`;
  }
  if (kind === "table") {
    const rows = Array.isArray(data.children) ? data.children.length : 0;
    return `table ${String(data.table_width ?? "?")} columns, ${rows} rows`;
  }
  const text = plain(data.rich_text);
  if (kind === "to_do")
    return `to_do [${data.checked === true ? "x" : " "}] ${text}`;
  if (kind === "code") return `code (${String(data.language ?? "")}) ${text}`;
  return `${kind} ${text}`;
}

/** Plain-language view of the exact Notion requests an approval will send. */
export function NotionWritePreview({
  args,
}: {
  readonly args: Record<string, unknown>;
}): React.JSX.Element {
  if (typeof args.content_sha256 === "string") {
    return <NotionBookPreview args={args} />;
  }
  const requests = Array.isArray(args.requests) ? args.requests : [];
  if (requests.length === 0) {
    return (
      <p className="m-0 text-maya-small text-maya-secondary">
        No Notion requests were found in the arguments.
      </p>
    );
  }
  return (
    <div className="flex min-w-0 flex-col gap-3">
      {requests.map((req, i) => {
        const r = isObj(req) ? req : {};
        const body = isObj(r.body) ? r.body : {};
        const props = isObj(body.properties) ? body.properties : {};
        const children = Array.isArray(body.children) ? body.children : [];
        return (
          <div key={i} className="flex min-w-0 flex-col gap-1 text-maya-small">
            <p className="m-0 font-mono text-maya-mono">
              {String(r.method ?? "")} {String(r.path ?? "")}
            </p>
            {typeof r.note === "string" && r.note !== "" && (
              <WrapText dir="auto">{r.note}</WrapText>
            )}
            {body.archived === true && (
              <p className="m-0 font-medium">Moves the page to the trash.</p>
            )}
            {typeof body.after === "string" && (
              <p className="m-0">Inserted after block {body.after}.</p>
            )}
            {Object.keys(props).length > 0 && (
              <ul className="m-0 list-none p-0">
                {Object.entries(props).map(([name, v]) => (
                  <li key={name}>
                    <span className="font-medium">{name}:</span>{" "}
                    <WrapText dir="auto">{propertyText(v)}</WrapText>
                  </li>
                ))}
              </ul>
            )}
            {children.length > 0 && (
              <ol className="m-0 list-decimal pl-5">
                {children.map((b, j) => (
                  <li key={j}>
                    <WrapText dir="auto">{blockText(b)}</WrapText>
                  </li>
                ))}
              </ol>
            )}
          </div>
        );
      })}
    </div>
  );
}
