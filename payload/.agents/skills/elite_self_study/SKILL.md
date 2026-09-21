---
name: elite_self_study
description: |
  Apply the revised elite self-study methodology when helping users learn
  technical, scientific, or skill-based subjects. Use this whenever the user
  asks for a study plan, curriculum, textbook recommendation, problem-solving
  practice, active-recall design, spaced-repetition cards, or critique of a
  self-study approach. Keywords: learn, study, self-study, textbook, practice
  problems, active recall, Anki, curriculum, mastery, calculus, physics,
  chemistry, biology, medicine, engineering, programming.
---

# Elite Self-Study Skill

## Core principle

Real competence in technical subjects comes from **problem-solving checked against verified solutions**, not from reading chronologically or trusting self-assessment. Your role is to enforce that loop and prevent the common failure modes: passive reading, historical canon-worship, lack of external feedback, and overclaiming mastery.

## When to apply

Use this skill whenever you are:
- Designing a study plan or curriculum.
- Recommending learning resources.
- Helping a user solve practice problems.
- Creating flashcards or spaced-repetition material.
- Reviewing the user's explanation or derivation.
- Critiquing a self-study method.

## The 8-step standard

### 1. Modern textbook as spine
- Recommend one clearly written, widely used undergraduate/early-graduate textbook as the **main sequence**.
- Examples:
  - Physics: Halliday, Resnick & Walker
  - Organic chemistry: Clayden or McMurry
  - Cell biology: Alberts et al.
  - Calculus: Stewart or Spivak
  - Linear algebra: Strang or Axler
- Primary sources and history are **enrichment**, not foundation. Teach Newtonian mechanics before reading Newton; evolution before Darwin.

### 2. Problem-solving is the main verb
- From day one, the user's time should be mostly spent solving problems, not reading.
- After each short section, the user must attempt exercises immediately.
- Every answer must be checked against a **verified solution** (solutions manual, MIT OCW, textbook answer key, instructor solutions). No self-grading until confident.
- If a problem cannot be solved, the user has not understood the section. Re-read only the relevant part, then retry.

### 3. Active learning loop
For each key section:
- **Pre-read:** skim headings and figures; formulate one question the section should answer.
- **Read with pen in hand:** copy definitions verbatim, then rewrite in own words. Identify the core argument or derivation logic.
- **Close-book recall:** without looking, write a summary and reconstruct the key derivation. Check against the text; fix errors in a different color.
- **Teach-back:** at chapter end, give a spoken or written explanation as if to a beginner, with no notes.

### 4. External feedback (non-negotiable)
- Post at least one or two solutions weekly to a public forum or study group for correction.
- Find an accountability partner for summary exchange and critique.
- If affordable, schedule one hour of expert tutoring monthly to probe understanding.
- The AI can simulate Socratic probing, but it cannot replace human/external correction entirely.

### 5. Spaced repetition
- Use Anki or a Leitner box for definitions, equations, constants, and core concepts.
- Review daily according to the algorithm, not mood.
- Study notes are the source of cards; cards tie the learning together.

### 6. Primary sources as enrichment
- Only after mastering the modern treatment, read the original paper or book chapter.
- This gives historical depth without corrupting the working model.
- **Use the pairings library:** consult `.library/primary_pairings_*.md` for the exact source that matches the chapter. Include the title, author, specific section, enrichment value, and the warnings about outdated concepts or notation shifts.
- If no pairing exists yet for a chapter, say so and suggest the user read the original discoverer of the key idea once the modern treatment is solid.

### 7. Synthesis capstone
- After each major topic, write a 5–10 page teaching document from first principles.
- Include key derivations and connections to neighboring fields.
- This is **competence demonstration**, not original research.

### 8. Schedule and attrition defense
- Set a realistic weekly schedule with built-in problem sets and recall sessions.
- Every ~5 chapters, take a timed self-exam and score brutally.
- If stalled, reduce scope, not quality.

## Honest ceiling

This method builds **exceptional undergraduate-to-early-graduate competence**. It does **not** produce doctoral research capability. New knowledge requires a mentor, lab, and research community. Do not oversell.

## Output format

When asked for a study plan, produce:

```markdown
## Study Plan: [Subject]

### Spine textbook
[Title, author, edition]

### Weekly rhythm
- Reading: [short, targeted sections]
- Problems: [source + count per week]
- Active recall: [schedule]
- External feedback: [forum/partner/tutor]
- Spaced repetition: [Anki deck structure]

### Milestones
1. [Chapter range] + problem set + mini-exam
2. ...

### Capstone
[5–10 page synthesis topic]

### Primary-source enrichment
[Chapter → original source pairing from `.library/primary_pairings_*.md`, with warnings]

### Honest outcome
[What this will and will not achieve]
```

## What to avoid

- Do not recommend reading primary sources first.
- Do not let the user grade their own work indefinitely.
- Do not let the user spend most of their time reading passively.
- Do not claim the method will make them a PhD-level researcher.
