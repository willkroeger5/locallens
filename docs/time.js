// Deal schedule math, shared by index.html and test_time.js. Times are minutes since midnight;
// a window's end may exceed 1440 when it runs past midnight (e.g. 10pm-2am = [1320, 1560]).
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const LATE_CLOSE = 26 * 60;  // "until close" is treated as 2am

const toMin = t => t ? +t.slice(0, 2) * 60 + +t.slice(3) : null;

// A deal's windows as [dayIndex, startMin, endMin], endMin may exceed 1440 (past midnight).
function windows(d) {
  const s = toMin(d.start), e = toMin(d.end);
  let span;
  if (d.all_day) span = [0, 1440];
  else if (s == null && e == null) return [];  // days known, times not listed: never claim "on now"
  else if (s != null && e != null) span = [s, e > s ? e : e + 1440];
  else if (s != null) span = [s, d.until_close ? LATE_CLOSE : Math.max(s + 180, 1440)];
  else span = [0, e];
  return d.days.map(day => [DAYS.indexOf(day), ...span]);
}

// Minutes until the deal ends if it's on right now, else null.
function liveFor(d, now) {
  for (const [day, s, e] of windows(d)) {
    if (day === now.day && now.min >= s && now.min < e) return e - now.min;
    if ((day + 1) % 7 === now.day && now.min + 1440 >= s && now.min + 1440 < e) return e - now.min - 1440;
  }
  return null;
}

// Minutes until it starts later today, else null.
function startsIn(d, now) {
  const m = windows(d).filter(([day, s]) => day === now.day && s > now.min).map(([, s]) => s - now.min);
  return m.length ? Math.min(...m) : null;
}

if (typeof module !== "undefined") module.exports = { DAYS, toMin, windows, liveFor, startsIn };
