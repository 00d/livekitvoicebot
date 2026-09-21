# Bug Report — Pivot Point Ortho Scheduling Assistant

### 1. Patient record lookup/creation appears completely non-functional — 0 of 12 task-oriented calls succeeded

**Severity:** High
**Calls:** every call in `calls/` except `hours_location_insurance-*` (pure
info Q&A, no patient lookup needed) — e.g.
`calls/reschedule_existing-1789955466/transcript.txt`,
`calls/cancel_appointment-1789955471/transcript.txt`,
`calls/medication_refill-1789955477/transcript.txt`,
`calls/insurance_not_accepted-1789967552/transcript.txt`
**Details:** Across every scheduling, rescheduling, canceling, and refill
scenario tested, the assistant collects name, date of birth, and phone
number, then reports "I'm unable to find your record in our system" or
"I can't proceed further right now" and transfers to patient support —
which is a dead-end stub ("Hello. You've reached the Pretty Good AI test
line. Goodbye."), not a real transfer. This happens even for callers who
give clean, correctly-spelled information on the first attempt (e.g.
`insurance_not_accepted`'s Kevin O'Brien). Not one appointment was booked,
moved, canceled, or refilled in 12 attempts across every tested scenario
type. This suggests the backend patient-record integration itself is
broken or unreachable in this environment, independent of what the caller
says or how clearly they say it. Entry #2 (name-spelling loop) and the
never-answered questions in entry #4 are likely downstream symptoms of
this same root failure rather than independent bugs.

---

### 2. Name/spelling confirmation loop never converges, blocks scheduling entirely

**Severity:** High
**Call:** `calls/simple_scheduling-1789943347/transcript.txt`, 22:30:02–22:31:35
**Details:** Caller's name ("Maria Alvarez") was mistranscribed as "Maria
Aldarez." When asked to spell it, the caller spelled both names letter by
letter (M-A-R-I-A / A-L-V-A-R-E-Z). The assistant's next confirmation
attempt was worse than correct ("n a r I" / "a l v a r d a") despite
having just received the correct spelling. A second correction gave
a third wrong result ("Maria Aldery"). The call never resolved
the name, the system reported it couldn't find/create a patient
record, and the call ended in a transfer to a dead-end stub rather than a
scheduled appointment. The core task (schedule a new-patient appointment)
failed as a result. (See entry #1 — this may be a symptom of the same
record-lookup failure rather than a separate spelling-specific bug, though
the confirmation attempts genuinely getting _worse_ after being corrected
is notable on its own.)

---

### 3. Caller misidentified as "Maria" regardless of actual caller

**Severity:** Medium
**Calls:**
`calls/sunday_hours_edge_case-1789966799/transcript.txt` (05:00:31),
`calls/interruption_barge_in-1789967050/transcript.txt` (05:04:44),
`calls/frustrated_repeat_caller-1789967803/transcript.txt` (05:17:23),
`calls/silence_and_mumbling-1789968054/transcript.txt` (05:21:29)
**Details:** Before the caller states their name, the assistant opens with
"I see you're calling from the number we have on file. Am I speaking with
Maria?" This happened to four different callers — Linda Osei, Marcus Webb,
Sandra Wells, and Omar Farouk — none of whom are named Maria. "Maria
Alvarez" was the caller in the very first test call made in this whole
test suite, so this looks like caller-ID-based recognition stuck
referencing that one earlier call rather than treating each call as a
distinct patient. Each caller had to correct it and the call proceeded
normally afterward, but in a real clinical setting, confirming the wrong
patient's identity even briefly is a real safety concern. Root cause
(caller-ID caching, stale session state, or something else) isn't
confirmed — this didn't occur in an earlier batch of calls placed
back-to-back, only in a later batch placed one at a time with gaps between
them, and it also didn't occur in two calls where the caller stated their
name before being asked.

---

### 4. Direct questions get ignored in favor of the scripted intake order

**Severity:** Medium
**Calls:**
`calls/insurance_not_accepted-1789967552/transcript.txt` (05:13:45, 05:14:14),
`calls/unclear_ambiguous_request-1789967301/transcript.txt` (05:09:46, 05:10:15)
**Details:** In `insurance_not_accepted`, the caller asks "Do you accept
state Medicaid insurance?" twice, back to back with providing other
requested info — the assistant never answers or acknowledges the question
at all, and the call ends (via entry #1's failure) without it ever being
addressed. In `unclear_ambiguous_request`, the caller asks twice "how do
we figure out if I need to see someone for my hip or my back?" with the
same result — no response, just a repeat of the assistant's own
confirmation question. The assistant appears to run a rigid, one-directional
intake script that cannot process a caller's question asked alongside the
information it's requesting, even when repeated.

---

## Observations

- `frustrated_repeat_caller`'s stated frustration ("I already called
  earlier today and got cut off, and nobody called me back") is never
  acknowledged or apologized for — the assistant proceeds directly into
  its standard intake script with no change in tone.
- `silence_and_mumbling`'s scripted mid-sentence pause ("it's... uh, sorry,
  one sec... March 14th, 1993") was handled correctly, and the assistant
  waited rather than treating the pause as the end of the caller's turn.
- `hours_location_insurance` (the one call not requiring patient lookup)
  completed cleanly with no issues — hours, location/parking, and
  insurance-acceptance questions were all answered correctly and the call
  ended naturally.
- Several calls showed noticeable response latency, most visible in
  `insurance_not_accepted` and `unclear_ambiguous_request`.
