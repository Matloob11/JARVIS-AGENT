"""
# voice_fingerprint.py
Speaker Identification engine for JARVIS using SpeechBrain.
"""

import os
import shutil
import wave
import subprocess

import torch # pylint: disable=import-error
import torchaudio # pylint: disable=import-error
import torchaudio.transforms # pylint: disable=import-error
import numpy as np
import soundfile as sf

# Monkeypatch for torchaudio compatibility with SpeechBrain on newer versions
if not hasattr(torchaudio, "list_audio_backends"):
    torchaudio.list_audio_backends = lambda: []

# pylint: disable=wrong-import-position
import huggingface_hub
_original_hf_hub_download = huggingface_hub.hf_hub_download
def _patched_hf_hub_download(*args, **kwargs):
    if "use_auth_token" in kwargs:
        kwargs["token"] = kwargs.pop("use_auth_token")
    if "local_dir_use_symlinks" not in kwargs:
        kwargs["local_dir_use_symlinks"] = False
    return _original_hf_hub_download(*args, **kwargs)
huggingface_hub.hf_hub_download = _patched_hf_hub_download

_original_snapshot_download = huggingface_hub.snapshot_download
def _patched_snapshot_download(*args, **kwargs):
    if "use_auth_token" in kwargs:
        kwargs["token"] = kwargs.pop("use_auth_token")
    if "local_dir_use_symlinks" not in kwargs:
        kwargs["local_dir_use_symlinks"] = False
    return _original_snapshot_download(*args, **kwargs)
huggingface_hub.snapshot_download = _patched_snapshot_download

if os.name == 'nt':
    def _patched_symlink(src, dst, target_is_directory=False, **kwargs):
        try:
            if os.path.isdir(src):
                if os.path.exists(dst):
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
            else:
                if os.path.exists(dst):
                    os.remove(dst)
                shutil.copy2(src, dst)
        except OSError:
            pass
    
    os.symlink = _patched_symlink

from speechbrain.inference.speaker import SpeakerRecognition # pylint: disable=import-error
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("VOICE-ID")
# pylint: enable=wrong-import-position

