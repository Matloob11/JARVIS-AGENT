import { useMemo, useState } from 'react';
import type { MemoryItem, MemoryRole, MemorySource } from './useMemories';

interface MemoryListProps {
  rows: MemoryItem[];
  onClearConfirmed: () => void;
}

function relativeTime(tsMs: number): string {
  const sec = Math.max(0, Math.floor((Date.now() - tsMs) / 1000));
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 48) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  return `${day}d ago`;
}

function rolePill(role?: MemoryRole): string {
  if (role === 'user') return 'memory-pill memory-pill--user';
  if (role === 'assistant') return 'memory-pill memory-pill--assistant';
  return 'memory-pill memory-pill--muted';
}

function sourcePill(source?: MemorySource): string {
  if (source === 'extractor') return 'memory-pill memory-pill--extractor';
  if (source === 'fact') return 'memory-pill memory-pill--fact';
  if (source === 'vector') return 'memory-pill memory-pill--vector';
  return 'memory-pill memory-pill--muted';
}

const MemoryList = ({ rows, onClearConfirmed }: MemoryListProps) => {
  const [query, setQuery] = useState('');
  const [confirmOpen, setConfirmOpen] = useState(false);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter(row => row.content.toLowerCase().includes(q));
  }, [query, rows]);

  const requestClear = () => setConfirmOpen(true);
  const cancelClear = () => setConfirmOpen(false);
  const confirmClear = () => {
    setConfirmOpen(false);
    onClearConfirmed();
  };

  return (
    <div className="memory-panel" data-testid="memory-tab-panel">
      <div className="memory-panel__toolbar">
        <input
          type="search"
          className="memory-search"
          data-testid="memory-search"
          placeholder="Memory search..."
          aria-label="Memory search"
          value={query}
          onChange={event => setQuery(event.target.value)}
        />
      </div>

      {filtered.length === 0 ? (
        <div className="tool-panel-empty" data-testid="memory-empty">
          <p className="text-sm font-medium text-command-text">
            {rows.length === 0 ? 'No memories synced yet' : 'No matches'}
          </p>
          <p className="mt-2 text-xs text-command-muted">
            {rows.length === 0
              ? 'Waiting for backend bridge events.'
              : 'Try a different search term.'}
          </p>
        </div>
      ) : (
        <div className="space-y-2" data-testid="memory-list">
          {filtered.map(row => (
            <div
              key={row.id}
              className="memory-row"
              data-testid="memory-row"
              data-id={row.id}
            >
              <div className="memory-row__head">
                <span className={rolePill(row.role)}>
                  {row.role ?? 'unknown'}
                </span>
                <span className={sourcePill(row.source)}>
                  {row.source ?? 'unknown'}
                </span>
                <span className="memory-row__meta">{relativeTime(row.ts)}</span>
              </div>
              <p className="memory-row__content">{row.content}</p>
            </div>
          ))}
        </div>
      )}

      <div className="memory-panel__footer">
        {confirmOpen ? (
          <div className="memory-clear-confirm" data-testid="memory-clear-confirm-panel">
            <p className="memory-clear-confirm__text">
              Short-term saved memory delete karni hai? Ye action undo nahi hota.
            </p>
            <div className="memory-clear-confirm__actions">
              <button
                type="button"
                className="memory-clear-btn memory-clear-btn--ghost"
                data-testid="memory-clear-cancel"
                onClick={cancelClear}
              >
                Cancel
              </button>
              <button
                type="button"
                className="memory-clear-btn memory-clear-btn--danger"
                data-testid="memory-clear-confirm"
                onClick={confirmClear}
              >
                Haan, saaf karo
              </button>
            </div>
          </div>
        ) : (
          <button
            type="button"
            className="memory-clear-btn memory-clear-btn--primary"
            data-testid="memory-clear-btn"
            onClick={requestClear}
          >
            Saved short-term memory saaf karo
          </button>
        )}
        <p className="memory-panel__note" lang="ur-Latn">
          Live voice conversation par asar nahi hota.
        </p>
      </div>
    </div>
  );
};

export default MemoryList;
