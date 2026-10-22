import { describe, expect, it } from 'vitest';
import {
  nextCaptureScale,
  nextJpegQuality,
  shouldStopCameraShare,
} from './cameraVisionAck';

describe('cameraVisionAck (C-60)', () => {
  it('stops share on agent_offline / no_agent', () => {
    expect(shouldStopCameraShare({ status: 'agent_offline' })).toBe(true);
    expect(shouldStopCameraShare({ status: 'no_agent' })).toBe(true);
    expect(shouldStopCameraShare({ status: 'sent' })).toBe(false);
  });

  it('lowers JPEG quality on too_large', () => {
    expect(nextJpegQuality(0.8, { status: 'rejected', reason: 'too_large' })).toBe(0.65);
    expect(nextJpegQuality(0.4, { status: 'rejected', reason: 'too_large' })).toBe(0.35);
    expect(nextJpegQuality(0.8, { status: 'sent' })).toBeNull();
  });

  it('shrinks scale only after quality is at floor', () => {
    expect(
      nextCaptureScale(1, 0.5, { status: 'rejected', reason: 'too_large' }),
    ).toBeNull();
    expect(
      nextCaptureScale(1, 0.35, { status: 'rejected', reason: 'too_large' }),
    ).toBe(0.85);
  });
});
