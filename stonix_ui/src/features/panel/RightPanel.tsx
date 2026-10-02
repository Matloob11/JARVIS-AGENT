import { FormEvent, ReactNode, useState } from 'react';
import { Hexagon, Send } from 'lucide-react';
import type { ChatMessage, LiveCaption } from '@/features/chat/types';
import type { JobEntry } from '@/features/jobs/useJobs';
import MemoryList from '@/features/memory/MemoryList';
import type { MemoryItem } from '@/features/memory/useMemories';
import TasksPanel from '@/features/tasks/TasksPanel';
import type { TaskItem, TaskSource } from '@/features/tasks/useTasks';
import type { ToolLogEntry } from '@/features/tools/useToolLogs';
import { personaAccent } from '@/shared/theme/commandTheme';
import { composerPlaceholder, type PersonaKey } from '@/shared/persona';
type VortexLog = {
  category?: string;
  text: string;
  timestamp: number;
};
import ChatList, { ChatCaptionsStrip } from './ChatList';
import PanelTabs, { type PanelMainTab } from './PanelTabs';
import ToolsTab from './ToolsTab';

type LegacyTab = 'logs' | 'notes' | 'tasks';

interface RightPanelProps {
  persona: PersonaKey;
  messages: ChatMessage[];
  logs: VortexLog[];
  memories: MemoryItem[];
  onClearMemory: () => void;
  toolLogs: ToolLogEntry[];
  jobs: JobEntry[];
  onCancelJob: (jobId: string) => void;
  onOpenJobArtifact: (jobId: string) => void;
  tasks: TaskItem[];
  onCancelTask: (id: string, source: TaskSource) => void;
  onRefreshTasks: () => void;
  onSend: (text: string) => void;
  onSendSwarm: (task: string) => void;
  onFeedback: (messageId: string, score: 1 | -1) => void;
  liveCaptions?: { user: LiveCaption | null; assistant: LiveCaption | null };
  fallbackTranscription?: string;
  voiceConnected?: boolean;
  hasMore?: boolean;
  onLoadOlder?: () => void;
  onRetry?: (id: string) => void;
  isThinking?: boolean;
  approvalsWaiting?: number;
  onOpenSim: () => void;
  confirmSlot?: ReactNode;
}

const legacyTabs: LegacyTab[] = ['logs', 'notes', 'tasks'];

const formatLogTime = (timestamp: number) =>
  new Date(timestamp * 1000).toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });

const LogsPane = ({ logs }: { logs: VortexLog[] }) => {
  if (logs.length === 0) {
    return (
      <div className="flex h-full min-h-[180px] flex-col items-center justify-center text-center">
        <p className="text-sm font-medium text-command-text">No system logs yet</p>
        <p className="mt-2 max-w-[260px] text-xs leading-5 text-command-muted">
          Waiting for backend bridge events.
        </p>
      </div>
    );
  }
  return (
    <div className="space-y-2">
      {logs.map((log, index) => (
        <div key={`${log.category}-${log.timestamp}-${index}`} className="data-row">
          <div className="flex items-center justify-between gap-3">
            <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-command-accent">
              {log.category || 'system'}
            </span>
            <span className="font-mono text-[10px] text-ink-muted-70">{formatLogTime(log.timestamp)}</span>
          </div>
          <p className="mt-2 text-xs leading-5 text-ink-body-80">{log.text}</p>
        </div>
      ))}
    </div>
  );
};

const ApprovalsPane = () => (
  <div className="approvals-tab-pane" data-testid="approvals-panel">
    <div className="approvals-tab-pane__empty">
      <p className="approvals-tab-pane__title">No approvals waiting</p>
      <p>Guarded tools always ask first. Pending requests appear in the confirm card above.</p>
    </div>
  </div>
);

