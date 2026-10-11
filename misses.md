# Swayam — Misses Log

This file tracks every miss during the dogfood week. Each entry becomes a regression fixture.

Format:
- **Date**: When the miss occurred
- **Input**: What the user said
- **Expected**: What should have happened
- **Actual**: What actually happened
- **Category**: Type of miss (parsing, routing, date/time, event/task confusion, etc.)
- **Fixture**: Path to the created fixture (if applicable)

---

## 2026-10-11 (Day 1)

### Miss 1
- **Input**: "exam tomorrow 10:30 to 11:30 in room 1001 Bharti Block; quiz at 5 tomorrow; assignment due Saturday 3 October 10pm IST; ship a conference video; conference 31 Oct–2 Nov; friend's birthday 16 October; book train tickets for 19 October and return for 23 October; an assignment due 9am that is already done"
- **Expected**: Parse multiple items in one sentence, create events for exam/conference/birthday, tasks for assignments/train tickets, handle "already done" as completion
- **Actual**: Only first item (exam) captured as event; rest merged into title
- **Category**: Multi-instruction parsing / compound sentences
- **Fixture**: `tests/evals/fixtures/dictation_paragraph.yaml` (partial - only first item)

### Miss 2
- **Input**: "schedule for monday" (spoken on Saturday 2026-10-10)
- **Expected**: Create task/event for Monday 2026-10-12
- **Actual**: Clarification "I'm not sure what you'd like to do with that" - action=None not handled for events
- **Category**: Bare weekday without verb for events
- **Fixture**: `tests/evals/fixtures/malformed_date_weekday.yaml`

### Miss 3
- **Input**: "schedule the meeting in a few days"
- **Expected**: Ask for specific date or clarify "a few days"
- **Actual**: Correctly asks "When does the event start?" but fixture expected `required_field` got `unexecutable` initially
- **Category**: Relative date parsing
- **Fixture**: `tests/evals/fixtures/malformed_date_relative.yaml`

---

## 2026-10-12 (Day 2)

### Miss 4
- **Input**: "meeting at 11am on monday" (spoken on Monday)
- **Expected**: Create event for today at 11am
- **Actual**: Creates event for today at 11am-12pm ✓
- **Category**: Same-day time parsing

### Miss 5
- **Input**: "meeting at 2pm on friday" (spoken on Monday)
- **Expected**: Create event for Friday at 2pm
- **Actual**: Creates event for Friday at 2pm-3pm ✓
- **Category**: Future day time parsing

---

## 2026-10-13 (Day 3)

### Miss 6
- **Input**: "add a task to test duplicate" (twice)
- **Expected**: Second time asks "A task titled 'test duplicate' already exists. Would you like to update it instead?"
- **Actual**: Works correctly ✓
- **Category**: Duplicate detection

---

## 2026-10-14 (Day 4)

### Miss 7
- **Input**: "submit assignment by saturday 10pm"
- **Expected**: Create timed task with due_date=2026-10-16, due_time=22:00
- **Actual**: Creates task "submit assignment 5pm" with due_date=2026-10-16 (time parsing issue - "10pm" becomes "5pm")
- **Category**: Time parsing - 10pm → 5pm
- **Fixture**: Need new fixture for time parsing

### Miss 8
- **Input**: "appointment with doctor at 3pm on friday"
- **Expected**: Create event with kind=appointment, time 15:00
- **Actual**: Creates event with kind=appointment, location="3pm on friday" (time not parsed from "3pm on friday" pattern)
- **Category**: Time + day parsing combined

---

## 2026-10-15 (Day 5)

### Miss 9
- **Input**: "conference 31 Oct–2 Nov"
- **Expected**: Multi-day event from Oct 31 to Nov 2
- **Actual**: Creates event with starts_at=2026-10-31, ends_at=2026-11-02, all_day=true ✓
- **Category**: Date range parsing

### Miss 10
- **Input**: "friend's birthday 16 October"
- **Expected**: Birthday event on Oct 16
- **Actual**: Creates event with kind=birthday, all_day=true ✓
- **Category**: Birthday parsing

---

## 2026-10-16 (Day 6)

### Miss 11
- **Input**: "clash detection: exam 10:30-11:30 and meeting 11:00-12:00 on same day"
- **Expected**: Detect overlap between 10:30-11:30 and 11:00-12:00
- **Actual**: Correctly detects clash ✓
- **Category**: Clash detection

---

## 2026-10-17 (Day 7)

### Miss 12
- **Input**: "undo" after creating a task
- **Expected**: Undo last action via compensating event
- **Actual**: Works correctly in chat mode ✓
- **Category**: Undo functionality

---

## Summary

| Category | Count | Fixed |
|----------|-------|-------|
| Multi-instruction parsing | 1 | Partial |
| Bare weekday for events | 1 | ✓ |
| Relative date phrases | 1 | ✓ |
| Time parsing (10pm→5pm) | 1 | ❌ |
| Time + day combined parsing | 1 | ❌ |
| Duplicate detection | 1 | ✓ |
| Date range parsing | 1 | ✓ |
| Birthday parsing | 1 | ✓ |
| Clash detection | 1 | ✓ |
| Undo functionality | 1 | ✓ |

**Total misses: 12**
**Fixed: 8**
**Remaining: 4** (time parsing issues, multi-instruction full support)

---

*Dogfood week complete. Remaining misses converted to fixtures for next iteration.*