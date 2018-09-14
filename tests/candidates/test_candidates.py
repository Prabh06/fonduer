#! /usr/bin/env python
import logging
import os

import pytest

from fonduer import Meta
from fonduer.candidates import CandidateExtractor, MentionExtractor, MentionNgrams
from fonduer.candidates.matchers import PersonMatcher
from fonduer.candidates.mentions import Ngrams
from fonduer.candidates.models import Candidate, candidate_subclass, mention_subclass
from fonduer.parser import Parser
from fonduer.parser.models import Document, Sentence
from fonduer.parser.preprocessors import HTMLDocPreprocessor
from tests.shared.hardware_matchers import part_matcher, temp_matcher, volt_matcher
from tests.shared.hardware_spaces import (
    MentionNgramsPart,
    MentionNgramsTemp,
    MentionNgramsVolt,
)
from tests.shared.hardware_throttlers import temp_throttler, volt_throttler

logger = logging.getLogger(__name__)
ATTRIBUTE = "stg_temp_max"
DB = "cand_test"


def test_ngram_split(caplog):
    """Test ngram split."""
    caplog.set_level(logging.INFO)
    ngrams = Ngrams()
    sent = Sentence()

    # When a split_token appears in the middle of the text.
    sent.text = "New-Text"
    sent.words = ["New-Text"]
    sent.char_offsets = [0]
    sent.abs_char_offsets = [0]
    result = list(ngrams.apply(sent))

    assert len(result) == 3
    assert result[0].get_span() == "New-Text"
    assert result[1].get_span() == "New"
    assert result[2].get_span() == "Text"

    # When a text ends with a split_token.
    sent.text = "New-"
    sent.words = ["New-"]
    result = list(ngrams.apply(sent))

    assert len(result) == 2
    assert result[0].get_span() == "New-"
    assert result[1].get_span() == "New"

    # When a text starts with a split_token.
    sent.text = "-Text"
    sent.words = ["-Text"]
    result = list(ngrams.apply(sent))

    assert len(result) == 2
    assert result[0].get_span() == "-Text"
    assert result[1].get_span() == "Text"

    # When more than one split_token appears.
    sent.text = "New/Text-Word"
    sent.words = ["New/Text-Word"]
    result = list(ngrams.apply(sent))

    assert len(result) == 3
    assert result[0].get_span() == "New/Text-Word"
    assert result[1].get_span() == "New"
    assert result[2].get_span() == "Text-Word"


def test_span_char_start_and_char_end(caplog):
    """Test chart_start and char_end of TemporarySpan that comes from Ngrams.apply."""
    caplog.set_level(logging.INFO)
    ngrams = Ngrams()
    sent = Sentence()
    sent.text = "BC548BG"
    sent.words = ["BC548BG"]
    sent.char_offsets = [0]
    sent.abs_char_offsets = [0]
    result = list(ngrams.apply(sent))

    assert len(result) == 1
    assert result[0].get_span() == "BC548BG"
    assert result[0].char_start == 0
    assert result[0].char_end == 6


