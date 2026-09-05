# Progress log

A running note to myself on what is done, what is left, and what I decided along
the way.

## Result so far

The rules extractor reaches 0.9672 on the ten development companies and 0.7765
on the fourteen test companies, over 1,225 labelled cells. The nineteen point
gap is the thing I would lead with. It says most of what looked like extraction
skill while I was building was fitting to ten companies' house styles, and I only
know that because I split by company before writing any of it.

The second number I care about: on test, the extractor abstains on 16.3 percent
of cells and is silently wrong on 6.1 percent, so of the cells it answers, 92.7
percent are right. Failing by refusing to answer is the failure mode I want. It
leaves a gap someone can fill instead of a bad number nobody finds.

## Where this stands

All four phases are complete and measured. Phase 3, the language model half,
selects its provider from the model id through a small provider layer, and the
results come from Gemini 2.5 Flash-Lite through Vertex AI's OpenAI compatible
endpoint. Phase 4's hybrid and comparison now have two methods to compare
rather than one.

## The comparison, which is the thing this project is named after

| method | development | test | both |
|---|---|---|---|
| rules | 0.9672 | 0.7765 | 0.8571 |
| model | 0.9292 | 0.9321 | 0.9309 |
| hybrid | 0.9540 | 0.9347 | 0.9417 |

**The rules have a nineteen point generalisation gap and the model has none**,
0.9292 against 0.9321. That is the finding. The model beats the rules by 15.6
points on unseen layouts and loses to them by 3.8 on the ten companies the rules
were tuned against, which is what tuning buys and is not an advantage on anything
new.

The prediction I wrote down before running it was that a model would win where
the printed label varies and lose where the target is regular. Scored: the two
largest gains are `eps_diluted`, 0.571 to 0.976, and `revenue`, 0.643 to 0.925,
which are exactly the two fields I named in advance. The one field the rules keep
is `stockholders_equity`, 0.893 against 0.813, where the model reads 3M's total
equity line and XBRL means the parent only line. So the first half of the
prediction is strongly right and the second half is right in one place rather
than the several I expected.

The hybrid is best on test at 0.9347 while sending the model only the 16 percent
of cells the rules declined, and it recovers `stockholders_equity` to 0.949
because the rules answer that field confidently and the model is never asked.

**Cost, measured rather than estimated.** 14,076,286 input tokens and $1.42 per
run, $4.25 for the three runs behind the numbers above, 5.6 seconds per filing
against the rules extractor's 0.43. About one cent per filing. Run to run spread
at temperature zero was 0.0019 on development and 0.0057 on test, and the input
token count was identical to the digit across all three runs.

**The prompt is still at version 1 with zero revisions**, and the test number was
produced by it. The obvious revision, teaching it the total equity distinction,
is deliberately not made, because making it now would mean reporting a number
from a prompt tuned against the test split.

## Numbers, as measured

- 144 filings, 24 companies, six report years, form 10-K, no gaps.
- 1,225 labelled cells. 79 filings complete on all nine fields, mean 8.51 of 9.
- 277,347 inline XBRL facts stripped across 23,798 tables, about 165 tables per
  filing.
- Rules: 0.8571 overall, 0.9672 development, 0.7765 test.
- Model: 0.9309 overall, 0.9292 development, 0.9321 test, on Gemini 2.5
  Flash-Lite through Vertex, three runs at temperature zero.
- Hybrid: 0.9417 overall, 0.9347 test, the best of the three on unseen layouts.
- 0.43 seconds per filing for rules, 5.6 for the model. Full rules run end to
  end with a warm cache, 67 seconds; three model runs, 41 minutes and $4.25.
- Ten revisions of the extractor, all against the development split. Test scored
  once, after the rules were frozen.
- Two consecutive runs produce byte identical extraction tables.

## Done

- Fetch and cache. 144 annual reports across 24 companies and six report years,
  every document and every company fact set cached to disk, rate limited under
  the ten requests a second the SEC asks for. A rerun does no network work.
- Ground truth from XBRL company facts, joined to filings on accession number
  and period end, with the deduplication rule fixed and stated.
