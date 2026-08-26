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

/**
 * 서버가 강제하는 ID 형식을 화면에서 먼저 검사한다.
 * 형식이 틀린 요청을 굳이 보내서 400을 받는 대신, 왜 틀렸는지 그 자리에서 알려주는 편이 낫다.
 */
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
    <div className="border border-line bg-panel px-5 py-4">
      <label htmlFor={inputId} className="font-mono text-eyebrow uppercase text-faint">
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
        className={`mt-3 w-full border px-3 py-2 font-mono text-[13px] text-ink ${
          touched && !valid ? 'border-block' : 'border-line'
        }`}
      />
      {touched && !valid ? (
        <p className="mt-2 text-[12px] text-block">{patternHint}</p>
      ) : null}
      {presets && presets.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {presets.map((preset) => (
            <button
              key={preset.id}
              type="button"
              onClick={() => onChange(preset.id)}
              className="border border-line px-2.5 py-1 text-[12px] text-muted hover:border-navy hover:text-navy"
            >
              {preset.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
