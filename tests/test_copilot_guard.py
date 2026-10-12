"""The copilot's promise: the language model never writes a number; our code fills every one in."""
from conftest import small_facts

from floodmap.copilot import check, fact_table, fill


def test_the_ai_may_not_write_digits_or_number_words():
    table = fact_table(small_facts())
    assert "the model wrote a digit itself" in check("There are 358 buildings hit.", table)
    assert "the model wrote a digit itself" in check("३५८ भवनमा असर परेको छ।", table)   # Nepali digits too
    assert any("number words" in p for p in check("Three villages are cut off.", table))


def test_text_made_of_placeholders_is_accepted():
    table = fact_table(small_facts())
    assert check("{buildings_hit} buildings were hit and {cut_off_count} village is cut off: {cutoff1_name}.", table) == []


def test_a_made_up_placeholder_is_rejected():
    assert check("{people_dead} people died.", fact_table(small_facts())) == ["unknown placeholder {people_dead}"]


def test_our_code_fills_in_the_real_numbers_in_both_languages():
    table = fact_table(small_facts())
    assert fill("{buildings_hit} buildings, {cutoff1_name} has priority {cutoff1_priority}", table, "en") == \
        "358 buildings, Resuo has priority 88"
    assert fill("{buildings_hit}", table, "ne") == "३५८"
