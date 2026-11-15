import {
  Fragment,
  memo,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import "katex/dist/katex.min.css";
import "./math.css";

import { TableScroll } from "../ui/Scroll";
import {
  describeLinkTarget,
  inlinePlainText,
  linkLabelShowsTarget,
  markBidiControls,
  parseMarkdown,
  stripBidiControls,
  type Block,
  type Inline,
} from "../../lib/markdown";
import {
  getLoadedKatex,
  loadKatex,
  typesetMath,
  type KatexApi,
} from "../../lib/math-katex";
import { isCitationLabel, prepareCitations } from "../../lib/citations";
import { CodeBlock } from "./CodeBlock";

/**
 * Renders the safe Markdown subset from `lib/markdown.ts` as React elements.
 *
 * Only elements are created here: there is no dangerouslySetInnerHTML, no
 * innerHTML and no HTML string. Text always goes through React, so markup in the
 * source stays visible text. The one exception is typeset math: KaTeX output is
 * rebuilt from an allowlist (lib/math-katex.ts) and appended as DOM nodes. Links get rel="noopener noreferrer" and open in a
 * new tab (the parser only produces http, https and mailto links).
 *
 * Every link also shows where it really goes, as "(host)" after the link text, and
 * says "goes to host" to screen readers, so link text such as
 * "https://mybank.example/login" cannot pose as another site. The host is not
 * repeated when the link text is itself that address. Bidirectional control
 * characters are stripped from link text and shown as <U+202E> markers in inline
 * code, so they cannot reorder the host or the text around it.
 */

export interface MarkdownRendererProps {
  content: string;
  /** Show the streaming caret at the end of the text. */
  streaming?: boolean;
}

const HEADING_TAGS = ["h3", "h3", "h4", "h5", "h6", "h6"] as const;

/**
 * One formula. Until KaTeX has loaded (it is a lazy chunk), or when KaTeX refuses the formula,
 * it shows the text fallback from latex-text.ts in the same box, so nothing jumps or goes blank.
 */
function MathView({
  tex,
  text,
  ok,
  display,
}: {
  tex: string;
  text: string;
  ok: boolean;
  display: boolean;
}) {
  const [katex, setKatex] = useState<KatexApi | null>(getLoadedKatex);
  useEffect(() => {
    if (katex !== null) return;
    let live = true;
    loadKatex().then(
      (api) => {
        if (live) setKatex(api);
      },
      () => undefined, // stay on the text fallback
    );
    return () => {
      live = false;
    };
  }, [katex]);

  const fragment = useMemo(
    () => (katex === null ? null : typesetMath(katex, tex, display)),
    [katex, tex, display],
  );
  const host = useRef<HTMLSpanElement>(null);
  useLayoutEffect(() => {
    if (fragment !== null && host.current !== null) {
      host.current.replaceChildren(fragment.cloneNode(true));
    }
  }, [fragment]);

  const Tag = display ? "div" : "span";
  const shape = display
    ? "maya-math maya-math--block"
    : "maya-math maya-math--inline";
  const label = { role: "math", "aria-label": tex, title: tex } as const;
  if (fragment !== null) {
    return (
      <Tag className={shape} {...label} tabIndex={display ? 0 : undefined}>
        <span ref={host} />
      </Tag>
    );
  }
  if (!ok) {
    return display ? (
      <div className="maya-md__math-block maya-md__math-block--raw" {...label}>
        <code>{markBidiControls(tex)}</code>
      </div>
    ) : (
      <code className="maya-md__code" title="Formula shown as written">
        {markBidiControls(tex)}
      </code>
    );
  }
  return display ? (
    <div className="maya-md__math-block maya-math--pending" {...label}>
      {text}
    </div>
  ) : (
    <em className="maya-md__math maya-math--pending" {...label}>
      {text}
    </em>
  );
}

function Caret() {
  return <span className="maya-caret" aria-hidden="true" />;
}

function renderInlines(nodes: readonly Inline[], inLink = false): ReactNode[] {
  return nodes.map((node, index) => {
    switch (node.type) {
      case "text":
        return (
          <Fragment key={index}>
            {inLink ? stripBidiControls(node.value) : node.value}
          </Fragment>
        );
      case "strong":
        return (
          <strong key={index}>{renderInlines(node.children, inLink)}</strong>
        );
      case "em":
        return <em key={index}>{renderInlines(node.children, inLink)}</em>;
      case "code":
        return (
          <code key={index} className="maya-md__code">
            {markBidiControls(node.value)}
          </code>
        );
      case "math":
        return (
          <MathView
            key={index}
            tex={node.tex}
            text={node.text}
            ok={node.ok}
            display={false}
          />
        );
      case "link": {
        const target = describeLinkTarget(node.href);
        const label = inlinePlainText(node.children);
        if (isCitationLabel(label)) {
          // A citation: a small superscript number; the host is the tooltip.
          return (
            <sup key={index} className="maya-md__cite">
              <a
                href={node.href}
                target="_blank"
                rel="noopener noreferrer"
                referrerPolicy="no-referrer"
                title={target ?? undefined}
                aria-label={`Source ${label.slice(1, -1)}${target ? `, ${target}` : ""} (opens in a new tab)`}
              >
                {label}
              </a>
            </sup>
          );
        }
        const showTarget =
          target !== null &&
          !linkLabelShowsTarget(inlinePlainText(node.children), node.href);
        return (
          <a
            key={index}
            href={node.href}
            target="_blank"
            rel="noopener noreferrer"
            referrerPolicy="no-referrer"
            className="maya-md__link"
          >
            {renderInlines(node.children, true)}
            {showTarget ? (
              <>
                {" "}
                <span className="maya-md__host" aria-hidden="true">
                  ({target})
                </span>
                <span className="maya-sr-only"> (goes to {target})</span>
              </>
            ) : null}
            <span className="maya-sr-only"> (opens in a new tab)</span>
          </a>
        );
      }
      case "br":
        return <br key={index} />;
    }
  });
}

function renderBlock(block: Block, caret: boolean, key: number): ReactNode {
  switch (block.type) {
    case "paragraph":
      return (
        <p key={key} dir="auto">
          {renderInlines(block.children)}
          {caret ? <Caret /> : null}
        </p>
      );
    case "heading": {
      const Tag = HEADING_TAGS[block.level - 1];
      return (
        <Tag
          key={key}
          dir="auto"
          className={`maya-md__h maya-md__h--${block.level}`}
        >
          {renderInlines(block.children)}
          {caret ? <Caret /> : null}
        </Tag>
      );
    }
    case "code":
      return (
        <Fragment key={key}>
          <CodeBlock code={block.value} language={block.language} />
          {caret ? (
            <p>
              <Caret />
            </p>
          ) : null}
        </Fragment>
      );
    case "list": {
      const Tag = block.ordered ? "ol" : "ul";
      return (
        <Tag
          key={key}
          start={block.ordered && block.start !== 1 ? block.start : undefined}
        >
          {block.items.map((item, index) => (
            <li key={index} dir="auto">
              {renderBlocks(item, caret && index === block.items.length - 1)}
            </li>
          ))}
        </Tag>
      );
    }
    case "blockquote":
      return (
        <blockquote key={key} dir="auto">
          {renderBlocks(block.children, caret)}
        </blockquote>
      );
    case "table":
      return (
        <Fragment key={key}>
          <TableScroll
            label={`Table, ${block.rows.length} ${block.rows.length === 1 ? "row" : "rows"}`}
            className="maya-md__table"
          >
            <table>
              <thead>
                <tr>
                  {block.header.map((cell, column) => (
                    <th
                      key={column}
                      scope="col"
                      dir="auto"
                      data-align={block.align[column] ?? undefined}
                    >
                      {renderInlines(cell)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {block.rows.map((row, rowIndex) => (
                  <tr key={rowIndex}>
                    {row.map((cell, column) => (
                      <td
                        key={column}
                        dir="auto"
                        data-align={block.align[column] ?? undefined}
                      >
                        {renderInlines(cell)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
          {caret ? (
            <p>
              <Caret />
            </p>
          ) : null}
        </Fragment>
      );
    case "math":
      return (
        <Fragment key={key}>
          <MathView tex={block.tex} text={block.text} ok={block.ok} display />
          {caret ? (
            <p>
              <Caret />
            </p>
          ) : null}
        </Fragment>
      );
    case "hr":
      return (
        <Fragment key={key}>
          <hr />
          {caret ? (
            <p>
              <Caret />
            </p>
          ) : null}
        </Fragment>
      );
  }
}

function renderBlocks(blocks: readonly Block[], caret: boolean): ReactNode[] {
  return blocks.map((block, index) =>
    renderBlock(block, caret && index === blocks.length - 1, index),
  );
}

function MarkdownRendererImpl({
  content,
  streaming = false,
}: MarkdownRendererProps) {
  const blocks = useMemo(
    () => parseMarkdown(prepareCitations(content)),
    [content],
  );
  return (
    <div className={`maya-md ${streaming ? "maya-md--streaming" : ""}`.trim()}>
      {renderBlocks(blocks, streaming)}
      {streaming && blocks.length === 0 ? (
        <p>
          <Caret />
        </p>
      ) : null}
    </div>
  );
}

export const MarkdownRenderer = memo(MarkdownRendererImpl);
