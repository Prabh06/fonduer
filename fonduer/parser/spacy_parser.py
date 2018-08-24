import logging
from collections import defaultdict
from pathlib import Path

import pkg_resources

try:
    import spacy
    from spacy.cli import download
    from spacy import util
except Exception as e:
    raise Exception("spaCy not installed. Use `pip install spacy`.")


class Spacy(object):
    """
    spaCy
    https://spacy.io/

    Models for each target language needs to be downloaded using the
    following command:

    python -m spacy download en

    Default named entity types

    PERSON      People, including fictional.
    NORP        Nationalities or religious or political groups.
    FACILITY    Buildings, airports, highways, bridges, etc.
    ORG         Companies, agencies, institutions, etc.
    GPE         Countries, cities, states.
    LOC         Non-GPE locations, mountain ranges, bodies of water.
    PRODUCT     Objects, vehicles, foods, etc. (Not services.)
    EVENT       Named hurricanes, battles, wars, sports events, etc.
    WORK_OF_ART Titles of books, songs, etc.
    LANGUAGE    Any named language.

    DATE        Absolute or relative dates or periods.
    TIME        Times smaller than a day.
    PERCENT     Percentage, including "%".
    MONEY       Monetary values, including unit.
    QUANTITY    Measurements, as of weight or distance.
    ORDINAL     "first", "second", etc.
    CARDINAL    Numerals that do not fall under another type.

    """

    def __init__(self, lang="en"):
        self.logger = logging.getLogger(__name__)
        self.name = "spacy"
        self.model = Spacy.load_lang_model(lang)

    @staticmethod
    def is_package(name):
        """Check if string maps to a package installed via pip.
        name (unicode): Name of package.
        RETURNS (bool): True if installed package, False if not.

        From https://github.com/explosion/spaCy/blob/master/spacy/util.py

        """
        name = name.lower()  # compare package name against lowercase name
        packages = pkg_resources.working_set.by_key.keys()
        for package in packages:
            if package.lower().replace("-", "_") == name:
                return True
        return False

    @staticmethod
    def model_installed(name):
        """
        Check if spaCy language model is installed

        From https://github.com/explosion/spaCy/blob/master/spacy/util.py

        :param name:
        :return:
        """
        data_path = util.get_data_path()
        if not data_path or not data_path.exists():
            raise IOError("Can't find spaCy data path: %s" % str(data_path))
        if name in {d.name for d in data_path.iterdir()}:
            return True
        if Spacy.is_package(name):  # installed as package
            return True
        if Path(name).exists():  # path to model data directory
            return True
        return False

    @staticmethod
    def load_lang_model(lang):
        """
        Load spaCy language model or download if
        model is available and not installed

        Currenty supported spaCy languages

        en English (50MB)
        de German (645MB)
        fr French (1.33GB)
        es Spanish (377MB)

        :param lang:
        :return:
        """
        if not Spacy.model_installed(lang):
            download(lang)
        return spacy.load(lang)

    def custom_boundary_funct(self, all_sentence_objs):
        start_token_marker = []
        for sentence in all_sentence_objs:
            if len(sentence.words) > 0:
                start_token_marker += [True] + [False] * (len(sentence.words) - 1)

        def set_custom_boundary(doc):
            for token_nr, token in enumerate(doc):
                if start_token_marker[token_nr] is True:
                    doc[token.i].is_sent_start = True
                else:
                    doc[token.i].is_sent_start = False
            return doc

        return set_custom_boundary

    def parse(self, all_sentences):
        """
        Enrich a list of fonduer Sentence objects with NLP features.
        We merge and process the text of all Sentences for higher efficiency
        :param all_sentences: List of fonduer Sentence objects for one document
        :return:
        """

        if len(all_sentences) == 0:
            return  # Nothing to parse

        if self.model.has_pipe("sbd"):
            self.model.remove_pipe("sbd")
            self.logger.debug(
                "Removed sentencizer ('sbd') from model. Now in pipeline: {}".format(
                    self.model.pipe_names
                )
            )

        batch_char_limit = 250000
        sentence_batches = [[]]
        num_chars = 0
        for sentence in all_sentences:
            if num_chars + len(sentence.text) >= batch_char_limit:
                sentence_batches.append([sentence])
                num_chars = len(sentence.text)
            else:
                sentence_batches[-1].append(sentence)
                num_chars += len(sentence.text)

        # TODO: We could do this in parallel. Test speedup in the future
        for sentence_batch in sentence_batches:
            all_sentence_strings = [x.text for x in sentence_batch]
            merged_sentences = " ".join(all_sentence_strings)

            if not self.model.has_pipe("sentence_boundary_detector"):
                custom_boundary_fct = self.custom_boundary_funct(sentence_batch)
                self.model.add_pipe(
                    custom_boundary_fct,
                    before="parser",
                    name="sentence_boundary_detector",
                )
            else:
                self.model.remove_pipe(name="sentence_boundary_detector")
                custom_boundary_fct = self.custom_boundary_funct(sentence_batch)
                self.model.add_pipe(
                    custom_boundary_fct,
                    before="parser",
                    name="sentence_boundary_detector",
                )

            doc = self.model(merged_sentences)

            try:
                assert doc.is_parsed
            except Exception:
                self.logger.exception("{} was not parsed".format(doc))

            parsed_sentences = list(doc.sents)
            try:
                assert len(all_sentence_strings) == len(parsed_sentences)
            except AssertionError:
                self.logger.error(
                    "Number of parsed spacy sentences doesnt match input sentences:\
                 input {}, output: {}".format(
                        len(all_sentence_strings), len(parsed_sentences)
                    )
                )
                raise

            sentence_nr = -1
            for sent in parsed_sentences:
                sentence_nr += 1
                parts = defaultdict(list)

                for i, token in enumerate(sent):
                    parts["lemmas"].append(token.lemma_)
                    parts["pos_tags"].append(token.tag_)
                    parts["ner_tags"].append(
                        token.ent_type_ if token.ent_type_ else "O"
                    )
                    head_idx = (
                        0 if token.head is token else token.head.i - sent[0].i + 1
                    )
                    parts["dep_parents"].append(head_idx)
                    parts["dep_labels"].append(token.dep_)
                current_sentence_obj = all_sentences[sentence_nr]
                current_sentence_obj.pos_tags = parts["pos_tags"]
                current_sentence_obj.lemmas = parts["lemmas"]
                current_sentence_obj.ner_tags = parts["ner_tags"]
                current_sentence_obj.dep_parents = parts["dep_parents"]
                current_sentence_obj.dep_labels = parts["dep_labels"]
                yield current_sentence_obj

    def split_sentences(self, document, text):
        """
        Split input text into sentences that match CoreNLP's
         default format, but are not yet processed
        :param document: The Document context
        :param text: The text of the parent paragraph of the sentences
        :return:
        """

        if self.model.has_pipe("sentence_boundary_detector"):
            self.model.remove_pipe(name="sentence_boundary_detector")

        if not self.model.has_pipe("sbd"):
            sbd = self.model.create_pipe("sbd")  # add sentencizer
            self.model.add_pipe(sbd)
        try:
            doc = self.model(text, disable=["parser", "tagger", "ner"])
        except ValueError:
            # temporary increase character limit of spacy
            # 'Probably save' according to spacy, as no parser or NER is used
            self.model.max_length = 100000000
            doc = self.model(text, disable=["parser", "tagger", "ner"])
            self.model.max_length = 1000000

        position = 0
        for sent in doc.sents:
            parts = defaultdict(list)
            text = sent.text

            for i, token in enumerate(sent):
                parts["words"].append(str(token))
                parts["lemmas"].append("")  # placeholder for later NLP parsing
                parts["pos_tags"].append("")  # placeholder for later NLP parsing
                parts["ner_tags"].append("")  # placeholder for later NLP parsing
                parts["char_offsets"].append(token.idx)
                parts["abs_char_offsets"].append(token.idx)
                parts["dep_parents"].append(0)  # placeholder for later NLP parsing
                parts["dep_labels"].append("")  # placeholder for later NLP parsing

            # Add null entity array (matching null for CoreNLP)
            parts["entity_cids"] = ["O" for _ in parts["words"]]
            parts["entity_types"] = ["O" for _ in parts["words"]]

            # make char_offsets relative to start of sentence
            parts["char_offsets"] = [
                p - parts["char_offsets"][0] for p in parts["char_offsets"]
            ]
            parts["position"] = position

            # Link the sentence to its parent document object
            parts["document"] = document
            parts["text"] = text

            # Add null entity array (matching null for CoreNLP)
            parts["entity_cids"] = ["O" for _ in parts["words"]]
            parts["entity_types"] = ["O" for _ in parts["words"]]

            position += 1

            yield parts
