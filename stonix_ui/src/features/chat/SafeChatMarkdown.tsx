import {
  Fragment,
  cloneElement,
  createElement,
  isValidElement,
  memo,
  type ReactNode,
} from 'react';

const WORD = /[\p{L}\p{N}_]/u;

function renderInline(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const re =
    /`([^`\n]+)`|\*\*(?=\S)([^*]+?)\*\*|\*(?=\S)([^*\n]+?)\*|_(?=\S)([^_\n]+?)_/g;
  let last = 0;
  let key = 0;
  let match: RegExpExecArray | null;
  while ((match = re.exec(text))) {
    const italic = match[3] ?? match[4];
    if (italic != null) {
      const start = match.index;
      const end = start + match[0].length;
      const before = start > 0 ? text[start - 1] : '';
      const after = end < text.length ? text[end] : '';
      const endsSpace = /\s$/.test(italic);
      if ((before && WORD.test(before)) || (after && WORD.test(after)) || endsSpace) {
        re.lastIndex = match.index + 1;
        continue;
      }
    }
    if (match.index > last) {
      nodes.push(<Fragment key={`t${key++}`}>{text.slice(last, match.index)}</Fragment>);
    }
    if (match[1] != null) {
      nodes.push(<code key={`c${key++}`}>{match[1]}</code>);
    } else if (match[2] != null) {
      nodes.push(<strong key={`b${key++}`}>{match[2]}</strong>);
    } else {
      nodes.push(<em key={`i${key++}`}>{italic}</em>);
    }
    last = match.index + match[0].length;
  }
  if (last < text.length) {
    nodes.push(<Fragment key={`t${key++}`}>{text.slice(last)}</Fragment>);
  }
  return nodes;
}

function isBlank(line: string): boolean {
  return /^\s*$/.test(line);
}

function isFence(line: string): boolean {
  return /^\s*```/.test(line);
}

function headingMatch(line: string): { level: number; text: string } | null {
  const m = line.match(/^\s*(#{1,6})\s+(\S.*)$/);
  if (!m) return null;
  return { level: Math.min(6, m[1].length), text: m[2].trimEnd() };
}

function isUl(line: string): boolean {
  return /^\s*[-*]\s+/.test(line);
}

function isOl(line: string): boolean {
  return /^\s*\d+\.\s+/.test(line);
}

function stripUl(line: string): string {
  return line.replace(/^\s*[-*]\s+/, '');
}

function parseOl(line: string): { n: number; text: string } {
  const m = line.match(/^\s*(\d+)\.\s+(.*)$/);
  if (!m) return { n: 1, text: line };
  return { n: Number.parseInt(m[1], 10), text: m[2] };
}

function attachSuffix(blocks: ReactNode[], suffix?: ReactNode): ReactNode[] {
  if (suffix == null) return blocks;
  for (let i = blocks.length - 1; i >= 0; i -= 1) {
    const node = blocks[i];
    if (isValidElement(node) && node.type === 'p') {
      const next = blocks.slice();
      next[i] = cloneElement(node, undefined, node.props.children, suffix);
      return next;
    }
  }
  return [...blocks, <span key="md-suffix">{suffix}</span>];
}

function SafeChatMarkdownInner({
  text,
  suffix,
}: {
  text: string;
  suffix?: ReactNode;
}) {
  const lines = String(text ?? '').replace(/\r\n/g, '\n').split('\n');
  const blocks: ReactNode[] = [];
  let i = 0;
  let key = 0;
  while (i < lines.length) {
    if (isBlank(lines[i])) {
      i += 1;
      continue;
    }
    if (isFence(lines[i])) {
      i += 1;
      const body: string[] = [];
      while (i < lines.length && !isFence(lines[i])) {
        body.push(lines[i]);
        i += 1;
      }
      if (i < lines.length && isFence(lines[i])) i += 1;
      blocks.push(
        <pre key={`pre${key++}`}>
          <code>{body.join('\n')}</code>
        </pre>,
      );
      continue;
    }
    const heading = headingMatch(lines[i]);
    if (heading) {
      const tag = (`h${heading.level}`) as 'h1' | 'h2' | 'h3' | 'h4' | 'h5' | 'h6';
      blocks.push(createElement(tag, { key: `h${key++}` }, renderInline(heading.text)));
      i += 1;
      continue;
    }
    if (isUl(lines[i])) {
      const items: string[] = [];
      while (i < lines.length && isUl(lines[i])) {
        items.push(stripUl(lines[i]));
        i += 1;
      }
      blocks.push(
        <ul key={`ul${key++}`}>
          {items.map((item, idx) => (
            <li key={idx}>{renderInline(item)}</li>
          ))}
        </ul>,
      );
      continue;
    }
    if (isOl(lines[i])) {
      const items: { n: number; text: string }[] = [];
      while (i < lines.length && isOl(lines[i])) {
        items.push(parseOl(lines[i]));
        i += 1;
      }
      blocks.push(
        <ol key={`ol${key++}`}>
          {items.map((item, idx) => (
            <li key={idx} value={item.n}>
              {renderInline(item.text)}
            </li>
          ))}
        </ol>,
      );
      continue;
    }
    const para: string[] = [];
    while (
      i < lines.length &&
      !isBlank(lines[i]) &&
      !isUl(lines[i]) &&
      !isOl(lines[i]) &&
      !isFence(lines[i]) &&
      !headingMatch(lines[i])
    ) {
      para.push(lines[i]);
      i += 1;
    }
    if (para.length) {
      blocks.push(
        <p key={`p${key++}`}>
          {para.map((line, idx) => (
            <Fragment key={idx}>
              {idx > 0 ? <br /> : null}
              {renderInline(line)}
            </Fragment>
          ))}
        </p>,
      );
    }
  }
  return <div className="chat-msg-md">{attachSuffix(blocks, suffix)}</div>;
}

export const SafeChatMarkdown = memo(SafeChatMarkdownInner);
