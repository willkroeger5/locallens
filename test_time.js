// node test_time.js — checks the open-now logic, including windows that cross midnight.
const assert = require("assert");
const { liveFor, startsIn } = require("./docs/time.js");
const at = (day, hhmm) => ({ day: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].indexOf(day), min: +hhmm.slice(0, 2) * 60 + +hhmm.slice(3) });
const deal = o => ({ days: ["Mon", "Tue", "Wed", "Thu", "Fri"], start: null, end: null, until_close: false, all_day: false, ...o });

const hh = deal({ start: "15:00", end: "18:00" });
assert.strictEqual(liveFor(hh, at("Tue", "16:30")), 90);
assert.strictEqual(liveFor(hh, at("Tue", "18:00")), null);   // end is exclusive
assert.strictEqual(liveFor(hh, at("Sat", "16:30")), null);   // wrong day
assert.strictEqual(startsIn(hh, at("Tue", "14:00")), 60);

const late = deal({ days: ["Fri", "Sat"], start: "22:00", end: "02:00" });
assert.strictEqual(liveFor(late, at("Fri", "23:00")), 180);
assert.strictEqual(liveFor(late, at("Sat", "01:00")), 60);   // Friday's window spilling into Saturday
assert.strictEqual(liveFor(late, at("Sun", "01:00")), 60);   // Saturday's window spilling into Sunday
assert.strictEqual(liveFor(late, at("Mon", "01:00")), null);

const close = deal({ days: ["Mon"], start: "21:00", until_close: true });
assert.strictEqual(liveFor(close, at("Mon", "23:30")), 150); // close treated as 2am
assert.strictEqual(liveFor(close, at("Tue", "01:30")), 30);

const allDay = deal({ days: ["Mon"], all_day: true });
assert.ok(liveFor(allDay, at("Mon", "09:00")) != null);
assert.strictEqual(liveFor(allDay, at("Tue", "09:00")), null);
const noTimes = deal({ days: ["Wed"] });                    // "Happy hour Wed" with no hours given
assert.strictEqual(liveFor(noTimes, at("Wed", "17:00")), null);
assert.strictEqual(startsIn(noTimes, at("Wed", "09:00")), null);
console.log("time.js ok");
