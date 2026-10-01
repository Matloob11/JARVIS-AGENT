import type { JobEntry } from '@/features/jobs/useJobs';

interface JobsListProps {
  jobs: JobEntry[];
  onCancel: (jobId: string) => void;
  onOpenArtifact: (jobId: string) => void;
}

function barWidth(percent: number | null): string {
  if (percent == null || !Number.isFinite(percent)) return '0%';
  return `${Math.min(100, Math.max(0, percent))}%`;
}

const JobsList = ({ jobs, onCancel, onOpenArtifact }: JobsListProps) => {
  if (jobs.length === 0) {
    return (
      <div className="tool-panel-empty tool-panel-empty--compact" data-testid="jobs-empty">
        <p className="text-xs text-command-muted">No background jobs</p>
      </div>
    );
  }

  return (
    <div className="jobs-list" data-testid="jobs-list">
      <p className="jobs-list__title">Jobs</p>
      <div className="space-y-2">
        {jobs.map(job => {
          const running = job.status === 'running' || job.status === 'queued';
          const finished = job.status === 'done';
          const hasArtifact = finished && Boolean(job.artifact?.path || job.artifact?.name);
          return (
            <div
              key={job.job_id}
              className="job-row"
              data-testid="job-row"
              data-status={job.status}
              data-id={job.job_id}
            >
              <div className="job-row__head">
                <span className="job-row__title">{job.title}</span>
                <span className="job-row__status">{job.status}</span>
              </div>
              <p className="job-row__tool">{job.tool}</p>
              {(job.percent != null || running) && (
                <div className="job-row__bar" data-testid="job-progress-bar">
                  <div
                    className="job-row__bar-fill"
                    style={{ width: barWidth(job.percent) }}
                    data-testid="job-progress-fill"
                    data-percent={job.percent ?? 0}
                  />
                </div>
              )}
              {job.message && <p className="job-row__message">{job.message}</p>}
              <div className="job-row__actions">
                {running && (
                  <button
                    type="button"
                    className="job-row__btn job-row__btn--cancel"
                    data-testid="job-cancel"
                    onClick={() => onCancel(job.job_id)}
                  >
                    Cancel
                  </button>
                )}
                {hasArtifact && (
                  <button
                    type="button"
                    className="job-row__btn"
                    data-testid="job-open-artifact"
                    onClick={() => onOpenArtifact(job.job_id)}
                  >
                    PC par kholo
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default JobsList;
