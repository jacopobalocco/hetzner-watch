from hetzner_watch.compare import Candidate, Delta
from hetzner_watch.hetzner import Baseline, Offer, Specs
from hetzner_watch.report import ISSUE_TITLE, render_body, render_close_comment
from hetzner_watch.snapshot import Changes

SPECS = Specs(2, 2.0, 40, "shared", "x86")
BASELINE = Baseline("example-server-1", "CPX12", "fsn1", SPECS, 5.0, 20.0)
NO_CHANGES = Changes(new=(), price_changed=(), gone=())


def candidate(name="CAX21", price=7.0, multiarch=False, deltas=None):
    offer = Offer(name, "fsn1", SPECS, price, 20.0, deprecated=False, available=True)
    return Candidate(
        offer,
        deltas or (Delta("memory_gb", 3.0, 9.5, True), Delta("disk_gb", 45.0, 32.0, False)),
        needs_multiarch=multiarch,
    )


def body(*candidates, changes=NO_CHANGES, baseline_location_available=True):
    return render_body(
        BASELINE, list(candidates), changes, "2026-08-07T07:00:00Z",
        baseline_location_available,
    )


def test_issue_title_is_the_spec_value():
    assert ISSUE_TITLE == "Alternative Hetzner sotto soglia"


def test_body_states_the_baseline_used():
    rendered = body(candidate())
    assert "CPX12" in rendered
    assert "example-server-1" in rendered
    assert "2026-08-07T07:00:00Z" in rendered


def test_news_section_comes_before_the_full_table():
    changes = Changes(new=("CAX21@fsn1",), price_changed=(), gone=())
    rendered = body(candidate(), changes=changes)
    assert rendered.index("Novità dall'ultimo run") < rendered.index("Tutti i candidati")


def test_no_changes_says_so_in_news_section():
    # NO_CHANGES is the default `changes=` in every other test in this file;
    # none of them assert the empty-news branch actually renders its text.
    assert "Nessuna variazione." in body(candidate())


def test_improvements_and_regressions_are_both_signed():
    # +6.5 / -13 (not the round, easily-collided +6 / -20 from the brief):
    # distinct magnitudes, opposite signs, and unlikely to coincide with any
    # other number on the page (price 7.00, baseline price 5.00, specs 2/40).
    rendered = body(candidate())
    assert "RAM +6.5" in rendered
    assert "Disco -13" in rendered


def test_multiarch_candidates_are_flagged():
    assert "multi-arch" in body(candidate(multiarch=True))
    assert "multi-arch" not in body(candidate(multiarch=False))


def test_price_changes_show_both_values_in_order():
    # Asserting the two values joined by the arrow (not two separate `in`
    # checks) catches a before/after swap, which two independent substring
    # checks would miss since both numbers would still be present either way.
    changes = Changes(new=(), price_changed=(("CAX21@fsn1", 7.20, 6.85),), gone=())
    rendered = body(candidate(price=6.85), changes=changes)
    assert "7.20 € → 6.85 €" in rendered


def test_disappearances_are_listed():
    changes = Changes(new=(), price_changed=(), gone=("CCX13@fsn1",))
    assert "CCX13@fsn1" in body(candidate(), changes=changes)


def test_spec_changes_are_listed_in_the_news_section():
    # Finding 3: a spec change (e.g. disk 40 -> 80 GB) at an unchanged price
    # must be visible in "novità", not silently absorbed into the table with
    # no explanation of what moved.
    changes = Changes(new=(), price_changed=(), gone=(), spec_changed=("CAX21@fsn1",))
    rendered = body(candidate(), changes=changes)
    assert rendered.index("CAX21@fsn1") < rendered.index("Tutti i candidati")


def test_baseline_change_is_announced_in_the_news_section():
    # Finding 4: resizing the baseline with an unchanged candidate set must
    # be visible in "novità", naming the new baseline type/location.
    changes = Changes(new=(), price_changed=(), gone=(), baseline_changed=True)
    rendered = body(candidate(), changes=changes)
    news_section = rendered.split("Tutti i candidati")[0]
    assert "CPX12" in news_section
    assert "fsn1" in news_section


def test_no_news_text_is_absent_once_there_is_a_spec_or_baseline_change():
    changes = Changes(new=(), price_changed=(), gone=(), spec_changed=("CAX21@fsn1",))
    assert "Nessuna variazione." not in body(candidate(), changes=changes)


def test_empty_candidate_list_says_so():
    assert "Nessuna alternativa" in body()


def test_traffic_delta_uses_tib_not_tb():
    # included_traffic_tib is base-1024 (tebibytes); the label must never
    # read TB, and "TB" is not a substring of "TiB" so this genuinely
    # distinguishes the two.
    rendered = body(candidate(deltas=(Delta("included_traffic_tib", 20.0, 1.0, False),)))
    assert "TiB" in rendered
    assert "TB" not in rendered


def test_zero_availability_at_the_baseline_location_is_stated_explicitly():
    # Finding 2: a table with no rows for the baseline's own location reads
    # as a bug unless the report says otherwise outright.
    rendered = body(candidate(), baseline_location_available=False)
    assert "Nessun tipo attualmente disponibile" in rendered


def test_baseline_location_availability_note_is_absent_when_available():
    rendered = body(candidate(), baseline_location_available=True)
    assert "Nessun tipo attualmente disponibile" not in rendered


def test_zero_availability_note_names_the_baseline_location_not_a_constant():
    # A baseline in nbg1 (not the module-level fsn1 fixture), so a hardcoded
    # "fsn1" in the note text cannot pass this by coincidence.
    nbg1_baseline = Baseline("srv-b", "CPX12", "nbg1", SPECS, 5.0, 20.0)
    rendered = render_body(
        nbg1_baseline, [candidate()], NO_CHANGES, "2026-08-07T07:00:00Z", False,
    )
    assert "Nessun tipo attualmente disponibile in nbg1" in rendered


def test_close_comment_mentions_the_run_time():
    assert "2026-08-07T07:00:00Z" in render_close_comment("2026-08-07T07:00:00Z")
