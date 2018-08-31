#! /usr/bin/env python
import pytest

from fonduer import Meta

DB = "meta_test"


def test_meta_connection_strings(caplog):
    """Simple sanity checks for validating postgres connection strings."""

    with pytest.raises(ValueError):
        Meta.init("postgres" + DB).Session()

    with pytest.raises(ValueError):
        Meta.init("sqlite://somethingsilly" + DB).Session()

    with pytest.raises(ValueError):
        Meta.init("postgres://somethingsilly:5432/").Session()

    Meta.init("postgres://localhost:5432/" + DB).Session()
    assert Meta.DBNAME == DB
    Meta.init("postgres://localhost:5432/" + "cand_test").Session()
    assert Meta.DBNAME == "cand_test"
