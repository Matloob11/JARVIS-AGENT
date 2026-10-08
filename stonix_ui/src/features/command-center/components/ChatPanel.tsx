import RightPanel from '@/features/panel/RightPanel';
import type { ConfirmDecision, QueuedConfirm } from '@/features/confirm/useConfirmQueue';
import type { ReactNode } from 'react';
import { Message } from '../hooks/useCommandCenter';
import type { ChatMessage, LiveCaption } from '@/features/chat/types';
import type { MemoryItem } from '@/features/memory/useMemories';
import type { JobEntry } from '@/features/jobs/useJobs';
import type { ToolLogEntry } from '@/features/tools/useToolLogs';
import type { TaskItem, TaskSource } from '@/features/tasks/useTasks';

interface ChatPanelProps {
  persona: 'jarvis' | 'anna';
  messages: Message[];
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
  chatMediaChip?: { label: string } | null;
  approvalsWaiting?: number;
  onOpenSim: () => void;
  confirmSlot?: ReactNode;
  confirmBar?: ReactNode;
  confirmQueue?: QueuedConfirm[];
  confirmDecisions?: ConfirmDecision[];
}

const ChatPanel = (props: ChatPanelProps) => <RightPanel {...props} />;

export default ChatPanel;
export type { ChatMessage };
