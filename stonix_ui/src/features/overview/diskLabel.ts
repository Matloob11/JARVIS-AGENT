/** Inner caption for "Disk (…)" so "Disk C" never becomes "Disk (Disk C)". */
export function diskCaptionInner(raw: string | undefined | null, fallback: string): string {
  if (raw == null) return fallback;
  let inner = String(raw).trim();
  if (!inner) return fallback;
  for (let i = 0; i < 3; i += 1) {
    const next = inner
      .replace(/^disk\b/i, '')
      .replace(/^\s*[:([]+/, '')
      .replace(/[)\]]+$/, '')
      .trim();
    if (next === inner) break;
    inner = next;
  }
  return inner || fallback;
}

export function formatDiskCaption(raw: string | undefined | null, fallback: string): string {
  return `Disk (${diskCaptionInner(raw, fallback)})`;
}
