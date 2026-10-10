'use client';

import type { ReactNode } from 'react';

type FilterChipProps = {
  selected?: boolean;
  onClick?: () => void;
  children: ReactNode;
  icon?: ReactNode;
  className?: string;
};

export function FilterChip({ selected, onClick, children, icon, className }: FilterChipProps) {
  const cls = ['chip', selected ? 'selected' : '', className].filter(Boolean).join(' ');
  return (
    <button className={cls} onClick={onClick} type="button">
      {selected && !icon && (
        <span className="material-symbols-outlined">check</span>
      )}
      {icon}
      {children}
    </button>
  );
}

type InputChipProps = {
  onRemove?: () => void;
  children: ReactNode;
  removeLabel?: string;
  className?: string;
};

export function InputChip({ onRemove, children, removeLabel = 'Убрать', className }: InputChipProps) {
  const cls = ['chip', 'chip--input', className].filter(Boolean).join(' ');
  return (
    <span className={cls}>
      {children}
      {onRemove && (
        <button onClick={onRemove} aria-label={removeLabel} type="button">
          <span className="material-symbols-outlined">close</span>
        </button>
      )}
    </span>
  );
}

type AssistChipProps = {
  onClick?: () => void;
  children: ReactNode;
  icon?: ReactNode;
  className?: string;
};

export function AssistChip({ onClick, children, icon, className }: AssistChipProps) {
  const cls = ['chip', 'chip--assist', className].filter(Boolean).join(' ');
  return (
    <button className={cls} onClick={onClick} type="button">
      {icon}
      {children}
    </button>
  );
}
