import { ShieldCheck } from 'lucide-react';
import JobsList from '@/features/tools/JobsList';
import ToolLogList from '@/features/tools/ToolLogList';
import type { JobEntry } from '@/features/jobs/useJobs';
import type { ToolLogEntry } from '@/features/tools/useToolLogs';
import { useAgentStatusStore } from '@/features/status/agentStatusStore';
import { PanelOfflineHint } from '@/shared/ui/PanelState';

interface ToolsTabProps {
  jobs: JobEntry[];
  toolLogs: ToolLogEntry[];
  onCancelJob: (jobId: string) => void;
  onOpenJobArtifact: (jobId: string) => void;
  onOpenSim: () => void;
}

const ToolsTab = ({
  jobs,
  toolLogs,
  onCancelJob,
  onOpenJobArtifact,
  onOpenSim,
}: ToolsTabProps) => {
  const agentOnline = useAgentStatusStore().status.online;
  return (
  <div className="panel-tab-pane tools-tab" data-testid="tools-tab-panel">
    <PanelOfflineHint online={agentOnline} />
    <div className="tools-tab__workspaces">
      <span className="tools-tab__workspaces-label">Workspaces</span>
      <button
        type="button"
        className="tools-tab__sim-btn n-btn quiet sm"
        title="SIM lookup workspace"
        onClick={onOpenSim}
      >
        <ShieldCheck size={15} aria-hidden />
        SIM lookup
      </button>
    </div>
    <JobsList jobs={jobs} onCancel={onCancelJob} onOpenArtifact={onOpenJobArtifact} />
    <ToolLogList rows={toolLogs} />
  </div>
  );
};

export default ToolsTab;
