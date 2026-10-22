/** Pure helpers for vision_frame ack handling (C-60). */

export type VisionFrameAck = {
  status?: string;
  reason?: string;
};

/** Stop socket fallback sharing when the bridge says the agent is gone. */
export function shouldStopCameraShare(ack: VisionFrameAck | undefined): boolean {
  return ack?.status === 'agent_offline' || ack?.status === 'no_agent';
}

/** Shrink JPEG quality on too_large; null when no change. Floor 0.35. */
export function nextJpegQuality(
  quality: number,
  ack: VisionFrameAck | undefined,
): number | null {
  if (ack?.status !== 'rejected' || ack.reason !== 'too_large') return null;
  const next = Math.round((quality - 0.15) * 100) / 100;
  return Math.max(0.35, next);
}

/** Shrink capture scale on too_large after quality floor; null when no change. */
export function nextCaptureScale(
  scale: number,
  quality: number,
  ack: VisionFrameAck | undefined,
): number | null {
  if (ack?.status !== 'rejected' || ack.reason !== 'too_large') return null;
  if (quality > 0.35) return null;
  const next = Math.round((scale - 0.15) * 100) / 100;
  return Math.max(0.4, next);
}
