import clsx from 'clsx';
import type { IconName } from './Sprite';

type IconProps = {
  name: IconName;
  className?: string;
  filled?: boolean;
};

/** Reference ink-box icons (spec v2 §4.8). */
export function Icon({ name, className, filled }: IconProps) {
  return (
    <svg
      aria-hidden="true"
      className={clsx('n-ic block h-5 w-5 flex-none overflow-visible', filled && 'fill', className)}
    >
      <use href={`#i-${name}`} />
    </svg>
  );
}
