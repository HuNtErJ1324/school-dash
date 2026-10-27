import { Component, useMemo, type ErrorInfo, type ReactNode } from "react";

import { CloseIcon, GearIcon, GripIcon } from "../ui/icons";
import { definition } from "../widgets/registry";
import type { WidgetInstance } from "./boardState";

export function WidgetFrame({ instance, editing, onSettings, onRemove }: {
  instance: WidgetInstance;
  editing: boolean;
  onSettings: () => void;
  onRemove: () => void;
}) {
  const def = definition(instance.type);
  const options = useMemo(() => ({ ...def.defaults, ...instance.options }), [def, instance.options]);
  const title = instance.title || def.title;
  const Body = def.component;
  const headingId = `widget-${instance.id}`;

  return (
    <section className="widget" aria-labelledby={headingId}>
      <header className={editing ? "widget-head widget-drag" : "widget-head"}>
        {editing && (
          <span className="widget-grip" aria-hidden="true">
            <GripIcon />
          </span>
        )}
        <h2 id={headingId} className="widget-title">
          <span aria-hidden="true">{asPath(title)}</span>
          <span className="visually-hidden">{title}</span>
        </h2>
        {editing && (
          <span className="widget-tools">
            <button type="button" className="icon-button" aria-label={`Settings for ${title}`} onClick={onSettings}>
              <GearIcon />
            </button>
            <button type="button" className="icon-button" aria-label={`Remove ${title}`} onClick={onRemove}>
              <CloseIcon />
            </button>
          </span>
        )}
      </header>
      <div className="widget-body">
        <WidgetErrorBoundary>
          <Body options={options} />
        </WidgetErrorBoundary>
      </div>
    </section>
  );
}

/** A title as the path on a panel's border, like justin-chae.org's sections: "Today's plan" -> "~/todays-plan". */
function asPath(title: string): string {
  const slug = title
    .toLowerCase()
    .replace(/['’]/g, "")
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .replace(/^-|-$/g, "");
  return `~/${slug || title}`;
}

/** One broken widget shouldn't take the rest of the board down with it. */
class WidgetErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Widget crashed", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <p className="problem">
        This widget hit an error: {this.state.error.message}.{" "}
        <button type="button" className="text-button" onClick={() => this.setState({ error: null })}>
          Try again
        </button>
      </p>
    );
  }
}
