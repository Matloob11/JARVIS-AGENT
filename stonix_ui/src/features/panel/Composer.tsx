import {
  FormEvent,
  KeyboardEvent,
  useCallback,
  useEffect,
  useRef,
  type FocusEvent,
} from 'react';
import { Mic, Paperclip, Send, X, Zap } from 'lucide-react';
import { toast } from '@/toast';
import { useLiveKit } from '@/features/voice/LiveKitProvider';
import {
  composerMicControlDisabled,
  composerPushToTalkBlocked,
  useVoiceUi,
} from '@/features/voice/voiceState';
import {
  composerPlaceholder,
  composerTaskPlaceholder,
  voiceStatusLine,
  type PersonaKey,
} from '@/shared/persona';
import { useSwarmMode } from './useSwarmMode';

const PTT_MIN_MS = 350;
const TEXTAREA_MAX_PX = 140;

type ComposerProps = {
  persona: PersonaKey;
  text: string;
  onTextChange: (value: string) => void;
  onSubmit: (value: string) => void;
  onFocusCapture?: (event: FocusEvent<HTMLFormElement>) => void;
  onBlurCapture?: (event: FocusEvent<HTMLFormElement>) => void;
};

const Composer = ({
  persona,
  text,
  onTextChange,
  onSubmit,
  onFocusCapture,
  onBlurCapture,
}: ComposerProps) => {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const voice = useLiveKit();
  const { swarmMode, setSwarmMode } = useSwarmMode();
  const voiceUi = useVoiceUi(persona, {
    isSpeaking: false,
    isThinking: false,
    isUserSpeaking: false,
  });
  const micDisabled = composerMicControlDisabled(voiceUi.state);
  const pttHeld = voice.pushToTalkActive;
  const micLabel = pttHeld ? 'Listening…' : 'Use microphone';

  const resizeField = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, TEXTAREA_MAX_PX)}px`;
  }, []);

  useEffect(() => {
    resizeField();
  }, [text, resizeField]);

  const send = useCallback(() => {
    const trimmed = text.trim();
    if (!trimmed) return;
    onSubmit(trimmed);
    onTextChange('');
    requestAnimationFrame(resizeField);
  }, [onSubmit, onTextChange, resizeField, text]);

  const onFormSubmit = (event: FormEvent) => {
    event.preventDefault();
    send();
  };

  const onFieldKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  };

  const onAttachClick = () => {
    toast('Attachments are not available yet');
  };

  const tryPushToTalkStart = useCallback(
    (event?: KeyboardEvent<HTMLButtonElement>) => {
      if (event && 'repeat' in event && event.repeat) return;
      if (event && 'key' in event && event.type === 'keydown') {
        const key = event.key;
        if (key !== ' ' && key !== 'Enter') return;
        event.preventDefault();
      }
      if (micDisabled) return;
      if (composerPushToTalkBlocked(voiceUi.state)) {
        toast(voiceStatusLine(voiceUi.state, persona));
        return;
      }
      void voice.beginPushToTalk();
    },
    [micDisabled, persona, voice, voiceUi.state],
  );

  const tryPushToTalkEnd = useCallback(async () => {
    if (!voice.pushToTalkActive) return;
    const held = await voice.endPushToTalk();
    if (held > 0 && held < PTT_MIN_MS) {
      toast('Hold to talk');
    }
  }, [voice]);

  const placeholder = swarmMode ? composerTaskPlaceholder(persona) : composerPlaceholder(persona);

  return (
    <form
      onSubmit={onFormSubmit}
      className={`command-input neon-composer panel-composer${swarmMode ? ' neon-composer--task' : ''}`}
      data-testid="chat-composer"
      onFocusCapture={onFocusCapture}
      onBlurCapture={onBlurCapture}
      autoComplete="off"
    >
      {swarmMode ? (
        <span className="neon-composer__task-chip">
          <Zap size={12} fill="currentColor" aria-hidden />
          Task mode
          <button
            type="button"
            className="neon-composer__task-chip-x"
            aria-label="Leave task mode"
            onClick={() => setSwarmMode(false)}
          >
            <X size={10} strokeWidth={3} aria-hidden />
          </button>
        </span>
      ) : null}

      <div className="neon-composer__field edge">
        <textarea
          ref={textareaRef}
          rows={1}
          value={text}
          onChange={event => onTextChange(event.target.value)}
          onKeyDown={onFieldKeyDown}
          placeholder={placeholder}
          aria-label={placeholder}
          className="neon-composer__textarea command-input__field"
          data-testid="chat-composer-input"
        />
        <button
          type="submit"
          className="neon-composer__send command-input__button"
          title="Send command"
          aria-label="Send command"
          data-testid="chat-composer-send"
        >
          <Send size={22} fill="currentColor" strokeWidth={0} aria-hidden />
        </button>
      </div>

      <button
        type="button"
        className="neon-composer__attach"
        data-testid="composer-attach"
        aria-disabled="true"
        aria-label="Attach (coming soon)"
        title="Attachments are not available yet"
        onClick={onAttachClick}
      >
        <Paperclip size={18} strokeWidth={1.5} aria-hidden />
      </button>

      <span className="neon-composer__kbd-hint hint-kbd" aria-hidden="true">
        <span className="lab-l">Shift + Enter for new line</span>
        <span className="lab-s">Shift+Enter: new line</span>
      </span>

      <button
        type="button"
        className="neon-composer__mic"
        data-testid="composer-mic"
        disabled={micDisabled}
        aria-pressed={pttHeld}
        aria-label={`Use microphone: hold to talk${pttHeld ? ' (listening)' : ''}`}
        title="Push to talk: hold while you speak (the same voice-room mic as Start Listening)"
        onClick={event => event.preventDefault()}
        onPointerDown={() => tryPushToTalkStart()}
        onPointerUp={() => void tryPushToTalkEnd()}
        onPointerLeave={() => void tryPushToTalkEnd()}
        onPointerCancel={() => void tryPushToTalkEnd()}
        onKeyDown={event => tryPushToTalkStart(event)}
        onKeyUp={() => void tryPushToTalkEnd()}
        onBlur={() => void tryPushToTalkEnd()}
      >
        <Mic size={18} fill={pttHeld ? 'currentColor' : 'none'} aria-hidden />
        <span className="neon-composer__mic-label">{micLabel}</span>
      </button>
    </form>
  );
};

export default Composer;
