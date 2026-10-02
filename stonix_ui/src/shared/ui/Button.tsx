import clsx from 'clsx';
import type { ButtonHTMLAttributes, ReactNode } from 'react';

export type NeonButtonVariant = 'default' | 'primary' | 'danger' | 'quiet';
export type NeonButtonSize = 'default' | 'sm' | 'xs' | 'icon';

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: NeonButtonVariant;
  size?: NeonButtonSize;
  children?: ReactNode;
};

export function Button({
  variant = 'default',
  size = 'default',
  className,
  type = 'button',
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={clsx(
        'n-btn',
        variant === 'primary' && 'primary',
        variant === 'danger' && 'danger',
        variant === 'quiet' && 'quiet',
        size === 'sm' && 'sm',
        size === 'xs' && 'xs',
        size === 'icon' && 'icon',
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}
