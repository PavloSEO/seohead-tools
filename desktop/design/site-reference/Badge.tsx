import type { ReactNode } from 'react';

type Variant = 'neutral' | 'info' | 'success' | 'warning' | 'error' | 'accent';
type DiffLevel = 1 | 2 | 3;

type BaseProps = { children: ReactNode; className?: string };

export function Badge({
  variant,
  children,
  className,
}: BaseProps & { variant?: Variant }) {
  const cls = ['badge', variant ? `badge--${variant}` : '', className].filter(Boolean).join(' ');
  return <span className={cls}>{children}</span>;
}

export function DifficultyBadge({
  level,
  children,
  className,
}: BaseProps & { level: DiffLevel }) {
  const cls = [`badge--diff`, `badge--diff-${level}`, className].filter(Boolean).join(' ');
  return <span className={cls}>{children}</span>;
}

export function CountBadge({ children, className }: BaseProps) {
  const cls = ['badge', 'badge--count', className].filter(Boolean).join(' ');
  return <span className={cls}>{children}</span>;
}

export function DotBadge({ className }: { className?: string }) {
  const cls = ['badge--count', 'badge--dot', className].filter(Boolean).join(' ');
  return <span className={cls} />;
}
