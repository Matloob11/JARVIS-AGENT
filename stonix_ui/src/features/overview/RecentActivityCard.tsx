import clsx from 'clsx';
import { useActivityFeedRows, type ActivityFeedRow } from '@/features/activity/feedStore';
import { Icon } from '@/shared/ui/icons/Icon';

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

function ActivityRow({ row }: { row: ActivityFeedRow }) {
  const title =
    row.repeat && row.repeat > 1 ? (
      <>
        {row.title}
        <span className="overview-activity-row__repeat">×{row.repeat}</span>
      </>
    ) : (
      row.title
    );

  return (
    <li
      className={clsx('overview-activity-row', rowClass(row.level, row.kind))}
      data-testid="activity-row"
      data-kind={row.kind}
      data-level={row.level}
    >
      <span className="overview-activity-row__dot" aria-hidden />
      <a className="overview-activity-row__link" href={rowHref(row)}>
        <span className="overview-activity-row__title">{title}</span>
        {row.subtitle ? <span className="overview-activity-row__sub">{row.subtitle}</span> : null}
        <span className="overview-activity-row__time">{formatActivityTime(row.ts)}</span>
      </a>
    </li>
  );
}

const RecentActivityCard = () => {
  const rows = useActivityFeedRows();
  const visible = rows.slice(0, 4);

  return (
    <section className="overview-activity-card n-card edge" data-testid="activity-card">
      <header className="n-hd">
        <Icon name="clock" className="overview-activity-card__icon" />
        <div className="min-w-0">
          <h3>Recent Activity</h3>
          <p>Latest events from JARVIS &amp; ANNA</p>
        </div>
        <div className="n-hd-act">
          <a
            className="n-btn xs icon"
            href="#/activity/timeline"
            aria-label="Open timeline"
            data-testid="activity-open"
          >
            <Icon name="chev-r" />
          </a>
        </div>
      </header>

      <div className="overview-activity-card__line" aria-hidden />

      {visible.length === 0 ? (
        <p className="overview-activity-card__empty">No activity yet</p>
      ) : (
        <ol className="overview-activity-card__list">
          {visible.map(row => (
            <ActivityRow key={row.id} row={row} />
          ))}
        </ol>
      )}
    </section>
  );
};

export default RecentActivityCard;