- The dev and test split, by company, frozen before I wrote a line of the
  extractor.
- The rules extractor, with a per stage failure taxonomy. Ten revisions, each
  one forced by a specific filing: a contents page that outscored the statement
  it pointed to, a segment reconciliation that read like an income statement, a
  quarterly table with identical row labels, a fiscal year label that is not a
  calendar year, a scale caption printed inside the table without the word "in",
  a statement title broken mid word by markup, and three rows sharing one
  identical label with only the heading above to tell them apart.
- The scorer, shared by every method, with the failure kinds separated.
- Tests covering the stripping step, the number parsing, the ground truth
  period logic, the scoring classification, and the rules path end to end on
  real filing markup with no network call.

## The decision the whole project rests on

Every filing in this window is inline XBRL. The filer has already wrapped each
reportable figure in a tag naming its accounting concept, and that tag is the
same information my ground truth comes from. Leaving it in place would let a
four line regular expression score one hundred percent while measuring nothing.

So I strip it. The inline XBRL header goes, every ix element is unwrapped down
to its visible text, and both extractors read the rendered page the way a person
would. Across the 144 filings that removes 277,347 tagged facts.

I am aware this is the assumption an interviewer should attack first, because it
is what makes the task hard, and I chose to make it hard. If I had left the tags
in, the honest description of the result would be "I measured how well a regular
expression reads a machine readable field", which is not the question.

## What I decided and why

**Ground truth is agreement with the filer, not with truth.** XBRL is tagged by
the filer. Where a company tags something unusually, my extractor is marked wrong
for reading the page correctly. I decided to leave those cases in and let them
show up in the error analysis rather than removing them, because removing them
would quietly inflate every accuracy number in the repository.

**A filing is the source of truth only for its own period.** Annual reports carry
comparatives, so the same figure appears under several filings and several fiscal
years. I match on period end date, never fiscal year, and I only accept a fact
whose period end equals the filing's own report date. A later restatement of an
earlier period is therefore never used, which is right here: the question is
whether an extractor can read the number printed on that filing's page.

**Fields that are absent are not scored.** Eight of the twenty four companies
never tag total liabilities, because their balance sheet prints no total
liabilities subtotal. Three never tag operating income, because their income
statement runs from gross profit to pre-tax income with no operating line. The
label is missing precisely because the number is missing from the page, so those
cells are excluded rather than counted as failures.

**Split by company, not by filing.** Layout belongs to the company and its filing
agent. Splitting by filing would have put five years of the same house style in
development and the sixth in test, which measures memorisation of a layout rather
than generalisation to one. Apple sits in development by hand, not by the draw,
because its 2024 report is the document I read while designing the table parser.

**The hybrid may only route on abstention.** It asks the model about cells the
rules extractor returned nothing for, never about cells it answered. Routing on
correctness would require knowing the answer at run time, which no pipeline has.
The consequence is that the hybrid inherits every silent rules error, and I would
rather state that ceiling in advance than discover it in the results.

## Open decisions

- Whether the language model should receive the whole stripped document or only
  the located statement tables. I implemented both and defaulted to the whole
  document, because the cheaper mode hands the model the rules extractor's first
  stage and would turn a comparison of two methods into a comparison of one and a
  half. This is the choice I am least settled on, since the cheap mode is what a
  real pipeline would actually deploy.
- Whether nine fields is the right set. It spans instants and durations, three
  statements, and one unscaled per share figure, which is what I wanted. It is
  still nine fields chosen by me.

## Next

- A second model. Everything here is one model on one day, and the cheapest way
  to learn whether the fifteen point gap belongs to language models or to this
  one is to add a second provider class in `providers.py` and rerun.
- The `statements` context mode has never been run. It hands the model the rules
  extractor's statement locator for free, which makes it a worse comparison but a
  far cheaper pipeline, and the cost section would be more useful with both.
- Update `docs/decision_memo.md`, which still predates the comparison.
- A hand labelled subsample, a hundred cells read by a person, to measure how far
  filer tagging is from what a reader would call the answer. This is the ceiling
  on what any accuracy number here means and it is unmeasured.
