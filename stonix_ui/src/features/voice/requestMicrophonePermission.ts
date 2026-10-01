/** Browser mic permission (Roman Urdu errors match legacy useLiveKitVoice). */
export async function requestMicrophonePermission(): Promise<MediaStream> {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error('Browser mic API missing — HTTPS site use karein (Chrome/Edge).');
  }

  try {
    const perm = await navigator.permissions.query({ name: 'microphone' as PermissionName });
    if (perm.state === 'denied') {
      throw new Error(
        'Microphone BLOCKED hai. Address bar lock pe click → Site settings → Microphone = Allow, phir page refresh.',
      );
    }
  } catch (err) {
    if (err instanceof Error && err.message.includes('BLOCKED')) throw err;
  }

  try {
    return await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
      video: false,
    });
  } catch (err) {
    const name = err instanceof DOMException ? err.name : '';
    if (name === 'NotAllowedError' || name === 'PermissionDeniedError') {
      throw new Error(
        'Mic allow nahi hua. Address bar (lock) → Site settings → Microphone → Allow, phir Voice on dubara dabao.',
      );
    }
    if (name === 'NotFoundError') {
      throw new Error('Koi microphone device nahi mila — headset/mic check karo.');
    }
    throw new Error(`Mic open fail: ${err instanceof Error ? err.message : String(err)}`);
  }
}
