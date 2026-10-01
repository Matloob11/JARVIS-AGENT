import JobsList from '@/features/tools/JobsList';
import type { JobEntry } from './useJobs';

interface JobsPanelProps {
  jobs: JobEntry[];
  onCancel: (jobId: string) => void;
  onOpenArtifact: (jobId: string) => void;
}

/** Thin wrapper for dashboard panels that need jobs only. */
const JobsPanel = (props: JobsPanelProps) => <JobsList {...props} />;

export default JobsPanel;
