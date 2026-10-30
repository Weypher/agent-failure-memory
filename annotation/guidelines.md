# A5 annotation: are the lessons' diagnoses correct?

Two annotators label the same 60 items **independently** (do not discuss items until both sheets are done).
Fill your own sheet only: annotator A → `sheet-A.csv`, annotator B → `sheet-B.csv`. You never see whether any
retry succeeded; please do not look it up. About 3 minutes per item.

Each item shows: the issue text, what the failed first attempt changed (diff) and its final test errors, the
lesson the model wrote about that failure, and the reference fix (source files only; tests omitted).
The reference fix is one valid fix; judge the *diagnosis*, not whether the lesson's wording matches the diff.

## Q1 `q1_root_cause` — Is the lesson's **Root cause** right?
- `correct` — names the defect the reference fix corrects: the faulty behaviour **and** where it lives
  (function or code region), consistent with the fix.
- `partial` — right location but wrong or vague mechanism, or right mechanism in the wrong place, or the right
  cause buried among several wrong candidates.
- `incorrect` — points to a different cause/location, or only restates the test failure without a cause.
- `na` — cannot be judged from the material.

## Q2 `q2_what_failed` — Is **What failed** a true account of the failed attempt?
Check against the attempt diff and the test errors.
- `accurate` — what it says the attempt did and why it failed is consistent with the evidence.
- `inaccurate` — contains a false statement about what the attempt did or what the tests showed.
- `na` — says nothing checkable.

## Q3 `q3_next_steps` — Would following the **Next steps** lead toward the reference fix?
- `yes` — the first concrete step points at the right place and the right kind of change.
- `no` — leads elsewhere or is too generic to act on ("investigate the failure").

`note`: optional free text (e.g., "reference fix is one of several plausible fixes").

Labels must be exactly the lowercase words above. When done, the analyst runs
`python3 m1/annotation_a5.py score` (Cohen's kappa per question); disagreements are then resolved by discussion
and the adjudicated label is recorded in a `final` column, with the pre-discussion kappa reported.