def test_cand_gen(caplog):
    """Test extracting candidates from mentions from documents."""
    caplog.set_level(logging.INFO)
    # SpaCy on mac has issue on parallel parsing
    if os.name == "posix":
        logger.info("Using single core.")
        PARALLEL = 1
    else:
        PARALLEL = 2  # Travis only gives 2 cores

    max_docs = 10
    session = Meta.init("postgres://localhost:5432/" + DB).Session()

    docs_path = "tests/data/html/"
    pdf_path = "tests/data/pdf/"

    # Parsing
    logger.info("Parsing...")
    doc_preprocessor = HTMLDocPreprocessor(docs_path, max_docs=max_docs)
    corpus_parser = Parser(
        session, structural=True, lingual=True, visual=True, pdf_path=pdf_path
    )
    corpus_parser.apply(doc_preprocessor, parallelism=PARALLEL)
    assert session.query(Document).count() == max_docs
    assert session.query(Sentence).count() == 5548
    docs = session.query(Document).order_by(Document.name).all()

    # Mention Extraction
    part_ngrams = MentionNgramsPart(parts_by_doc=None, n_max=3)
    temp_ngrams = MentionNgramsTemp(n_max=2)
    volt_ngrams = MentionNgramsVolt(n_max=1)

    Part = mention_subclass("Part")
    Temp = mention_subclass("Temp")
    Volt = mention_subclass("Volt")

    with pytest.raises(ValueError):
        mention_extractor = MentionExtractor(
            session,
            [Part, Temp, Volt],
            [part_ngrams, volt_ngrams],  # Fail, mismatched arity
            [part_matcher, temp_matcher, volt_matcher],
        )
    with pytest.raises(ValueError):
        mention_extractor = MentionExtractor(
            session,
            [Part, Temp, Volt],
            [part_ngrams, temp_matcher, volt_ngrams],
            [part_matcher, temp_matcher],  # Fail, mismatched arity
        )

    mention_extractor = MentionExtractor(
        session,
        [Part, Temp, Volt],
        [part_ngrams, temp_ngrams, volt_ngrams],
        [part_matcher, temp_matcher, volt_matcher],
    )
    mention_extractor.apply(docs, parallelism=PARALLEL)

    assert session.query(Part).count() == 234
    assert session.query(Volt).count() == 107
    assert session.query(Temp).count() == 125
    part = session.query(Part).order_by(Part.id).all()[0]
    volt = session.query(Volt).order_by(Volt.id).all()[0]
    temp = session.query(Temp).order_by(Temp.id).all()[0]
    logger.info("Part: {}".format(part.span))
    logger.info("Volt: {}".format(volt.span))
    logger.info("Temp: {}".format(temp.span))

    # Candidate Extraction
    PartTemp = candidate_subclass("PartTemp", [Part, Temp])
    PartVolt = candidate_subclass("PartVolt", [Part, Volt])

    with pytest.raises(ValueError):
        candidate_extractor = CandidateExtractor(
            session,
            [PartTemp, PartVolt],
            throttlers=[
                temp_throttler,
                volt_throttler,
                volt_throttler,
            ],  # Fail, mismatched arity
        )

    with pytest.raises(ValueError):
        candidate_extractor = CandidateExtractor(
            session,
            [PartTemp],  # Fail, mismatched arity
            throttlers=[temp_throttler, volt_throttler],
        )

    # Test that no throttler in candidate extractor
    candidate_extractor = CandidateExtractor(
        session, [PartTemp, PartVolt]
    )  # Pass, no throttler

    candidate_extractor.apply(docs, split=0, parallelism=PARALLEL)

    assert session.query(PartTemp).count() == 3654
    assert session.query(PartVolt).count() == 3657
    assert session.query(Candidate).count() == 7311
    candidate_extractor.clear_all(split=0)
    assert session.query(Candidate).count() == 0

    # Test that None in throttlers in candidate extractor
    candidate_extractor = CandidateExtractor(
        session, [PartTemp, PartVolt], throttlers=[temp_throttler, None]
    )

    candidate_extractor.apply(docs, split=0, parallelism=PARALLEL)

    assert session.query(PartTemp).count() == 3530
    assert session.query(PartVolt).count() == 3657
    assert session.query(Candidate).count() == 7187
    candidate_extractor.clear_all(split=0)
    assert session.query(Candidate).count() == 0

    candidate_extractor = CandidateExtractor(
        session, [PartTemp, PartVolt], throttlers=[temp_throttler, volt_throttler]
    )

    candidate_extractor.apply(docs, split=0, parallelism=PARALLEL)

    assert session.query(PartTemp).count() == 3530
    assert session.query(PartVolt).count() == 3313
    assert session.query(Candidate).count() == 6843
    assert docs[0].name == "112823"
    assert len(docs[0].parts) == 70
    assert len(docs[0].volts) == 33
    assert len(docs[0].temps) == 18

    # Test that deletion of a Candidate does not delete the Mention
    session.query(PartTemp).delete()
    assert session.query(PartTemp).count() == 0
    assert session.query(Temp).count() == 125
    assert session.query(Part).count() == 234

    # Test deletion of Candidate if Mention is deleted
    assert session.query(PartVolt).count() == 3313
    assert session.query(Volt).count() == 107
    session.query(Volt).delete()
    assert session.query(Volt).count() == 0
    assert session.query(PartVolt).count() == 0


def test_ngrams(caplog):
    """Test ngram limits in mention extraction"""
    caplog.set_level(logging.INFO)
    PARALLEL = 1

    max_docs = 1
    session = Meta.init("postgres://localhost:5432/" + DB).Session()

    docs_path = "tests/data/pure_html/lincoln_short.html"

    logger.info("Parsing...")
    doc_preprocessor = HTMLDocPreprocessor(docs_path, max_docs=max_docs)
    corpus_parser = Parser(session, structural=True, lingual=True)
    corpus_parser.apply(doc_preprocessor, parallelism=PARALLEL)
    assert session.query(Document).count() == max_docs
    assert session.query(Sentence).count() == 503
    docs = session.query(Document).order_by(Document.name).all()

    # Mention Extraction
    Person = mention_subclass("Person")
    person_ngrams = MentionNgrams(n_max=3)
    person_matcher = PersonMatcher()

    mention_extractor = MentionExtractor(
        session, [Person], [person_ngrams], [person_matcher]
    )
    mention_extractor.apply(docs, parallelism=PARALLEL)

    assert session.query(Person).count() == 126
    mentions = session.query(Person).all()
    assert len([x for x in mentions if x.span.get_n() == 1]) == 50
    assert len([x for x in mentions if x.span.get_n() > 3]) == 0

    # Test for unigram exclusion
    person_ngrams = MentionNgrams(n_min=2, n_max=3)
    mention_extractor = MentionExtractor(
        session, [Person], [person_ngrams], [person_matcher]
    )
    mention_extractor.apply(docs, parallelism=PARALLEL)
    assert session.query(Person).count() == 76
    mentions = session.query(Person).all()
    assert len([x for x in mentions if x.span.get_n() == 1]) == 0
    assert len([x for x in mentions if x.span.get_n() > 3]) == 0
