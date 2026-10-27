import { useRef, useState } from "react";

import type { Course, Plan, PlanTask } from "../api";
import { BriefDialog } from "../agent/BriefDialog";
import { useAgent, useMakePlan, useTaskDone, useTodayPlan } from "../agent/hooks";
import { duration, Resources } from "../agent/Markdown";
import { courseStyle } from "../courses";
import { useCourses, useTimezone } from "../hooks";
import { useQuarter } from "../quarter";
import { clock } from "../time";
import { CheckIcon, RefreshIcon } from "../ui/icons";
import { Popover } from "../ui/Popover";

export interface PlanOptions {
  showSteps: boolean; // each task's short steps
  showHeadsUp: boolean; // what's coming that needs attention soon
}

export const planDefaults: PlanOptions = { showSteps: true, showHeadsUp: true };

/** Today's plan from the agent: a short list of concrete tasks sized to the time you have. */
export function PlanWidget({ options }: { options: PlanOptions }) {
  const today = useTodayPlan();
  const agent = useAgent();
  const tz = useTimezone();
  const { isCurrent } = useQuarter();

  if (!isCurrent) return <p className="quiet">Plans are made for the current quarter.</p>;

  if (today.isPending) return <p className="quiet">Loading…</p>;
  if (today.isError) return <p className="problem">Can't reach the dashboard server. It retries every minute.</p>;

  const { plan, pending } = today.data;
  const ready = agent.data?.ready ?? false;
  const running = pending?.status === "running";
  const failed = pending?.status === "error" ? pending : null;
  const nextAt = agent.data?.next_plan_at ? clock(new Date(agent.data.next_plan_at), tz) : null;

  return (
    <div className="plan">
      {running && (
        <p className="working">
          {plan ? "Making a new plan" : "Planning your day"}: checking what's due and reading the course materials. This
          takes a minute or two
        </p>
      )}
      {failed && <p className="problem">The plan couldn't be made: {failed.error}</p>}
      {plan ? (
        <PlanBody plan={plan} options={options} />
      ) : (
        !running && (
          <p className="quiet">
            {ready
              ? `No plan yet today.${nextAt ? ` One is made every morning at ${nextAt}.` : ""}`
              : (agent.data?.problem ?? "The planner isn't set up.")}
          </p>
        )
      )}
      {ready && !running && <Replan hasPlan={!!plan} />}
    </div>
  );
}

function PlanBody({ plan, options }: { plan: Plan; options: PlanOptions }) {
  const tz = useTimezone();
  const courses = useCourses();
  const byCode = new Map((courses.data ?? []).map((c) => [c.code, c]));
  const total = plan.tasks.reduce((sum, t) => sum + (t.minutes ?? 0), 0);
  const done = plan.tasks.reduce((sum, t) => sum + (t.done ? (t.minutes ?? 0) : 0), 0);
  const made = plan.finished_at ? new Date(plan.finished_at) : null;

  return (
    <>
      {plan.summary && <p className="plan-summary">{plan.summary}</p>}
      <p className="plan-meta">
        {total > 0 && <span>{done > 0 ? `${duration(done)} of ${duration(total)} done` : `${duration(total)} of work`}</span>}
        {made && <span>Made at {clock(made, tz)}</span>}
        {plan.note && <span>You asked: “{plan.note}”</span>}
      </p>
      <ol className="plan-tasks">
        {plan.tasks.map((task, i) => (
          <TaskRow key={i} plan={plan} index={i} task={task} course={task.course ? byCode.get(task.course) : undefined} showSteps={options.showSteps} />
        ))}
      </ol>
      {options.showHeadsUp && plan.heads_up.length > 0 && (
        <section className="group">
          <h3 className="group-label">Coming up</h3>
          <ul className="heads-up">
            {plan.heads_up.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

function TaskRow({ plan, index, task, course, showSteps }: {
  plan: Plan;
  index: number;
  task: PlanTask;
  course: Course | undefined;
  showSteps: boolean;
}) {
  const tick = useTaskDone();
  const [brief, setBrief] = useState(false);
  return (
    <li className={task.done ? "task done" : "task"} style={courseStyle(course ?? null)}>
      <button
        type="button"
        role="checkbox"
        aria-checked={task.done}
        aria-label={`Done: ${task.title}`}
        className="tick"
        onClick={() => tick.mutate({ plan, index, done: !task.done })}
      >
        <CheckIcon />
      </button>
      <div className="task-main">
        <p className="task-title">{task.title}</p>
        <p className="row-sub">
          {task.course && <span className="course-code">{task.course}</span>}
          {task.when && <span>{task.when}</span>}
          {task.item_id !== null && (
            <button type="button" className="text-button task-brief" onClick={() => setBrief(true)}>
              Brief
            </button>
          )}
        </p>
        {task.why && <p className="task-why">{task.why}</p>}
        {showSteps && task.steps.length > 0 && (
          <ol className="task-steps">
            {task.steps.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ol>
        )}
        <Resources resources={task.resources} />
      </div>
      {task.minutes !== null && <span className="task-minutes">{duration(task.minutes)}</span>}
      {brief && task.item_id !== null && (
        <BriefDialog itemId={task.item_id} title={task.course ? `${task.course}: ${task.title}` : task.title} onClose={() => setBrief(false)} />
      )}
    </li>
  );
}

/** Ask for a new plan, optionally saying what's different ("only two hours today"). */
function Replan({ hasPlan }: { hasPlan: boolean }) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const anchor = useRef<HTMLButtonElement>(null);
  const make = useMakePlan();
  const submit = () =>
    make.mutate(note.trim(), {
      onSuccess: () => {
        setOpen(false);
        setNote("");
      },
    });

  return (
    <div className="plan-actions">
      <button
        ref={anchor}
        type="button"
        className={hasPlan ? "outline-button" : "pill-button"}
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <RefreshIcon /> {hasPlan ? "Plan again" : "Make today's plan"}
      </button>
      {make.isError && <p className="problem">{make.error.message}</p>}
      {open && (
        <Popover anchor={anchor} label="Plan again" onClose={() => setOpen(false)}>
          <form
            className="note-editor"
            onSubmit={(e) => {
              e.preventDefault();
              submit();
            }}
          >
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
              }}
              rows={3}
              maxLength={1000}
              aria-label="Anything the plan should know"
              placeholder="Optional: only 2 hours today, focus on the MATH 300 midterm, I finished the Lab 3 design doc…"
            />
            <div className="note-editor-actions">
              <button type="button" className="text-button" onClick={() => setOpen(false)}>
                Cancel
              </button>
              <button type="submit" className="pill-button" disabled={make.isPending}>
                {hasPlan ? "Plan again" : "Make the plan"}
              </button>
            </div>
          </form>
        </Popover>
      )}
    </div>
  );
}
