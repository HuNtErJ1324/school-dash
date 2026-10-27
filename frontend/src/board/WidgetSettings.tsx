import { useEffect, useId, useRef } from "react";

import type { Course, Kind } from "../api";
import { courseStyle } from "../courses";
import { useCourses } from "../hooks";
import { definition, KIND_NAME, type Field } from "../widgets/registry";
import type { WidgetInstance } from "./boardState";

type Options = Record<string, unknown>;

/** A form generated from the widget's fields. Changes apply as you make them. */
export function WidgetSettings({ instance, onChange, onClose }: {
  instance: WidgetInstance;
  onChange: (next: WidgetInstance) => void;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const headingId = useId();
  const def = definition(instance.type);
  const options: Options = { ...def.defaults, ...instance.options };
  const courses = useCourses();

  useEffect(() => {
    ref.current?.showModal();
  }, []);

  const set = (key: string, value: unknown) => onChange({ ...instance, options: { ...instance.options, [key]: value } });

  return (
    <dialog ref={ref} className="settings" aria-labelledby={headingId} onClose={onClose}>
      <form method="dialog" className="settings-form">
        <h2 id={headingId} className="settings-title">
          {def.title} settings
        </h2>
        <label className="field">
          <span className="field-label">Title</span>
          <input
            type="text"
            value={instance.title ?? ""}
            placeholder={def.title}
            maxLength={60}
            onChange={(e) => onChange({ ...instance, title: e.target.value || undefined })}
          />
        </label>
        {def.fields.map((field) => (
          <FieldInput key={field.key} field={field} value={options[field.key]} courses={courses.data ?? []} onChange={(v) => set(field.key, v)} />
        ))}
        <div className="settings-actions">
          <button type="button" className="text-button" onClick={() => onChange({ id: instance.id, type: instance.type })}>
            Reset to defaults
          </button>
          <button type="submit" className="pill-button">
            Done
          </button>
        </div>
      </form>
    </dialog>
  );
}

function FieldInput({ field, value, courses, onChange }: {
  field: Field<Options>;
  value: unknown;
  courses: Course[];
  onChange: (value: unknown) => void;
}) {
  const help = field.help && <span className="field-help">{field.help}</span>;
  switch (field.type) {
    case "number":
      return (
        <label className="field">
          <span className="field-label">{field.label}</span>
          <input
            type="number"
            inputMode="numeric"
            min={field.min}
            max={field.max}
            value={Number(value)}
            onChange={(e) => {
              const n = Math.round(Number(e.target.value));
              if (e.target.value !== "" && Number.isFinite(n)) onChange(Math.min(field.max, Math.max(field.min, n)));
            }}
          />
          {help}
        </label>
      );
    case "boolean":
      return (
        <label className="field field-check">
          <input type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />
          <span>{field.label}</span>
        </label>
      );
    case "select":
      return (
        <label className="field">
          <span className="field-label">{field.label}</span>
          <select value={String(value)} onChange={(e) => onChange(e.target.value)}>
            {field.choices.map((c) => (
              <option key={c.value} value={c.value}>
                {c.label}
              </option>
            ))}
          </select>
          {help}
        </label>
      );
    case "kinds": {
      const chosen = new Set(value as Kind[]);
      return (
        <fieldset className="field">
          <legend className="field-label">{field.label}</legend>
          <div className="checks">
            {field.choices.map((kind) => (
              <label key={kind} className="field-check">
                <input
                  type="checkbox"
                  checked={chosen.has(kind)}
                  // Keep at least one, or the widget has nothing to show.
                  disabled={chosen.has(kind) && chosen.size === 1}
                  onChange={(e) => onChange(field.choices.filter((k) => (k === kind ? e.target.checked : chosen.has(k))))}
                />
                <span>{KIND_NAME[kind]}</span>
              </label>
            ))}
          </div>
          {help}
        </fieldset>
      );
    }
    case "courses": {
      const chosen = new Set(value as string[]);
      return (
        <fieldset className="field">
          <legend className="field-label">{field.label}</legend>
          {courses.length === 0 ? (
            <span className="field-help">No courses yet. They appear once a source syncs.</span>
          ) : (
            <div className="checks">
              {courses.map((c) => (
                <label key={c.id} className="field-check" style={courseStyle(c)}>
                  <input
                    type="checkbox"
                    checked={chosen.has(c.id)}
                    onChange={(e) => onChange(courses.filter((k) => (k.id === c.id ? e.target.checked : chosen.has(k.id))).map((k) => k.id))}
                  />
                  <span className="course-code">{c.code}</span>
                </label>
              ))}
            </div>
          )}
          {help ?? <span className="field-help">Leave all unchecked to include every course.</span>}
        </fieldset>
      );
    }
  }
}
