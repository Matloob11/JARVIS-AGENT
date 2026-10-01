import type { CameraState } from './components/cameraState';
import type { ToolLogLastError } from '@/features/tools/useToolLogs';
import type { AgentState, VoiceRoomState } from '@/features/voice/useVoiceRoom';
import { toEpochMs } from '@/shared/contracts/events';

export interface ModuleHealthSlice {
  ok?: boolean;
  detail?: string;
}

export interface ModuleHealthVision extends ModuleHealthSlice {
  model?: string;
  camera_source?: string;
}

export interface ModuleHealthTools extends ModuleHealthSlice {
  count?: number;
  failures_1h?: number;
}

export interface ModuleHealthSnapshot {
  voice?: ModuleHealthSlice;
  gemini?: ModuleHealthSlice;
  vision?: ModuleHealthVision;
  memory?: ModuleHealthSlice;
  tools?: ModuleHealthTools;
  voice_id?: ModuleHealthSlice & { state?: string; enrolled?: boolean };
  ts?: number;
}

function readSlice(raw: unknown): ModuleHealthSlice | undefined {
  if (!raw || typeof raw !== 'object') return undefined;
  const data = raw as Record<string, unknown>;
  const slice: ModuleHealthSlice = {};
  if (typeof data.ok === 'boolean') slice.ok = data.ok;
  if (data.detail != null && String(data.detail).trim()) slice.detail = String(data.detail).trim();
  return Object.keys(slice).length ? slice : undefined;
}

export function normalizeModuleHealth(raw: unknown): ModuleHealthSnapshot | null {
  if (!raw || typeof raw !== 'object') return null;
  const data = raw as Record<string, unknown>;
  const visionRaw = data.vision;
  let vision: ModuleHealthVision | undefined;
  if (visionRaw && typeof visionRaw === 'object') {
    const v = visionRaw as Record<string, unknown>;
    vision = { ...readSlice(v) };
    if (v.model != null) vision.model = String(v.model);
    if (v.camera_source != null) vision.camera_source = String(v.camera_source);
  }
  const toolsRaw = data.tools;
  let tools: ModuleHealthTools | undefined;
  if (toolsRaw && typeof toolsRaw === 'object') {
    const t = toolsRaw as Record<string, unknown>;
    tools = { ...readSlice(t) };
    if (typeof t.count === 'number') tools.count = t.count;
    if (typeof t.failures_1h === 'number') tools.failures_1h = t.failures_1h;
  }
  const ts = toEpochMs(data.ts);
  const snapshot: ModuleHealthSnapshot = {
    voice: readSlice(data.voice),
    gemini: readSlice(data.gemini),
    vision,
    memory: readSlice(data.memory),
    tools,
    ts: ts ?? undefined,
  };
  if (data.voice_id && typeof data.voice_id === 'object') {
    const vid = data.voice_id as Record<string, unknown>;
    snapshot.voice_id = {
      ...readSlice(vid),
      state: vid.state != null ? String(vid.state) : undefined,
      enrolled: vid.enrolled === true,
    };
  }
  return snapshot;
}

export function mergeModuleHealth(
  prev: ModuleHealthSnapshot | null,
  next: ModuleHealthSnapshot | null,
): ModuleHealthSnapshot | null {
  if (!next) return prev;
  if (!prev) return next;
  return {
    voice: next.voice ?? prev.voice,
    gemini: next.gemini ?? prev.gemini,
    vision: next.vision ? { ...prev.vision, ...next.vision } : prev.vision,
    memory: next.memory ?? prev.memory,
    tools: next.tools ? { ...prev.tools, ...next.tools } : prev.tools,
    voice_id: next.voice_id ? { ...prev.voice_id, ...next.voice_id } : prev.voice_id,
    ts: next.ts ?? prev.ts,
  };
}

function formatSyncAge(lastMs: number, nowMs: number): string {
  const sec = Math.max(0, Math.floor((nowMs - lastMs) / 1000));
  if (sec < 60) return `sync ${sec}s`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `sync ${min}m`;
  return `sync ${Math.floor(min / 60)}h`;
}

export function voiceFlowStatus(input: {
  roomState: VoiceRoomState;
  micEnabled: boolean;
  isMuted: boolean;
  agentState: AgentState;
}): string {
  const { roomState, micEnabled, isMuted, agentState } = input;
  if (roomState !== 'connected') {
    return roomState;
  }
  if (isMuted || !micEnabled) return 'muted';
  if (agentState && agentState !== 'unknown') return agentState;
  return 'unknown';
}

export function visionFlowStatus(
  cameraState: CameraState | undefined,
  vision: ModuleHealthVision | undefined,
): string {
  if (vision?.ok === false) {
    return vision.detail?.trim() ? `fault · ${vision.detail}` : 'fault';
  }
  const cam = cameraState;
  if (!cam) return 'unknown';
  if (!cam.enabled) return 'off';
  if (cam.sharing) {
    const src = vision?.camera_source?.trim();
    return src && src !== 'none' ? `active · ${src}` : 'active';
  }
  if (cam.badge === 'not_linked') return 'blocked';
  if (cam.badge === 'fallback') return 'fallback';
  if (cam.badge === 'local_preview') return 'local';
  return 'standby';
}

export function memoryFlowStatus(
  lastMemorySyncMs: number | null,
  memory: ModuleHealthSlice | undefined,
  nowMs: number,
): string {
  if (memory?.ok === false) {
    return memory.detail?.trim() ? `fault · ${memory.detail}` : 'fault';
  }
  if (lastMemorySyncMs != null) {
    return formatSyncAge(lastMemorySyncMs, nowMs);
  }
  if (memory?.ok === true) return 'linked';
  return 'unknown';
}

export function toolsFlowStatus(
  runningCount: number,
  lastError: ToolLogLastError | null,
  toolsHealth: ModuleHealthTools | undefined,
): string {
  if (runningCount > 0) {
    return runningCount === 1 ? '1 running' : `${runningCount} running`;
  }
  if (lastError?.tool) {
    return `error · ${lastError.tool}`;
  }
  if (toolsHealth?.ok === false) {
    return toolsHealth.detail?.trim() ? `fault · ${toolsHealth.detail}` : 'fault';
  }
  if (toolsHealth?.ok === true) return 'idle';
  return 'unknown';
}

export function coreFlowStatus(input: {
  agentOnline?: boolean;
  agentPaused?: boolean;
  agentSession?: string;
}): string {
  const { agentOnline, agentPaused, agentSession } = input;
  if (agentPaused === true) return 'paused';
  if (agentOnline === false) return 'offline';
  if (agentOnline === true) {
    if (agentSession?.trim()) {
      const short = agentSession.length > 10 ? `${agentSession.slice(0, 8)}…` : agentSession;
      return `online · ${short}`;
    }
    return 'online';
  }
  return 'unknown';
}

export function isFlowStatusUnknown(label: string): boolean {
  return label === 'unknown';
}
