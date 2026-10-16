#!/usr/bin/env python3
# nlp.smoke.py — the D3 verification for the `nlp` env.
#
# Same contract (assemble + import + do real work, inside the built arm64 image). The risky
# natives are spaCy's Cython extensions (the tokenizer, the `StringStore`, `Doc`
# serialization) and thinc's compiled ops underneath the pipeline.
#
# NOTHING IS DOWNLOADED. spaCy's documented install path for a pipeline is
# `python -m spacy download en_core_web_sm`, a runtime fetch from GitHub. conda-forge
# packages the model instead, so this asserts the model loads from the installed package —
# which is the whole reason this env could be built while `casatools` could not.
#
# THE STRONGEST CHECKS ARE IDENTITIES, NOT MODEL OUTPUTS, and the distinction matters for a
# statistical pipeline. Tagger and NER predictions are model behaviour: they can change with
# a model version without anything being broken, so asserting them narrowly would make this
# gate fail on upstream retraining rather than on a packaging fault. What cannot change
# without something being wrong:
#
#   * TOKENIZATION IS LOSSLESS. spaCy guarantees the document reconstructs its input
#     exactly — measured, `"".join(t.text_with_ws) == text` and `doc.text == text`.
#   * THE STRING STORE ROUND-TRIPS. string -> 64-bit hash -> string is exact.
#   * `Doc.to_bytes` / `from_bytes` PRESERVES TOKENS AND TAGS exactly.
#
# So the model's own predictions are checked loosely (a handful of unambiguous cases) and
# the machinery around them is checked exactly.
#
# Pure stdlib + the env's own packages. Exit 0 = functionally sound.
import platform
import sys
import traceback

FAILURES = []

MODEL = "en_core_web_sm"
# Deliberately full of tokenizer traps: abbreviations that must NOT split on their period,
# a multi-word entity, a currency amount, and a date.
TEXT = "Dr. Smith flew to San Francisco on Jan. 5, 2021."
STATE = {}


def check(name):
    def wrap(fn):
        try:
            fn()
            print(f"  ok   {name}")
        except Exception as exc:  # noqa: BLE001
            FAILURES.append((name, exc))
            print(f"  FAIL {name}: {exc!r}")
            traceback.print_exc()
        return fn
    return wrap


# --- 1. imports + the packaged model ----------------------------------------------
HEADLINE = ["numpy", "spacy", "spacy.tokens", "thinc", "en_core_web_sm"]
print("[smoke] 1. imports")
for mod in HEADLINE:
    @check(f"import {mod}")
    def _imp(mod=mod):
        __import__(mod)


@check("the English pipeline loads from the installed package, with no download")
def _load():
    import spacy
    nlp = spacy.load(MODEL)
    # The full pipeline must be present: a model that loaded but lost its tagger or NER
    # would still "work" for tokenization and quietly produce no annotations.
    for component in ("tok2vec", "tagger", "parser", "lemmatizer", "ner"):
        assert component in nlp.pipe_names, \
            f"{component!r} missing from the pipeline: {nlp.pipe_names}"
    STATE["nlp"] = nlp
    STATE["spacy_version"] = spacy.__version__
    print(f"       (spacy {spacy.__version__}, {MODEL}: {nlp.pipe_names})")


# --- 2. the exact identities -------------------------------------------------------
print("[smoke] 2. tokenization and serialization identities")


@check("tokenization is lossless — the document reconstructs its input exactly")
def _lossless():
    nlp = STATE.get("nlp")
    assert nlp is not None, "the model did not load"
    doc = nlp(TEXT)
    # spaCy's contract: whitespace is carried on the tokens, so the original text is
    # recoverable byte for byte. EQUALITY, not similarity — a tokenizer that dropped or
    # normalised a character fails here and nowhere else in a pipeline test.
    assert doc.text == TEXT, f"doc.text != input:\n  {doc.text!r}\n  {TEXT!r}"
    rebuilt = "".join(t.text_with_ws for t in doc)
    assert rebuilt == TEXT, f"token concatenation != input:\n  {rebuilt!r}"
    toks = [t.text for t in doc]
    # The abbreviation traps: these must survive as single tokens rather than splitting on
    # the period, which is exactly what a mis-built tokenizer exception table gets wrong.
    for abbrev in ("Dr.", "Jan."):
        assert abbrev in toks, f"{abbrev!r} was split; tokens were {toks}"
    STATE["doc"] = doc
    STATE["tokens"] = toks
    print(f"       ({len(toks)} tokens, text reconstructed exactly, 'Dr.' and 'Jan.' "
          "kept whole)")


