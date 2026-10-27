import type { CategoryGrade, CourseGrades, GradedItem } from "../api";
import { courseStyle } from "../courses";
import { useGrades } from "../hooks";
import { points } from "./parts";

export interface GradesOptions {
  courses: string[]; // empty means every course with grades or weights
}

export const gradesDefaults: GradesOptions = { courses: [] };

const pct = (n: number) => `${n.toFixed(n >= 99.95 ? 0 : 1)}%`;

export function Grades({ options }: { options: GradesOptions }) {
  const grades = useGrades();
  if (grades.isPending) return <p className="quiet">Loading…</p>;
  if (grades.isError) return <p className="problem">Can't reach the dashboard server. It retries every minute.</p>;
  const shown = grades.data.filter((g) => !options.courses.length || options.courses.includes(g.course.id));
  if (shown.length === 0) {
    return <p className="quiet">No grades yet. Scores appear once Gradescope or the Canvas API source has graded work.</p>;
  }
  return (
    <ul className="grade-cards">
      {shown.map((g) => (
        <GradeCard key={g.course.id} grades={g} />
      ))}
    </ul>
  );
}

function GradeCard({ grades: g }: { grades: CourseGrades }) {
  const graded = [...g.categories.flatMap((c) => c.items.map((i) => ({ ...i, category: c.name }))), ...g.uncategorized.map((i) => ({ ...i, category: null }))];
  return (
    <li className="grade-card" style={courseStyle(g.course)}>
      <h3 className="grade-course">
        <span className="swatch" aria-hidden="true" />
        {g.course.code}
      </h3>
      {g.estimate === null ? (
        <p className="quiet">Nothing graded yet.</p>
      ) : (
        <>
          <p className="grade-estimate">{pct(g.estimate)}</p>
          <p className="grade-basis">
            {g.weighted
              ? g.counted_weight >= 99.5
                ? "Weighted across every category"
                : `Based on the ${pct(g.counted_weight)} of the grade that's been graded`
              : `${points(g.earned)} of ${points(g.possible)} points, unweighted`}
          </p>
        </>
      )}

      {g.categories.length > 0 && (
        <ul className="grade-categories">
          {g.categories.map((c) => (
            <CategoryMeter key={c.name} category={c} />
          ))}
        </ul>
      )}
      {!g.weighted && <p className="field-help">Add this course's grading weights to config.yaml for a weighted estimate.</p>}
      {g.weighted && g.uncategorized.length > 0 && (
        <p className="field-help">
          {g.uncategorized.length} graded {g.uncategorized.length === 1 ? "item matches" : "items match"} no category and {g.uncategorized.length === 1 ? "isn't" : "aren't"} counted.
        </p>
      )}

      {graded.length > 0 && (
        <details className="grade-details">
          <summary>Graded work ({graded.length})</summary>
          <table>
            <thead>
              <tr>
                <th scope="col">Item</th>
                <th scope="col" className="num">
                  Score
                </th>
              </tr>
            </thead>
            <tbody>
              {graded.map((i) => (
                <GradedRow key={i.id} item={i} category={i.category} weighted={g.weighted} />
              ))}
            </tbody>
          </table>
        </details>
      )}
    </li>
  );
}

function CategoryMeter({ category: c }: { category: CategoryGrade }) {
  const label =
    c.percent === null
      ? `${c.name}: nothing graded yet, ${c.weight}% of the grade`
      : `${c.name}: ${pct(c.percent)}, ${points(c.earned)} of ${points(c.possible)} points, ${c.weight}% of the grade`;
  return (
    <li className="grade-category" title={label}>
      <span className="grade-category-name">
        {c.name} <span className="quiet-inline">{c.weight}%</span>
      </span>
      <span className="grade-category-value">{c.percent === null ? "–" : pct(c.percent)}</span>
      <span
        className="meter"
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={c.percent ?? undefined}
      >
        {c.percent !== null && <span className="meter-fill" style={{ width: `${Math.min(100, c.percent)}%` }} />}
      </span>
    </li>
  );
}

function GradedRow({ item, category, weighted }: { item: GradedItem; category: string | null; weighted: boolean }) {
  const score = item.points ? `${points(item.score)}/${points(item.points)}` : points(item.score);
  return (
    <tr className={item.dropped ? "dropped" : undefined}>
      <td>
        {item.url ? (
          <a href={item.url} target="_blank" rel="noreferrer">
            {item.title}
          </a>
        ) : (
          item.title
        )}
        {weighted && (
          <span className="grade-item-category">
            {category ?? "No category"}
            {item.dropped && ", dropped"}
          </span>
        )}
      </td>
      <td className="num">
        {score}
        {item.percent !== null && <span className="quiet-inline"> {pct(item.percent)}</span>}
      </td>
    </tr>
  );
}
