# Prompt revision log

Every change to the extraction prompt is recorded here, with what it changed and
why. The rule this log exists to enforce: a prompt may only be revised against
the development split. A language model accuracy figure obtained by iterating
against the scored set is not a measurement of anything, and the count of
revisions is the reader's only way to judge how much of a reported score is
selection.

## Current state

**Revisions made: zero.** The prompt is at version 1, as written, and the test
split result reported in the README was produced by it.

That is now a statement about discipline rather than about access. The extractor
has been run: three passes over all 144 filings on Gemini 2.5 Flash-Lite through
Vertex AI, scoring 0.9292 on the development split and 0.9321 on the test split.
The prompt was not touched between writing it and reporting that number, so no
part of the test figure is selection.

The prompt was written from the field specification and from the traps already
known to be in the data: comparative periods printed beside the current one,
print scale stated in a caption rather than on the row, parentheses for
negatives, and note tables that repeat statement vocabulary.

Where it demonstrably fails is `stockholders_equity`, 0.813 against the rules
extractor's 0.893, because it reads the total equity line where XBRL means the
parent only line. Rule 5 tells it not to derive or substitute, but nothing in the
prompt tells it which of two adjacent printed subtotals the filer tagged. That is
the obvious revision to make and I have deliberately not made it, because making
it now would mean the reported test number came from a prompt tuned against the
test split. If it is ever made, it goes through the procedure below.

## Version 1

Written together with `src/filingbench/llm.py`. The system prompt states five
rules, each aimed at a failure the rules extractor was observed to make on the
development split:

1. Report the requested period, not a comparative. Aimed at the trap that costs
   roughly half of all accuracy when handled wrongly.
2. Return whole units of currency, applying the caption scale, and never scale a
   per share amount. Aimed at the largest single error class the rules extractor
   showed before its caption handling was fixed.
3. Parentheses mean negative.
4. Use the consolidated statements, never a segment note or a five year summary.
   Aimed at the note table false positive.
5. Return null rather than deriving, estimating or substituting a related line.
   Aimed at the fields that are genuinely absent from some filers' statements,
   where the correct answer is nothing.

## Procedure for any future revision

1. Confirm `results/split.csv` is unchanged and that the test companies have not
   been inspected.
2. Run the extractor on the development split only.
3. Read the failures, change the prompt, bump `PROMPT_VERSION`, and add a section
   here saying what changed and which development failures motivated it.
4. Run the test split once, at the end, and report that number as the result.
