"use strict";

const calendar = document.querySelector("#anniversary-calendar");
const openCalendar = document.querySelector("#calendar-open");
const previousMonth = document.querySelector("#calendar-previous");
const nextMonth = document.querySelector("#calendar-next");
const calendarTitle = document.querySelector("#calendar-title");
const calendarDays = document.querySelector("#calendar-days");
const dayInput = document.querySelector("#anniversary-day");
const monthInput = document.querySelector("#anniversary-month");
const yearInput = document.querySelector("#anniversary-year");
const monthLabel = new Intl.DateTimeFormat("en-GB", { month: "long", year: "numeric", timeZone: "UTC" });
const dayLabel = new Intl.DateTimeFormat("en-GB", { dateStyle: "full", timeZone: "UTC" });
let calendarSelection;
let calendarFocus;

function calendarDate(year, month, day) {
  const result = new Date(0);
  result.setUTCFullYear(year, month, day);
  return result;
}

function changeMonth(date, amount) {
  const next = calendarDate(date.getUTCFullYear(), date.getUTCMonth() + amount, 1);
  const lastDay = calendarDate(next.getUTCFullYear(), next.getUTCMonth() + 1, 0).getUTCDate();
  next.setUTCDate(Math.min(date.getUTCDate(), lastDay));
  return next;
}

function renderCalendar(focusDay = false) {
  const year = calendarFocus.getUTCFullYear();
  const month = calendarFocus.getUTCMonth();
  const first = calendarDate(year, month, 1);
  const offset = (first.getUTCDay() + 6) % 7;
  const lastDay = calendarDate(year, month + 1, 0).getUTCDate();
  calendarTitle.textContent = monthLabel.format(first);
  previousMonth.disabled = year === 1 && month === 0;
  nextMonth.disabled = year === 9999 && month === 11;
  calendarDays.replaceChildren();
  let focusedButton;
  for (let index = 0; index < Math.ceil((offset + lastDay) / 7) * 7; index++) {
    const row = index % 7 === 0 ? calendarDays.insertRow() : calendarDays.rows[calendarDays.rows.length - 1];
    const cell = row.insertCell();
    const day = index - offset + 1;
    if (day < 1 || day > lastDay) continue;
    const date = calendarDate(year, month, day);
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = String(day);
    button.dataset.day = String(day);
    button.tabIndex = date.getTime() === calendarFocus.getTime() ? 0 : -1;
    button.setAttribute("aria-label", dayLabel.format(date));
    button.setAttribute("aria-pressed", String(date.getTime() === calendarSelection.getTime()));
    if (button.tabIndex === 0) focusedButton = button;
    cell.append(button);
  }
  if (focusDay) focusedButton?.focus();
}

function moveCalendar(date, focusDay) {
  if (date.getUTCFullYear() < 1 || date.getUTCFullYear() > 9999) return;
  calendarFocus = date;
  renderCalendar(focusDay);
}

openCalendar.addEventListener("click", () => {
  const year = yearInput.valueAsNumber;
  const month = Number(monthInput.value) - 1;
  const day = dayInput.valueAsNumber;
  const date = calendarDate(year, month, day);
  calendarSelection = yearInput.validity.valid && dayInput.validity.valid && date.getUTCMonth() === month
    ? date : new Date(`${calendar.dataset.selected}T00:00:00Z`);
  calendarFocus = calendarSelection;
  calendar.showModal();
  renderCalendar(true);
});
calendar.addEventListener("close", () => openCalendar.focus());
previousMonth.addEventListener("click", () => moveCalendar(changeMonth(calendarFocus, -1), false));
nextMonth.addEventListener("click", () => moveCalendar(changeMonth(calendarFocus, 1), false));
calendarDays.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-day]");
  if (!button) return;
  dayInput.value = button.dataset.day.padStart(2, "0");
  monthInput.value = String(calendarFocus.getUTCMonth() + 1);
  yearInput.value = String(calendarFocus.getUTCFullYear());
  calendar.close();
});
calendarDays.addEventListener("keydown", (event) => {
  const weekday = (calendarFocus.getUTCDay() + 6) % 7;
  const offsets = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7, Home: -weekday, End: 6 - weekday };
  if (event.key === "PageUp" || event.key === "PageDown") {
    event.preventDefault();
    const step = event.shiftKey ? 12 : 1;
    moveCalendar(changeMonth(calendarFocus, event.key === "PageUp" ? -step : step), true);
  } else if (Object.hasOwn(offsets, event.key)) {
    event.preventDefault();
    const date = calendarDate(
      calendarFocus.getUTCFullYear(), calendarFocus.getUTCMonth(), calendarFocus.getUTCDate() + offsets[event.key],
    );
    moveCalendar(date, true);
  }
});
openCalendar.hidden = false;
