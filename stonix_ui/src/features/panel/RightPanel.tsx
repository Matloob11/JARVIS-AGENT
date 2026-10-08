import { ReactNode, useState } from 'react';
import type { ChatMessage, LiveCaption } from '@/features/chat/types';
import type { JobEntry } from '@/features/jobs/useJobs';
import type { MemoryItem } from '@/features/memory/useMemories';
import type { TaskItem, TaskSource } from '@/features/tasks/useTasks';
import type { ToolLogEntry } from '@/features/tools/useToolLogs';
import type { PersonaKey } from '@/shared/persona';
import Composer from './Composer';
import type { ConfirmDecision, QueuedConfirm } from '@/features/confirm/useConfirmQueue';
import ChatList, { ChatCaptionsStrip } from './ChatList';
import PanelTabs, { type PanelMainTab } from './PanelTabs';
import ApprovalsTab from './ApprovalsTab';
import ToolsTab from './ToolsTab';
import { useSwarmMode } from './useSwarmMode';

interface RightPanelProps {
  persona: PersonaKey;
  messages: ChatMessage[];
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
  awaitingReply?: boolean;
  approvalsWaiting?: number;
  onOpenSim: () => void;
  confirmSlot?: ReactNode;
  confirmBar?: ReactNode;
  confirmQueue?: QueuedConfirm[];
  confirmDecisions?: ConfirmDecision[];
}

const RightPanel = ({
  persona,
  messages,
  memories: _memories,
  onClearMemory: _onClearMemory,
  toolLogs,
  jobs,
  onCancelJob,
  onOpenJobArtifact,
  tasks: _tasks,
  onCancelTask: _onCancelTask,
  onRefreshTasks: _onRefreshTasks,
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
  awaitingReply = false,
  approvalsWaiting = 0,
  onOpenSim,
  confirmSlot,
  confirmBar,
  confirmQueue = [],
  confirmDecisions = [],
}: RightPanelProps) => {
  const [mainTab, setMainTab] = useState<PanelMainTab>('chat');
  const [text, setText] = useState('');
  const { swarmMode } = useSwarmMode();

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
  };

  const submitText = (value: string) => {
    if (swarmMode) {
      onSendSwarm(value);
    } else {
      onSend(value);
    }
    setText('');
    setMainTab('chat');
  };

  const sendSuggestion = (value: string) => {
    onSend(value);
    setMainTab('chat');
  };

  const showCaptions = mainTab === 'chat' && Boolean(liveUser || liveAssistant);

  let feed: ReactNode;
  if (mainTab === 'chat') {
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
        awaitingReply={awaitingReply}
      />
    );
  } else if (mainTab === 'approvals') {
    feed = <ApprovalsTab queue={confirmQueue} decisions={confirmDecisions} />;
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
    mainTab === 'approvals' || mainTab === 'tools' ? (
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

      {confirmBar ? confirmBar : null}
      {confirmSlot ? <div className="panel-confirm-slot">{confirmSlot}</div> : null}

      <div className="panel-feed">{wrappedFeed}</div>

      {showCaptions ? (
        <ChatCaptionsStrip
          activePersona={persona}
          liveUser={liveUser}
          liveAssistant={liveAssistant}
        />
      ) : null}

      <Composer
        persona={persona}
        text={text}
        onTextChange={setText}
        onSubmit={submitText}
        onFocusCapture={() => document.documentElement.classList.add('typing')}
        onBlurCapture={event => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
            document.documentElement.classList.remove('typing');
          }
        }}
      />
    </section>
  );
};

export default RightPanel;
