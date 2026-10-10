const form = document.querySelector("#workout-form");
const exerciseList = document.querySelector("#exercise-list");
const workoutList = document.querySelector("#workout-list");
const formMessage = document.querySelector("#form-message");

function localToday() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

function readableDate(value, options = { month: "short", day: "numeric", year: "numeric" }) {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(year, month - 1, day).toLocaleDateString(undefined, options);
}

function addExercise(values = {}) {
  const row = document.createElement("div");
  row.className = "exercise-row";
  row.innerHTML = `
    <label class="exercise-name">Exercise<input type="text" maxlength="80" placeholder="e.g. Squats" required></label>
    <label class="exercise-number">Sets<input type="number" min="1" max="1000" placeholder="3" required></label>
    <label class="exercise-number">Reps<input type="number" min="1" max="1000" placeholder="10" required></label>
    <button type="button" class="remove-button" aria-label="Remove exercise" title="Remove exercise">×</button>
  `;
  const inputs = row.querySelectorAll("input");
  inputs[0].value = values.name || "";
  inputs[1].value = values.sets || "";
  inputs[2].value = values.reps || "";
  row.querySelector("button").addEventListener("click", () => {
    if (exerciseList.children.length > 1) row.remove();
  });
  exerciseList.append(row);
}

function showMessage(message, isError = false) {
  formMessage.textContent = message;
  formMessage.classList.toggle("error", isError);
}

async function request(url, options = {}) {
  const response = await fetch(url, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "Something went wrong. Please try again.");
  return body;
}

function renderSummary(summary) {
  document.querySelector("#stat-workouts").textContent = summary.workouts;
  document.querySelector("#stat-minutes").textContent = summary.minutes;
  document.querySelector("#stat-sets").textContent = summary.sets;
  document.querySelector("#stat-reps").textContent = summary.reps;
  document.querySelector("#week-range").textContent = `${readableDate(summary.week_start, { month: "short", day: "numeric" })} – ${readableDate(summary.week_end, { month: "short", day: "numeric" })}`;
  document.querySelector("#week-label").textContent = `${readableDate(summary.week_start)} – ${readableDate(summary.week_end)}`;

  const chart = document.querySelector("#activity-chart");
  chart.replaceChildren();
  const maximum = Math.max(1, ...summary.daily.map(day => day.minutes));
  summary.daily.forEach(day => {
    const column = document.createElement("div");
    column.className = "activity-day";
    const track = document.createElement("div");
    track.className = "activity-track";
    const bar = document.createElement("div");
    bar.className = "activity-bar";
    bar.style.height = `${day.minutes ? Math.max(8, day.minutes / maximum * 100) : 0}%`;
    track.append(bar);
    const label = document.createElement("span");
    label.textContent = readableDate(day.date, { weekday: "short" }).slice(0, 1);
    column.title = `${readableDate(day.date)}: ${day.minutes} min, ${day.workouts} workout${day.workouts === 1 ? "" : "s"}`;
    column.append(track, label);
    chart.append(column);
  });
  chart.setAttribute("aria-label", summary.daily.map(day => `${readableDate(day.date)}: ${day.minutes} minutes`).join("; "));
}

function renderWorkouts(workouts) {
  workoutList.replaceChildren();
  document.querySelector("#recent-count").textContent = `${workouts.length} logged`;
  if (!workouts.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.innerHTML = '<span aria-hidden="true">✳</span><h3>Your story starts here</h3><p>Log your first workout to see your progress unfold.</p>';
    workoutList.append(empty);
    return;
  }
  workouts.forEach(workout => {
    const card = document.createElement("article");
    card.className = "workout-card";
    const top = document.createElement("div");
    top.className = "workout-top";
    const info = document.createElement("div");
    const date = document.createElement("h3");
    date.textContent = readableDate(workout.date);
    const meta = document.createElement("p");
    meta.textContent = `${workout.duration_minutes} min · ${workout.exercises.length} exercise${workout.exercises.length === 1 ? "" : "s"}`;
    info.append(date, meta);
    const remove = document.createElement("button");
    remove.className = "delete-button";
    remove.type = "button";
    remove.textContent = "Delete";
    remove.setAttribute("aria-label", `Delete workout from ${readableDate(workout.date)}`);
    remove.addEventListener("click", async () => {
      if (!window.confirm("Delete this workout?")) return;
      try {
        await request(`/api/workouts/${workout.id}`, { method: "DELETE" });
        await refresh();
      } catch (error) {
        showMessage(error.message, true);
      }
    });
    top.append(info, remove);
    const exercises = document.createElement("ul");
    workout.exercises.forEach(exercise => {
      const item = document.createElement("li");
      const name = document.createElement("span");
      name.textContent = exercise.name;
      const detail = document.createElement("span");
      detail.textContent = `${exercise.sets} × ${exercise.reps}`;
      item.append(name, detail);
      exercises.append(item);
    });
    card.append(top, exercises);
    workoutList.append(card);
  });
}

async function refresh() {
  const [workouts, summary] = await Promise.all([
    request("/api/workouts"),
    request(`/api/summary?week=${localToday()}`),
  ]);
  renderWorkouts(workouts.workouts);
  renderSummary(summary);
}

document.querySelector("#workout-date").value = localToday();
document.querySelector("#add-exercise").addEventListener("click", () => {
  if (exerciseList.children.length < 30) addExercise();
});
addExercise();

form.addEventListener("submit", async event => {
  event.preventDefault();
  showMessage("");
  const exercises = [...exerciseList.children].map(row => {
    const fields = row.querySelectorAll("input");
    return { name: fields[0].value.trim(), sets: Number(fields[1].value), reps: Number(fields[2].value) };
  });
  const payload = {
    date: document.querySelector("#workout-date").value,
    duration_minutes: Number(document.querySelector("#duration").value),
    exercises,
  };
  const button = document.querySelector("#save-button");
  button.disabled = true;
  try {
    await request("/api/workouts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    form.reset();
    document.querySelector("#workout-date").value = localToday();
    exerciseList.replaceChildren();
    addExercise();
    showMessage("Workout saved. Keep it going!");
    await refresh();
  } catch (error) {
    showMessage(error.message, true);
  } finally {
    button.disabled = false;
  }
});

refresh().catch(error => showMessage(`Could not load workouts: ${error.message}`, true));