@check("the string store round-trips string -> hash -> string exactly")
def _stringstore():
    nlp = STATE.get("nlp")
    doc = STATE.get("doc")
    assert doc is not None, "the tokenization check did not run"
    store = nlp.vocab.strings
    # Every token's text is in the store already, reachable by its `orth` hash. This is the
    # compiled StringStore, so it is a real check on a Cython data structure rather than on
    # Python dict behaviour.
    for tok in doc:
        assert store[tok.orth] == tok.text, \
            f"hash {tok.orth} gave {store[tok.orth]!r}, expected {tok.text!r}"
    # And an explicit add round-trips too, including a multi-word string that is not a
    # token.
    h = store.add("San Francisco")
    assert store[h] == "San Francisco", f"explicit add round-trip gave {store[h]!r}"
    print(f"       ({len(doc)} token hashes round-tripped, plus an explicit multi-word add)")


@check("Doc.to_bytes / from_bytes preserves tokens and tags exactly")
def _serialize():
    from spacy.tokens import Doc
    nlp = STATE.get("nlp")
    doc = STATE.get("doc")
    assert doc is not None, "the tokenization check did not run"
    blob = doc.to_bytes()
    back = Doc(nlp.vocab).from_bytes(blob)
    # Equality on both the surface forms and the predicted tags: serialization that lost
    # the annotation layer would still pass a token-only comparison.
    assert [t.text for t in back] == STATE["tokens"], "tokens changed across to/from_bytes"
    assert [t.tag_ for t in back] == [t.tag_ for t in doc], \
        "fine-grained tags changed across to/from_bytes"
    assert back.text == doc.text, "text changed across to/from_bytes"
    print(f"       ({len(blob)} bytes; tokens, tags and text all identical)")


# --- 3. the model's own predictions, checked loosely on purpose --------------------
print("[smoke] 3. tagger and NER on unambiguous cases (loose by design)")


@check("the pipeline produces sane tags and entities")
def _predictions():
    nlp = STATE.get("nlp")
    doc = nlp("Apple was founded in California.")
    # Unambiguous enough to be stable across model minor versions, and asserted as
    # membership rather than an exact sequence — these are MODEL OUTPUTS, so a narrow
    # assertion here would make this gate fail on upstream retraining rather than on a
    # packaging fault. See the module docstring.
    pos = {t.text: t.pos_ for t in doc}
    assert pos.get("Apple") in ("PROPN", "NOUN"), f"Apple tagged {pos.get('Apple')!r}"
    assert pos.get("founded") == "VERB", f"founded tagged {pos.get('founded')!r}"
    labels = {e.text: e.label_ for e in doc.ents}
    assert labels, "no entities found at all — the NER component is not running"
    assert labels.get("California") in ("GPE", "LOC"), \
        f"California tagged {labels.get('California')!r}"
    # Every token must carry a lemma; empty lemmas mean the lemmatizer silently no-oped.
    assert all(t.lemma_ for t in doc), "some tokens have no lemma"
    print(f"       (entities {sorted(labels.items())}; 'founded' -> VERB; all lemmas set)")


@check("the small model's lack of word vectors is as documented")
def _no_vectors():
    nlp = STATE.get("nlp")
    # Asserted so the limitation is discoverable from the test rather than surprising
    # someone downstream: en_core_web_sm ships NO static vectors, and `has_vector` is True
    # anyway because it falls back to tok2vec output. If a future model DOES ship vectors
    # this check fails loudly, which is the right outcome — the README says it has none.
    shape = nlp.vocab.vectors.shape
    assert shape == (0, 0), (
        f"en_core_web_sm now reports vectors of shape {shape}; the README and spec say it "
        "has none, so update them")
    print(f"       (vocab.vectors.shape {shape} — no static vectors, as documented)")


# --- verdict ----------------------------------------------------------------------
print("[smoke] " + ("-" * 50))
if FAILURES:
    print(f"[smoke] FAILED: {len(FAILURES)} check(s): " + ", ".join(n for n, _ in FAILURES))
    sys.exit(1)
print("[smoke] PASSED: nlp env assembles; tokenization is lossless and serialization exact "
      "on " + platform.machine() + f" (python {platform.python_version()}) — verified.")
