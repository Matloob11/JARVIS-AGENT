import { ReactNode, useEffect, useState } from 'react';
import toast, { Toaster, type Toast as HotToast } from 'react-hot-toast';
import { StatusShape, type StatusKind } from '@/shared/ui/StatusShape';

function kindFromToast(t: HotToast): StatusKind {
  if (t.type === 'success') return 'ok';
  if (t.type === 'error') return 'err';
  if (t.type === 'loading') return 'busy';
  return 'acc';
}

function toastMessage(t: HotToast): ReactNode {
  if (typeof t.message === 'function') {
    return t.message(t);
  }
  return t.message;
}

function ToastShell({ t }: { t: HotToast }) {
  const raw = typeof t.message === 'string' ? t.message : '';
  const ur = /nahi|gaya|koshish|jawab/i.test(raw);
  return (
    <div
      className="neon-toast"
      role="status"
      lang={ur ? 'ur-Latn' : undefined}
      data-visible={t.visible ? 'true' : 'false'}
    >
      <span aria-hidden>
        <StatusShape s={kindFromToast(t)} label={t.type} hideLabel />
      </span>
      <div className="neon-toast__body">{toastMessage(t)}</div>
      <button
        type="button"
        className="neon-toast__dismiss"
        aria-label="Dismiss"
        onClick={() => toast.dismiss(t.id)}
      >
        ×
      </button>
    </div>
  );
}

export const Toast = ({ children }: { children: ReactNode }) => {
  const [phone, setPhone] = useState(false);

  useEffect(() => {
    const mq = window.matchMedia('(max-width: 1023px)');
    const apply = () => setPhone(mq.matches);
    apply();
    mq.addEventListener('change', apply);
    return () => mq.removeEventListener('change', apply);
  }, []);

  return (
    <>
      {children}
      <Toaster
        position={phone ? 'top-center' : 'bottom-center'}
        containerClassName={phone ? 'neon-toaster neon-toaster--ph' : 'neon-toaster neon-toaster--dk'}
        gutter={8}
        toastOptions={{ duration: 2500 }}
      >
        {t => <ToastShell t={t} />}
      </Toaster>
    </>
  );
};
