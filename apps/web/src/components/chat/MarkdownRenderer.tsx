import { Fragment, memo, useMemo, type ReactNode } from "react";

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
import { CodeBlock } from "./CodeBlock";

/**
 * Renders the safe Markdown subset from `lib/markdown.ts` as React elements.
 *
 * Only elements are created here: there is no dangerouslySetInnerHTML, no
 * innerHTML and no HTML string. Text always goes through React, so markup in the
 * source stays visible text. Links get rel="noopener noreferrer" and open in a
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
      case "link": {
        const target = describeLinkTarget(node.href);
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
        <p key={key}>
          {renderInlines(block.children)}
          {caret ? <Caret /> : null}
        </p>
      );
    case "heading": {
      const Tag = HEADING_TAGS[block.level - 1];
      return (
        <Tag key={key} className={`maya-md__h maya-md__h--${block.level}`}>
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
            <li key={index}>
              {renderBlocks(item, caret && index === block.items.length - 1)}
            </li>
          ))}
        </Tag>
      );
    }
    case "blockquote":
      return (
        <blockquote key={key}>{renderBlocks(block.children, caret)}</blockquote>
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
  const blocks = useMemo(() => parseMarkdown(content), [content]);
  return (
    <div className="maya-md">
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
