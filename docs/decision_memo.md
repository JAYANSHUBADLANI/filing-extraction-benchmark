# Decision memo: rules or a language model for filing extraction

For a lead who has to choose. Two pages.

## The recommendation in one paragraph

Route abstentions from rules to a language model; do not run rules alone or the
model alone. Rules get 77.7 percent of fields right on companies they have never
seen, at zero marginal cost and 0.43 seconds per filing, and critically fail by
declining to answer rather than by inventing a number. The model alone reaches
93.2 percent on the same test split, at roughly a cent per filing. The hybrid
that routes only what rules abstain on reaches 93.5 percent, the best of the
three, because it inherits the model's coverage without giving up rules' zero
cost on the fields rules already get right.

## What was measured

144 annual reports, 24 companies, six report years, nine numeric fields, 1,225
labelled cells. Labels come from the filers' own XBRL tagging, joined to
documents by accession number and period end, so no hand annotation was needed.
All inline XBRL markup is stripped before extraction, which is what stops the
benchmark from being a test of reading a machine readable field.

| Method | Accuracy on unseen companies | Cost per filing | Latency per filing |
|---|---|---|---|
| Rules | 0.777 (0.744 to 0.806) | 0 | 0.43 s |
| Language model | 0.932 | ~$0.01 | not separately timed |
| Hybrid | 0.935 | a fraction of the model's, scaling with the abstention rate | not separately timed |

Language model and hybrid figures are Gemini 2.5 Flash-Lite through Vertex AI,
one model on one day at temperature zero; see the model caveat below before
generalising past that. Cost is measured from the actual run, not estimated:
42.2 million input tokens across three passes over all 144 filings, $4.25 total.

## The two numbers that should drive the decision

**Generalisation, not accuracy.** The extractor scores 0.967 on the ten companies
it was developed against and 0.777 on the fourteen it was not. Nineteen points of
the development score is fitting to house style. Any vendor or internal demo that
reports a single accuracy number without saying which layouts it was tuned on is
reporting the 0.967, and you should assume the number you would get in production
is closer to the 0.777.

**Precision when answering, not accuracy.** Of the cells the extractor answers on
unseen companies, 92.7 percent are right. It abstains on 16.3 percent and is
silently wrong on 6.1 percent. That asymmetry is worth more than a few points of
headline accuracy: an abstention is a work item, a silent wrong number is a
corrupted record that nobody investigates. Whatever you buy or build, ask for
this split. A method with higher accuracy and worse precision when answering is
the worse method for a database.

## Which fields, and why the pattern is predictable

| Shape of field | Example | Test accuracy | Read as |
|---|---|---|---|
| Balance sheet subtotal, universal label, one obvious row | total liabilities, total assets, stockholders equity, cash | 0.845 to 0.893 | Rules are close to solved here |
| Income statement flow, label varies by filer | revenue, net income, operating cash flow | 0.643 to 0.738 | Rules are adequate and improvable |
| Value whose label is not on its own row | diluted earnings per share | 0.571 | Rules are weakest, a model most likely to help |

This ordering follows from field shape and was predictable before the run. Where
the label is regular, a pattern list wins on both accuracy and cost, and there is
nothing for a model to add. Where the printed label varies across filers, or the
distinguishing word sits in a section heading rather than on the row, rules
degrade and a reader that understands the page should do better.

So the field level recommendation is: **do not spend model tokens on balance
sheet subtotals at all.** They are the cheapest fields to get right with rules
and the least likely to reward a model. Spend them on earnings per share and on
the revenue line.

## What to do

1. **Keep rules as the first pass.** Zero marginal cost, 0.43 seconds per filing,
   deterministic, and auditable when a number turns out to be wrong.
2. **Route abstentions, not everything.** Only cells the rules returned nothing
   for. Routing on suspected wrongness would require knowing the answer at run
   time. This caps the hybrid: it inherits the 6.1 percent of silently wrong
   cells, and its ceiling is roughly 94 percent unless the rules half improves
   too.
3. **Confirm the model result on a second model before committing to one vendor.**
   This measurement is one model on one day. The provider layer takes a new
   class, not a rewrite, so a second model is the cheapest way to find out
   whether the result belongs to language models generally or to this one.
4. **Budget the rules improvement as well.** I know of at least one config line
   that would raise the test score and did not apply it, because applying it after
   seeing the test split would have made the reported number a tuned one. A
   quarter of engineering time would move 77.7 percent up materially. Compare a
   model against a properly resourced baseline, not against a baseline built in a
   day.

## What would change this recommendation

- **A model measured above roughly 90 percent on unseen layouts at acceptable
  cost.** This condition is now met (0.932, about a cent a filing), which is
  the case for moving the varying label fields to the model wholesale rather
  than routing only rules' abstentions. Weighed against that: the hybrid's
  measured 0.935 is already marginally higher, and routing only abstentions
  keeps rules' zero cost and determinism on the fields it already gets right.
  A wholesale move trades a small, currently negative accuracy delta for
  simplicity; it is a real option, not a clear win either way on this data.
- **A model that fails by inventing plausible numbers.** If its errors are silent
  wrong answers rather than refusals, the hybrid gets worse, not better, and the
  precision when answering figure is where you would see it.
- **Documents outside this sample.** Everything here is large capitalisation US
  10-K filings. Small filers, scanned documents, tables spanning pages and other
  jurisdictions are untested, and rules degrade fastest exactly there. If your
  real corpus looks like that, this measurement does not transfer and you should
  rerun it on your own documents before deciding.
- **Ground truth that is not filer tagged.** Accuracy here is agreement with the
  filer's own XBRL, not with truth. At least one sampled failure is a case where
  the extractor read the page correctly and the filer had tagged a different
  line. A hand labelled subsample of a hundred cells would tell you how large
  that effect is, and it is cheap to produce.

## The honest summary

I set out to test whether a language model beats rules on this task and
expected the answer to be "only where layout varies". It was not: the model
won outright on the full test split, 0.932 against rules' 0.777, and won by
the widest margin on exactly the varying-label fields the prediction named in
advance. The rules half is still stronger than its reputation on regular
fields, generalises considerably worse than a single accuracy number suggests,
and fails in the safe direction, which is why the hybrid edges out the model
alone rather than losing to it. That is enough to choose an architecture. It
is not enough to choose a vendor: this is one model on one day.
