import type { ButtonHTMLAttributes, ReactNode } from 'react';

type Variant = 'primary' | 'secondary' | 'quiet' | 'danger';
type Size = 'sm' | 'md';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  children: ReactNode;
}

/**
 * 화면 전체에서 button 형태를 하나로 고정한다. authorization은 여전히 서버가 갖는다.
 *
 * 모양은 rounded-control(10px)로 고정한다. 라운드는 목록·표·패널이 아니라
 * 실제로 누르는 대상에만 쓴다는 원칙에 따라, 여기서만 정의하고 다른 곳은 각지게 둔다.
 */
const VARIANT: Record<Variant, string> = {
  primary:
    'border-brand bg-brand text-on-brand hover:opacity-90 disabled:border-line disabled:bg-line disabled:text-faint',
  secondary:
    'border-rule bg-panel text-ink hover:bg-surface disabled:border-line disabled:bg-panel disabled:text-faint',
  quiet:
    'border-transparent bg-transparent text-navy underline underline-offset-2 hover:text-ink disabled:text-faint disabled:no-underline',
  danger:
    'border-block bg-block text-on-brand hover:opacity-90 disabled:border-line disabled:bg-line disabled:text-faint',
};

const SIZE: Record<Size, string> = {
  sm: 'px-3 py-1 text-[12px]',
  md: 'px-4 py-1.5 text-[13px]',
};

export function Button({
  variant = 'secondary',
  size = 'md',
  className = '',
  type = 'button',
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={`tap inline-flex items-center justify-center gap-2 rounded-control border font-medium tracking-tight transition-colors disabled:cursor-not-allowed ${VARIANT[variant]} ${SIZE[size]} ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}

/** Keeps blocked actions visible with the server-provided reason. */
export function BlockedAction({
  blocked,
  reason,
  children,
}: {
  blocked: boolean;
  reason?: string | null;
  children: ReactNode;
}) {
  if (!blocked) return <>{children}</>;
  return (
    <div className="flex flex-col gap-1">
      <div aria-disabled="true">{children}</div>
      {reason ? (
        <p role="status" className="text-[12px] leading-5 text-block">
          {reason}
        </p>
      ) : null}
    </div>
  );
}
