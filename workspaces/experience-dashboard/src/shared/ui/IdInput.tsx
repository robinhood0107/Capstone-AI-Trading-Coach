'use client';

import { useId } from 'react';

interface IdInputProps {
  label: string;
  hint: string;
  placeholder: string;
  value: string;
  onChange: (value: string) => void;
  pattern: RegExp;
  patternHint: string;
  presets?: { id: string; label: string }[];
}

/** Mirrors server ID validation for immediate form feedback. */
export function IdInput({
  label,
  hint,
  placeholder,
  value,
  onChange,
  pattern,
  patternHint,
  presets,
}: IdInputProps) {
  const inputId = useId();
  const touched = value.length > 0;
  const valid = pattern.test(value);

  return (
    <div className="rounded-tile bg-subtle px-5 py-4">
      <label htmlFor={inputId} className="text-eyebrow font-semibold uppercase text-faint">
        {label}
      </label>
      <p className="mt-1 text-[12px] leading-5 text-muted">{hint}</p>
      <input
        id={inputId}
        value={value}
        onChange={(event) => onChange(event.target.value.trim())}
        placeholder={placeholder}
        spellCheck={false}
        autoComplete="off"
        aria-invalid={touched && !valid}
        className={`mt-3 w-full rounded-control border bg-panel px-4 py-2 font-mono text-[13px] text-ink placeholder:text-faint focus:border-navy focus:outline-none ${
          touched && !valid ? 'border-block' : 'border-line'
        }`}
      />
      {touched && !valid ? <p className="mt-2 text-[12px] text-block">{patternHint}</p> : null}
      {presets && presets.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {presets.map((preset) => (
            <button
              key={preset.id}
              type="button"
              onClick={() => onChange(preset.id)}
              className="tap rounded-control border border-line bg-panel px-3 py-1 text-[12px] font-medium text-muted transition-colors hover:border-navy hover:text-navy"
            >
              {preset.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
