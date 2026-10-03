import type { StatusKind } from '@/shared/ui/StatusShape';
import type { PillTone } from '@/shared/ui/Pill';
import type { DesktopStatusSnapshot } from './desktopStatusStore';

export type LaptopChipShape =
  | 'unknown'
  | 'full'
  | 'stale'
  | 'offline'
  | 'locked'
  | 'panic'
  | 'readonly';

export type LaptopChipPresentation = {
  shape: LaptopChipShape;
  short: string;
  long: string;
  statusKind: StatusKind;
  tone: PillTone;
};

function readOnlyMode(mode: DesktopStatusSnapshot['mode']): boolean {
  return mode === 'read' || mode === 'off';
}

export function deriveLaptopChipPresentation(
  desktop: DesktopStatusSnapshot,
): LaptopChipPresentation {
  if (!desktop.seen) {
    return {
      shape: 'unknown',
      short: 'unknown',
      long: 'Unknown',
      statusKind: 'unknown',
      tone: 'off',
    };
  }

  if (desktop.state === 'offline' || !desktop.online) {
    return {
      shape: 'offline',
      short: 'Offline',
      long: 'Offline',
      statusKind: 'off',
      tone: 'off',
    };
  }

  if (desktop.state === 'stale') {
    return {
      shape: 'stale',
      short: 'Stale',
      long: 'Stale',
      statusKind: 'warn',
      tone: 'warn',
    };
  }

  if (desktop.state === 'panic' || desktop.panic) {
    return {
      shape: 'panic',
      short: 'Panic',
      long: 'Panic',
      statusKind: 'err',
      tone: 'err',
    };
  }

  if (desktop.state === 'locked' || desktop.locked) {
    return {
      shape: 'locked',
      short: 'Locked',
      long: 'Locked',
      statusKind: 'warn',
      tone: 'warn',
    };
  }

  if (readOnlyMode(desktop.mode)) {
    return {
      shape: 'readonly',
      short: 'Read-only',
      long: 'Read-only',
      statusKind: 'dev',
      tone: 'dev',
    };
  }

  return {
    shape: 'full',
    short: 'Full',
    long: 'Full control',
    statusKind: 'ok',
    tone: 'ok',
  };
}

export function laptopStopVisible(desktop: DesktopStatusSnapshot): boolean {
  return desktop.seen && desktop.online;
}

export function laptopStopDisabled(desktop: DesktopStatusSnapshot): boolean {
  return readOnlyMode(desktop.mode) || desktop.state === 'offline' || !desktop.online;
}
