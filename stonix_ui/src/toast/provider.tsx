import { ReactNode } from 'react';
import { Toaster } from 'react-hot-toast';

export const Toast = ({ children }: { children: ReactNode }) => (
  <>
    {children}
    <Toaster
      position="bottom-center"
      toastOptions={{
        duration: 2500,
        className: '!rounded-lg !border !border-white/10 !bg-[#0B1116]/95 !text-[#E6EDF3] !shadow-2xl !backdrop-blur-xl',
      }}
    />
  </>
);