const RightPanel = ({
  persona,
  messages,
  logs,
  memories,
  onClearMemory,
  toolLogs,
  jobs,
  onCancelJob,
  onOpenJobArtifact,
  tasks,
  onCancelTask,
  onRefreshTasks,
  onSend,
  onSendSwarm,
  onFeedback,
  liveCaptions,
  fallbackTranscription,
  voiceConnected = false,
  hasMore = false,
  onLoadOlder,
  onRetry,
  isThinking = false,
  approvalsWaiting = 0,
  onOpenSim,
  confirmSlot,
}: RightPanelProps) => {
  const [mainTab, setMainTab] = useState<PanelMainTab>('chat');
  const [legacyTab, setLegacyTab] = useState<LegacyTab | null>(null);
  const [text, setText] = useState('');
  const [swarmMode, setSwarmMode] = useState(false);
  const accent = personaAccent(persona);

  const liveUser =
    voiceConnected && liveCaptions?.user?.text
      ? liveCaptions.user
      : !voiceConnected && fallbackTranscription
        ? { role: 'user' as const, text: fallbackTranscription, final: false }
        : null;
  const liveAssistant =
    voiceConnected && liveCaptions?.assistant?.text ? liveCaptions.assistant : null;

  const selectMainTab = (tab: PanelMainTab) => {
    setMainTab(tab);
    setLegacyTab(null);
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!text.trim()) return;
    if (swarmMode) {
      onSendSwarm(text);
    } else {
      onSend(text);
    }
    setText('');
    setMainTab('chat');
    setLegacyTab(null);
  };

  const sendSuggestion = (value: string) => {
    onSend(value);
    setMainTab('chat');
    setLegacyTab(null);
  };

  const showCaptions =
    mainTab === 'chat' && legacyTab == null && Boolean(liveUser || liveAssistant);

  let feed: ReactNode;
  if (legacyTab === 'logs') {
    feed = <LogsPane logs={logs} />;
  } else if (legacyTab === 'notes') {
    feed = <MemoryList rows={memories} onClearConfirmed={onClearMemory} />;
  } else if (legacyTab === 'tasks') {
    feed = (
      <TasksPanel rows={tasks} onCancelConfirmed={onCancelTask} onRefresh={onRefreshTasks} />
    );
  } else if (mainTab === 'chat') {
    feed = (
      <ChatList
        activePersona={persona}
        messages={messages}
        hasMore={hasMore}
        onLoadOlder={onLoadOlder}
        onRetry={onRetry}
        onFeedback={onFeedback}
        onSuggest={sendSuggestion}
        isThinking={isThinking}
      />
    );
  } else if (mainTab === 'approvals') {
    feed = <ApprovalsPane />;
  } else {
    feed = (
      <ToolsTab
        jobs={jobs}
        toolLogs={toolLogs}
        onCancelJob={onCancelJob}
        onOpenJobArtifact={onOpenJobArtifact}
        onOpenSim={onOpenSim}
      />
    );
  }

  const wrappedFeed =
    legacyTab != null || mainTab === 'approvals' || mainTab === 'tools' ? (
      <div className="panel-feed__scroll custom-scrollbar">{feed}</div>
    ) : (
      feed
    );

  return (
    <section
      className="command-panel chat-panel neon-panel layout-zone layout-zone--chat"
      data-testid="chat-panel"
    >
      <PanelTabs active={mainTab} approvalsWaiting={approvalsWaiting} onChange={selectMainTab} />

      <div className="panel-legacy-tabs" data-legacy>
        {legacyTabs.map(tab => (
          <button
            key={tab}
            type="button"
            className={
              legacyTab === tab ? 'panel-legacy-tab panel-legacy-tab--active' : 'panel-legacy-tab'
            }
            onClick={() => {
              setLegacyTab(tab);
            }}
          >
            {tab}
          </button>
        ))}
      </div>

      {confirmSlot ? <div className="panel-confirm-slot">{confirmSlot}</div> : null}

      <div className="panel-feed">{wrappedFeed}</div>

      {showCaptions ? (
        <ChatCaptionsStrip
          activePersona={persona}
          liveUser={liveUser}
          liveAssistant={liveAssistant}
        />
      ) : null}

      <form
        onSubmit={submit}
        className="command-input panel-composer"
        onFocusCapture={() => document.documentElement.classList.add('typing')}
        onBlurCapture={event => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
            document.documentElement.classList.remove('typing');
          }
        }}
      >
        <button
          type="button"
          className={
            swarmMode ? 'command-input__swarm command-input__swarm--on' : 'command-input__swarm'
          }
          data-testid="swarm-mode-toggle"
          data-active={swarmMode ? 'true' : 'false'}
          title={swarmMode ? 'Swarm on — Send runs swarm task' : 'Swarm off — normal chat'}
          onClick={() => setSwarmMode(prev => !prev)}
        >
          <Hexagon size={14} />
          <span>Swarm</span>
        </button>
        <input
          value={text}
          onChange={event => setText(event.target.value)}
          placeholder={composerPlaceholder(persona)}
          className="command-input__field"
        />
        <button
          type="submit"
          disabled={!text.trim()}
          className={`command-input__button ${text.trim() ? accent.text : 'text-ink-muted-40'}`}
          title="Send command"
        >
          <Send size={16} />
        </button>
      </form>
    </section>
  );
};

export default RightPanel;
