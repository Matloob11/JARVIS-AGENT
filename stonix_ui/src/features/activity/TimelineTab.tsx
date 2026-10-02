import clsx from 'clsx';
import { Icon } from '@/shared/ui/icons/Icon';
import { useActivityFeedRows, type ActivityFeedRow } from './feedStore';
import { AlertsCard } from './AlertsCard';
import type { SystemAlert, VortexLog } from '@/features/command-center/hooks/useCommandCenter';
import {
  TIMELINE_FILTERS,
  rowMatchesTimelineFilter,
  setTimelineFilter,
  useTimelineFilter,
} from './timelineFilterStore';

function formatActivityTime(ts: number): string {
  return new Intl.DateTimeFormat(undefined, {
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  }).format(new Date(ts));
}

function rowClass(level: string, kind: string): string {
  if (kind === 'device') return 'k-device';
  if (level === 'ok') return 'k-ok';
  if (level === 'error') return 'k-err';
  if (level === 'warn') return 'k-warn';
  if (level === 'neutral') return 'k-neutral';
  return 'k-info';
}

function rowHref(row: ActivityFeedRow): string {
  return row.href ?? '#/activity/timeline';
}

function TimelineRow({ row }: { row: ActivityFeedRow }) {
  const title =
    row.repeat && row.repeat > 1 ? (
      <>
        {row.title}
        <span className="activity-timeline__repeat">×{row.repeat}</span>
      </>
    ) : (
      row.title
    );

  return (
    <li
      className={clsx('activity-timeline__row', rowClass(row.level, row.kind))}
      data-testid="timeline-row"
      data-kind={row.kind}
      data-level={row.level}
    >
      <span className="activity-timeline__dot" aria-hidden />
      <a className="activity-timeline__link" href={rowHref(row)}>
        <b>{title}</b>
        {row.subtitle ? <span>{row.subtitle}</span> : null}
      </a>
      <time dateTime={new Date(row.ts).toISOString()}>{formatActivityTime(row.ts)}</time>
    </li>
  );
}

export type TimelineTabProps = {
  systemAlerts: SystemAlert[];
  logs: VortexLog[];
};

export function TimelineTab({ systemAlerts, logs }: TimelineTabProps) {
  const filter = useTimelineFilter();
  const rows = useActivityFeedRows();
  const filtered = rows.filter(row => rowMatchesTimelineFilter(row, filter));

  return (
    <div className="activity-timeline">
      <AlertsCard systemAlerts={systemAlerts} logs={logs} />

      <div className="activity-timeline__filters n-chips" role="group" aria-label="Filter events">
        {TIMELINE_FILTERS.map(chip => (
          <button
            key={chip.id}
            type="button"
            className="activity-chip n-chip"
            aria-pressed={filter === chip.id}
            onClick={() => setTimelineFilter(chip.id)}
          >
            {chip.label}
          </button>
        ))}
      </div>

      {filtered.length === 0 ? (
        <div className="activity-timeline__empty n-empty">
          <Icon name="activity" className="n-ic" aria-hidden />
          <b>{rows.length ? 'Nothing for this filter' : 'No events yet'}</b>
          <span>
            {rows.length
              ? 'Pick All to see every event.'
              : 'Events appear here as JARVIS & ANNA work: voice, devices, approvals and tool runs.'}
          </span>
        </div>
      ) : (
        <ol className="activity-timeline__list n-tl">
          {filtered.map(row => (
            <TimelineRow key={row.id} row={row} />
          ))}
        </ol>
      )}
    </div>
  );
}

export default TimelineTab;
