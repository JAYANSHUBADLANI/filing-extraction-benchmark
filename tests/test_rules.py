"""Each test here is a trap taken from a real filing in the sample."""

import pytest

from filingbench.config import load_fields
from filingbench.docprep import prepare
from filingbench.rules import extract_field, find_statement_table, resolve_columns

FIELDS = load_fields()

# A note table that reuses statement vocabulary sits before the real statement,
# exactly as Oracle's segment margin reconciliation does.
DECOY_AND_STATEMENT = """
<html><body>
<p>The following table reconciles total margin for operating segments to income
before income taxes:</p>
<table>
  <tr><td>(in millions)</td><td>2024</td><td>2023</td></tr>
  <tr><td>Total margin for operating segments</td><td>$</td><td>31,345</td><td>$</td><td>29,162</td></tr>
  <tr><td>Research and development</td><td>(</td><td>8,915</td><td>)</td><td>(</td><td>8,623</td><td>)</td></tr>
  <tr><td>Net income</td><td>9,999</td><td>8,888</td></tr>
</table>
<p>CONSOLIDATED STATEMENTS OF OPERATIONS (In millions, except per share amounts)</p>
<table>
  <tr><td></td><td>September 28, 2024</td><td>September 30, 2023</td></tr>
  <tr><td>Total net sales</td><td>$</td><td>391,035</td><td>$</td><td>383,285</td></tr>
  <tr><td>Cost of sales</td><td>210,352</td><td>214,137</td></tr>
  <tr><td>Gross margin</td><td>180,683</td><td>169,148</td></tr>
  <tr><td>Operating expenses</td><td>57,467</td><td>54,847</td></tr>
  <tr><td>Operating income</td><td>123,216</td><td>114,301</td></tr>
  <tr><td>Net income</td><td>93,736</td><td>96,995</td></tr>
  <tr><td>Income tax</td><td>29,749</td><td>16,741</td></tr>
  <tr><td>Earnings per common share--diluted :</td></tr>
  <tr><td>Net income attributable to Pfizer Inc. common shareholders</td><td>$</td><td>6.08</td><td>$</td><td>6.13</td></tr>
</table>
</body></html>
"""


@pytest.fixture(scope="module")
def decoy_doc():
    return prepare(DECOY_AND_STATEMENT, doc_id="decoy")


def test_caption_beats_anchor_words(decoy_doc):
    """The note table scores well on anchors but has no statement caption."""
    table = find_statement_table(decoy_doc, "income_statement")
    assert table is not None
    assert "Total net sales" in table.flat_text
    assert "Total margin for operating segments" not in table.flat_text


def test_scale_from_caption_is_applied(decoy_doc):
    result = extract_field(decoy_doc, FIELDS["revenue"], "2024-09-28")
    assert result.status == "ok"
    assert result.value == 391_035 * 1_000_000


def test_current_period_column_is_chosen_not_the_comparative(decoy_doc):
    result = extract_field(decoy_doc, FIELDS["net_income"], "2023-09-30")
    assert result.value == 96_995 * 1_000_000, "the second column is fiscal 2023"


def test_per_share_field_is_never_scaled(decoy_doc):
    result = extract_field(decoy_doc, FIELDS["eps_diluted"], "2024-09-28")
    assert result.value == pytest.approx(6.08)


def test_section_heading_resolves_a_row_that_does_not_name_itself(decoy_doc):
    """Pfizer's diluted EPS row is labelled with the company name, not "diluted"."""
    result = extract_field(decoy_doc, FIELDS["eps_diluted"], "2024-09-28")
    assert result.matched_label.startswith("[section]")


BALANCE_SHEET = """
<html><body>
<p>CONSOLIDATED BALANCE SHEETS (In millions)</p>
<table>
  <tr><td></td><td>2024</td><td>2023</td></tr>
  <tr><td>Total current assets</td><td>10,000</td><td>9,000</td></tr>
  <tr><td>Goodwill</td><td>1,000</td><td>1,000</td></tr>
  <tr><td>Total assets</td><td>$</td><td>39,868</td><td>$</td><td>50,580</td></tr>
  <tr><td>Accounts payable</td><td>500</td><td>400</td></tr>
  <tr><td>Total current liabilities</td><td>8,000</td><td>7,000</td></tr>
  <tr><td>Total liabilities</td><td>35,974</td><td>45,712</td></tr>
  <tr><td>Common stock</td><td>9</td><td>9</td></tr>
  <tr><td>Retained earnings</td><td>3,000</td><td>4,000</td></tr>
  <tr><td>Total 3M Company shareholders' equity</td><td>3,842</td><td>4,807</td></tr>
  <tr><td>Equity attributable to noncontrolling interests</td><td>52</td><td>61</td></tr>
  <tr><td>Total equity</td><td>3,894</td><td>4,868</td></tr>
  <tr><td>Total liabilities and equity</td><td>$</td><td>39,868</td><td>$</td><td>50,580</td></tr>
</table>
</body></html>
"""


@pytest.fixture(scope="module")
def balance_doc():
    return prepare(BALANCE_SHEET, doc_id="bs")


def test_parent_equity_beats_total_equity(balance_doc):
    """XBRL StockholdersEquity excludes noncontrolling interests."""
    result = extract_field(balance_doc, FIELDS["stockholders_equity"], "2024-12-31")
    assert result.value == 3_842 * 1_000_000


def test_total_liabilities_is_not_the_grand_total(balance_doc):
    result = extract_field(balance_doc, FIELDS["total_liabilities"], "2024-12-31")
    assert result.value == 35_974 * 1_000_000


def test_total_assets_is_not_total_current_assets(balance_doc):
    result = extract_field(balance_doc, FIELDS["total_assets"], "2024-12-31")
    assert result.value == 39_868 * 1_000_000


FISCAL_LABEL = """
<html><body>
<p>CONSOLIDATED STATEMENTS OF EARNINGS</p>
<table>
  <tr><td>in millions, except per share data</td><td>Fiscal</td><td>Fiscal</td></tr>
  <tr><td></td><td>2023</td><td>2022</td></tr>
  <tr><td>Net sales</td><td>$</td><td>152,669</td><td>$</td><td>157,403</td></tr>
  <tr><td>Cost of sales</td><td>101,709</td><td>104,625</td></tr>
  <tr><td>Gross profit</td><td>50,960</td><td>52,778</td></tr>
  <tr><td>Operating income</td><td>21,689</td><td>24,039</td></tr>
  <tr><td>Net earnings</td><td>17,105</td><td>17,105</td></tr>
  <tr><td>Income tax</td><td>5,000</td><td>5,000</td></tr>
</table>
</body></html>
"""


def test_fiscal_year_label_is_not_a_calendar_year():
    """Home Depot's year ended 28 January 2024 is headed "Fiscal 2023"."""
    doc = prepare(FISCAL_LABEL, doc_id="fiscal")
    table = find_statement_table(doc, "income_statement")
    column, _, basis = resolve_columns(table, "2024-01-28")
    assert column == 0
    assert basis == "header_fiscal_year_label"
    result = extract_field(doc, FIELDS["revenue"], "2024-01-28")
    assert result.value == 152_669 * 1_000_000


def test_a_missing_statement_is_reported_not_guessed():
    doc = prepare("<html><body><p>No statements here.</p></body></html>", doc_id="empty")
    result = extract_field(doc, FIELDS["revenue"], "2024-12-31")
    assert result.value is None
    assert result.status == "statement_not_found"
