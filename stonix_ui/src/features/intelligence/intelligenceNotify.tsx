import { toast } from '@/toast';

export function notifyIntelligenceImageView(personaName: string): void {
  toast(
    t => (
      <div className="flex flex-wrap items-center gap-1 text-sm">
        <span>
          {personaName} shared an image:{' '}
          <button
            type="button"
            className="font-semibold text-command-accent underline underline-offset-2"
            onClick={() => {
              window.location.hash = '/activity/media';
              toast.dismiss(t.id);
            }}
          >
            View
          </button>
        </span>
      </div>
    ),
    { duration: 8000, id: 'intelligence-image-toast' },
  );
}
