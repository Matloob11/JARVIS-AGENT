import clsx from 'clsx';
import type { ReactNode } from 'react';

export type TileProps = {
  className?: string;
  children?: ReactNode;
};

export function Tile({ className, children }: TileProps) {
  return <div className={clsx('n-tile edge', className)}>{children}</div>;
}
