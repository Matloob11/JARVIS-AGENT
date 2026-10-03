import { Fragment, type ReactNode } from 'react';

function renderInline(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const re = /(`+)([^`]+)\1|\*\*([^*]+)\*\*|\*([^*]+)\*|_([^_]+)_/g;
  let last = 0;
  let key = 0;
  let match: RegExpExecArray | null;
  while ((match = re.exec(text))) {
    if (match.index > last) {
      nodes.push(<Fragment key={`t${key++}`}>{text.slice(last, match.index)}</Fragment>);
    }
    if (match[2] != null) {
      nodes.push(<code key={`c${key++}`}>{match[2]}</code>);
    } else if (match[3] != null) {
      nodes.push(<strong key={`b${key++}`}>{match[3]}</strong>);
    } else {
      const italic = match[4] ?? match[5] ?? '';
      nodes.push(<em key={`i${key++}`}>{italic}</em>);
    }
    last = match.index + match[0].length;
  }
  if (last < text.length) {
    nodes.push(<Fragment key={`t${key++}`}>{text.slice(last)}</Fragment>);
  }
  return nodes;
}

function isUl(line: string): boolean {
  return /^\s*[-*]\s+/.test(line);
}

function isOl(line: string): boolean {
  return /^\s*\d+\.\s+/.test(line);
}

function stripList(line: string): string {
  return line.replace(/^\s*(?:[-*]|\d+\.)\s+/, '');
}

export function SafeChatMarkdown({ text }: { text: string }) {
  const lines = String(text ?? '').replace(/\r\n/g, '\n').split('\n');
  const blocks: ReactNode[] = [];
  let i = 0;
  let key = 0;
  while (i < lines.length) {
    if (isUl(lines[i])) {
      const items: string[] = [];
      while (i < lines.length && isUl(lines[i])) {
        items.push(stripList(lines[i]));
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
      const items: string[] = [];
      while (i < lines.length && isOl(lines[i])) {
        items.push(stripList(lines[i]));
        i += 1;
      }
      blocks.push(
        <ol key={`ol${key++}`}>
          {items.map((item, idx) => (
            <li key={idx}>{renderInline(item)}</li>
          ))}
        </ol>,
      );
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i] !== '' && !isUl(lines[i]) && !isOl(lines[i])) {
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
    } else if (i < lines.length && lines[i] === '') {
      i += 1;
    }
  }
  return <div className="chat-msg-md">{blocks}</div>;
}
