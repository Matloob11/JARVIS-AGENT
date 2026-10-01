export type CameraBadge = 'off' | 'livekit' | 'not_linked' | 'fallback' | 'local_preview';

export type CameraState = {
  enabled: boolean;
  sharing: boolean;
  badge: CameraBadge;
};

export const defaultCameraState: CameraState = {
  enabled: false,
  sharing: false,
  badge: 'off',
};

export function cameraBadgeLabel(badge: CameraBadge): string {
  switch (badge) {
    case 'livekit':
      return 'Live to agent (LiveKit)';
    case 'not_linked':
      return 'Not linked — Take over';
    case 'fallback':
      return 'Fallback (bridge)';
    case 'local_preview':
      return 'Local preview only';
    default:
      return 'Offline';
  }
}
