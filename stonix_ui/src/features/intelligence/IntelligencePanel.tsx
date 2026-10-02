import { useCallback, useEffect, useMemo, useState } from 'react';
import { ChevronDown, ChevronUp, Copy, FileJson, ImageIcon, Link2, Music2 } from 'lucide-react';
import clsx from 'clsx';
import { Panel } from '@/shared/ui/Panel';
import type { IntelligenceItem } from './types';
import { useBridgeMediaUrl } from './useBridgeMediaUrl';
import { isLanOnlyMediaUrl, resolveMediaUrl } from './mediaUrl';

interface IntelligencePanelProps {
  latest: IntelligenceItem | null;
  history: IntelligenceItem[];
}

const typeIcon = (type: IntelligenceItem['type']) => {
  switch (type) {
    case 'image':
      return ImageIcon;
    case 'link':
      return Link2;
    case 'audio':
      return Music2;
    default:
      return FileJson;
  }
};

const IntelligencePanel = ({ latest, history }: IntelligencePanelProps) => {
  const [collapsed, setCollapsed] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [copyHint, setCopyHint] = useState('');

  const active =
    (selectedId && history.find(item => item.id === selectedId)) || latest || history[0] || null;

  useEffect(() => {
    if (latest?.id) setSelectedId(latest.id);
  }, [latest?.id]);

  const strip = useMemo(() => history.slice(0, 20), [history]);

  const copyLanUrl = useCallback(async (url: string) => {
    try {
      await navigator.clipboard.writeText(url);
      setCopyHint('Copied');
      window.setTimeout(() => setCopyHint(''), 2000);
    } catch {
      setCopyHint('Copy failed');
    }
  }, []);

  if (!active && strip.length === 0) {
    return null;
  }

  return (
    <>
      <Panel
        className="intelligence-panel shrink-0"
        title="Intelligence / Media"
        action={
          <button
            type="button"
            className="icon-button text-command-muted"
            data-testid="intelligence-toggle"
            aria-expanded={!collapsed}
            onClick={() => setCollapsed(value => !value)}
          >
            {collapsed ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
          </button>
        }
      >
        {!collapsed && (
          <div className="intelligence-panel__body" data-testid="intelligence-panel">
            {active ? (
              <IntelligenceFeatured
                item={active}
                onImageClick={() => setLightboxOpen(true)}
                onCopyLan={copyLanUrl}
                copyHint={copyHint}
              />
            ) : (
              <p className="px-4 py-3 text-xs text-command-muted">No intelligence items yet.</p>
            )}

            {strip.length > 0 && (
              <div className="intelligence-history custom-scrollbar" data-testid="intelligence-history">
                {strip.map(item => {
                  const Icon = typeIcon(item.type);
                  return (
                    <button
                      key={item.id}
                      type="button"
                      data-testid={`intelligence-history-${item.id}`}
                      className={clsx(
                        'intelligence-history__chip',
                        active?.id === item.id && 'intelligence-history__chip--active',
                      )}
                      title={item.label || item.type}
                      onClick={() => setSelectedId(item.id)}
                    >
                      <Icon size={12} />
                      <span>{item.label || item.type}</span>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </Panel>

      {lightboxOpen && active?.type === 'image' && active.url && !isLanOnlyMediaUrl(resolveMediaUrl(active.url)) && (
        <div
          className="intelligence-lightbox"
          role="dialog"
          aria-modal
          data-testid="intelligence-lightbox"
          onClick={() => setLightboxOpen(false)}
          onKeyDown={event => {
            if (event.key === 'Escape') setLightboxOpen(false);
          }}
        >
          <MediaImage item={active} className="intelligence-lightbox__img" />
        </div>
      )}
    </>
  );
};

function IntelligenceFeatured({
  item,
  onImageClick,
  onCopyLan,
  copyHint,
}: {
  item: IntelligenceItem;
  onImageClick: () => void;
  onCopyLan: (url: string) => void;
  copyHint: string;
}) {
  const label = item.label || item.type;

  if (item.type === 'json') {
    const entries = Object.entries(item.data ?? {});
    return (
      <div className="intelligence-featured" data-testid="intelligence-json">
        <span className="panel-kicker">{label}</span>
        <dl className="intelligence-kv">
          {entries.length === 0 ? (
            <div className="text-xs text-command-muted">Empty JSON payload</div>
          ) : (
            entries.map(([key, value]) => (
              <div key={key} className="intelligence-kv__row">
                <dt>{key}</dt>
                <dd>{formatJsonValue(value)}</dd>
              </div>
            ))
          )}
        </dl>
      </div>
    );
  }

  if (item.type === 'link' && item.url) {
    const href = resolveMediaUrl(item.url);
    return (
      <div className="intelligence-featured" data-testid="intelligence-link">
        <span className="panel-kicker">{label}</span>
        <a
          className="intelligence-link"
          href={href}
          target="_blank"
          rel="noopener noreferrer"
        >
          {href}
        </a>
      </div>
    );
  }

  if (item.type === 'audio' && item.url) {
    return (
      <div className="intelligence-featured" data-testid="intelligence-audio">
        <span className="panel-kicker">{label}</span>
        <MediaAudio item={item} onCopyLan={onCopyLan} copyHint={copyHint} />
      </div>
    );
  }

  if (item.type === 'image' && item.url) {
    const lan = isLanOnlyMediaUrl(resolveMediaUrl(item.url));
    if (lan) {
      return (
        <LanOnlyNotice
          label={label}
          url={item.url}
          onCopy={onCopyLan}
          copyHint={copyHint}
          testId="intelligence-lan"
        />
      );
    }
    return (
      <div className="intelligence-featured" data-testid="intelligence-image">
        <span className="panel-kicker">{label}</span>
        <button type="button" className="intelligence-image-btn" onClick={onImageClick}>
          <MediaImage item={item} className="intelligence-image" />
        </button>
      </div>
    );
  }

  return (
    <div className="intelligence-featured px-4 py-3 text-xs text-command-muted">
      {label}: no renderable payload
    </div>
  );
}

function LanOnlyNotice({
  label,
  url,
  onCopy,
  copyHint,
  testId,
}: {
  label: string;
  url: string;
  onCopy: (url: string) => void;
  copyHint: string;
  testId: string;
}) {
  const resolved = resolveMediaUrl(url);
  return (
    <div className="intelligence-featured" data-testid={testId}>
      <span className="panel-kicker">{label}</span>
      <p className="text-xs text-command-muted">Sirf PC network par</p>
      <code className="intelligence-lan-url">{resolved}</code>
      <button
        type="button"
        className="intelligence-copy"
        data-testid="intelligence-copy-lan"
        onClick={() => onCopy(resolved)}
      >
        <Copy size={12} />
        Copy URL
      </button>
      {copyHint && <span className="text-[10px] text-command-accent">{copyHint}</span>}
    </div>
  );
}

function MediaImage({ item, className }: { item: IntelligenceItem; className?: string }) {
  const { displayUrl, lanOnly, loading, error, retryFromImageError } = useBridgeMediaUrl(
    item.url,
    item.type === 'image' && Boolean(item.url),
  );

  if (lanOnly) return null;

  if (loading && !displayUrl) {
    return <div className="text-xs text-command-muted">Loading media…</div>;
  }

  if (!displayUrl) {
    return <div className="text-xs text-command-muted">{error || 'Media unavailable'}</div>;
  }

  return (
    <img
      src={displayUrl}
      alt={item.label || 'Generated media'}
      className={className}
      data-testid="intelligence-media-image"
      onError={() => retryFromImageError()}
    />
  );
}

function MediaAudio({
  item,
  onCopyLan,
  copyHint,
}: {
  item: IntelligenceItem;
  onCopyLan: (url: string) => void;
  copyHint: string;
}) {
  const { displayUrl, lanOnly, loading, retryFromImageError } = useBridgeMediaUrl(
    item.url,
    Boolean(item.url),
  );

  if (lanOnly && item.url) {
    return (
      <LanOnlyNotice
        label={item.label || 'audio'}
        url={item.url}
        onCopy={onCopyLan}
        copyHint={copyHint}
        testId="intelligence-lan-audio"
      />
    );
  }

  if (loading && !displayUrl) {
    return <div className="text-xs text-command-muted">Loading audio…</div>;
  }

  if (!displayUrl) {
    return <div className="text-xs text-command-muted">Audio unavailable</div>;
  }

  return (
    <audio controls src={displayUrl} data-testid="intelligence-media-audio" onError={() => retryFromImageError()} />
  );
}

function formatJsonValue(value: unknown): string {
  if (value == null) return '—';
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
}

export default IntelligencePanel;
