from trf.eval.loader import list_cases, load_case, load_expected_verdicts, load_reference


def test_list_cases_excludes_references_and_finds_dataset():
    cases = list_cases()
    assert not any(c.endswith(".fixed") for c in cases)
    assert len(cases) == 15 and "pandas_sales_analysis" in cases


def test_every_case_has_reference_and_assertions():
    from trf.eval.loader import DATASET_DIR

    for c in list_cases():
        assert (DATASET_DIR / f"{c}.fixed.md").exists(), c
        assert load_case(c).assertions, f"{c}: no assertions"


def test_reference_shares_assertions_but_not_code():
    orig, ref = load_case("pandas_sales_analysis"), load_reference("pandas_sales_analysis")
    assert orig.assertions == ref.assertions
    assert "df.append" in orig.tutorial_md and "df.append" not in ref.tutorial_md


def test_expected_verdicts_missing_file_is_empty():
    assert load_expected_verdicts("no_such_case") == {}