class VoiceFingerprintEngine:
    def __init__(self, master_voice_path: str):
        """
        Initializes the Speaker Recognition model and enrolls the master voice.
        """
        self.master_voice_path = master_voice_path
        self.model_source = "speechbrain/spkrec-ecapa-voxceleb"
        self.save_dir = os.path.join(os.getcwd(), "pretrained_models", "spkrec-ecapa-voxceleb")
        
        logger.info("Initializing Speaker Identification Engine...")
        try:
            # Manually download snapshot to avoid symlink issues on Windows
            key_file = os.path.join(self.save_dir, "hyperparams.yaml")
            if not os.path.exists(key_file):
                logger.info("Model files missing. Downloading to %s (No-Symlink Mode)...", self.save_dir)
                os.makedirs(self.save_dir, exist_ok=True)
                huggingface_hub.snapshot_download(
                    repo_id=self.model_source,
                    local_dir=self.save_dir,
                    local_dir_use_symlinks=False
                )
            else:
                logger.info("Model files found in %s", self.save_dir)

            self.verification = SpeakerRecognition.from_hparams(
                source=self.save_dir, # Use the local directory directly
                savedir=self.save_dir,
                run_opts={"device": "cpu"}
            )
            
            if not os.path.exists(master_voice_path):
                logger.error("Master voice file not found at %s", master_voice_path)
                self.master_embedding = None
            else:
                logger.info("Enrolling Master Voice from %s", master_voice_path)
                self.master_embedding = self._get_embedding(master_voice_path)
                logger.info("✅ Master Voice Identity Loaded.")
                
        except (RuntimeError, ValueError, IOError) as e:
            logger.error("Failed to initialize Voice ID Engine: %s", e)
            self.verification = None
            self.master_embedding = None

    def _get_embedding(self, audio_path: str):
        """Extracts speaker embedding from audio file with automatic format conversion if needed."""
        try:
            # Check if it's a valid WAV, if not, try to convert using ffmpeg
            is_valid_wav = False
            try:
                with wave.open(audio_path, 'rb') as wf:
                    is_valid_wav = True
            except (OSError, IOError, wave.Error):
                is_valid_wav = False

            if not is_valid_wav:
                logger.info("Non-WAV format detected for %s. Attempting FFmpeg conversion...", audio_path)
                temp_wav = audio_path + ".converted.wav"
                try:
                    subprocess.run(
                        ['ffmpeg', '-y', '-i', audio_path, '-ar', '16000', '-ac', '1', temp_wav],
                        check=True, capture_output=True
                    )
                    audio_path = temp_wav
                except (subprocess.SubprocessError, RuntimeError, IOError) as e:
                    logger.error("FFmpeg conversion failed: %s", e)
                    return None
            
            with wave.open(audio_path, 'rb') as wf:
                fs = wf.getframerate()
                n_channels = wf.getnchannels()
                n_frames = wf.getnframes()
                sampwidth = wf.getsampwidth()
                
                if sampwidth != 2:
                    # Fallback for non-16bit if soundfile is present
                    data, fs = sf.read(audio_path)
                    signal = torch.from_numpy(data.copy()).float()
                else:
                    frames = wf.readframes(n_frames)
                    data = np.frombuffer(frames, dtype=np.int16)
                    signal = torch.from_numpy(data.copy()).float() / 32768.0
                    
                    if n_channels > 1:
                        # Reshape interleaved data: [L, R, L, R...] -> [C, T]
                        signal = signal.view(-1, n_channels).transpose(0, 1)
                
                # Cleanup temp file if created
                if ".converted.wav" in audio_path and os.path.exists(audio_path):
                    wf.close() # Close before deleting
                    os.remove(audio_path)
                
                # Reshape and Resample
                if len(signal.shape) == 1:
                    signal = signal.unsqueeze(0)
                elif len(signal.shape) == 2 and signal.shape[1] < signal.shape[0]:
                    signal = signal.transpose(0, 1)
                
                if fs != 16000:
                    resampler = torchaudio.transforms.Resample(fs, 16000)
                    signal = resampler(signal)
                
                if signal.shape[0] > 1:
                    signal = torch.mean(signal, dim=0, keepdim=True)
                
                logger.info("Signal loaded for %s: Shape=%s, SampleRate=%d", audio_path, signal.shape, fs)

            embedding = self.verification.encode_batch(signal)
            # Ensure 3D (1, 1, 192) or 2D (1, 192)
            return embedding
        except (RuntimeError, ValueError, IOError) as e:
            logger.error("Embedding extraction failed for %s: %s", audio_path, e)
            return None

    def verify_segment(self, segment_path: str, threshold: float = 0.65) -> tuple[bool, float]:
        """
        Verifies if the audio segment matches the master voice.
        Returns: (is_match, score)
        """
        if self.verification is None or self.master_embedding is None:
            logger.warning("Voice ID Engine not initialized correctly. Denying access.")
            return False, 0.0

        try:
            test_embedding = self._get_embedding(segment_path)
            if test_embedding is None:
                return False, 0.0
            
            # SpeechBrain's verify_batch expects raw signals.
            # Since we pre-compute embeddings, we should compare them directly.
            try:
                # Standardize to (1, EmbeddingSize)
                # Ensure they are not None and are tensors
                m_emb = self.master_embedding
                t_emb = test_embedding
                
                if m_emb is None or t_emb is None:
                    logger.error("Embedding is None during comparison.")
                    return False, 0.0
                
                master_emb = m_emb.squeeze()
                test_emb = t_emb.squeeze()
                
                # pylint: disable=multiple-statements,not-callable
                if len(master_emb.shape) == 1: master_emb = master_emb.unsqueeze(0)
                if len(test_emb.shape) == 1: test_emb = test_emb.unsqueeze(0)

                # Calculate Cosine Similarity
                similarity = torch.nn.functional.cosine_similarity(master_emb, test_emb)
                # pylint: enable=multiple-statements,not-callable
                score_val = float(similarity[0].item())
            except (RuntimeError, ValueError, TypeError) as e:
                logger.error("Similarity calculation failed: %s", e)
                return False, 0.0
            
            is_match = score_val >= threshold
            
            logger.info("Voice Security Check: Score=%.4f | Threshold=%.2f | Match=%s", score_val, threshold, is_match)
            return is_match, score_val
            
        except (RuntimeError, ValueError, IOError) as e:
            logger.error("Verification logic failed: %s", e)
            return False, 0.0 # SECURE BY DEFAULT: Deny on failure

    def verify_bytes(self, audio_bytes: bytes, sample_rate: int = 16000, threshold: float = 0.70) -> tuple[bool, float]:
        """Verifies raw audio bytes (16-bit PCM)."""
        # Ensure audio is at least 1.0 seconds long to avoid model padding errors and low-confidence matches
        if len(audio_bytes) < (sample_rate * 1.0 * 2): # 2 bytes per sample for 16-bit
            return False, 0.0

        temp_path = "tmp_segment.wav"
        try:
            # Use wave module to save instead of torchaudio.save to avoid backend issues
            # pylint: disable=no-member
            with wave.open(temp_path, 'wb') as wf: # type: ignore
                wf.setnchannels(1)
                wf.setsampwidth(2) # 16-bit
                wf.setframerate(sample_rate)
                wf.writeframes(audio_bytes)
            # pylint: enable=no-member
            
            result = self.verify_segment(temp_path, threshold)
            return result
        except (ValueError, RuntimeError, IOError) as e:
            # Short fragments or noise might cause padding errors in the model
            # We deny these to be safe.
            if "Padding size" in str(e):
                logger.debug("Segment too short for model padding.")
            else:
                logger.error("Bytes verification logic failed: %s", e)
            return False, 0.0 # SECURE BY DEFAULT
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
        
        return False, 0.0 # Final fallback return 

# Singleton instance
voice_id_engine = VoiceFingerprintEngine("d:/Personal-Assistant-main/data/identity/master_voice.wav")
