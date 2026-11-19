"""#152: the only 500 of dispatch_finding (3-sep, daemon_stderr.log) was
`sqlite3.IntegrityError: constraint failed` on `INSERT INTO tickets_fts (rowid, ...)`.
That is what FTS5 says when the rowid is already taken (measured). The ticket id was
just created, so an index row with that rowid can only be an orphan: no ticket ever
owned it. The cause of the orphan was not found (8 weeks, ~660 tickets, never again;
the production base is 806/806 on 28-oct). What is fixed is the effect. The orphan
is removed, the ticket is indexed, and the log says what was there, so the next one
can be traced instead of failing the request.
"""

import logging

import pytest

from faros import db, service as s


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _next_id(conn):
    return (conn.execute("SELECT COALESCE(MAX(id), 0) FROM tickets").fetchone()[0]) + 1


def test_an_orphan_index_row_no_longer_breaks_the_new_ticket(conn, caplog):
    s.propose_ticket(conn, "owner", "la primera", owner="alice")
    nid = _next_id(conn)
    conn.execute("INSERT INTO tickets_fts (rowid, title, description) VALUES (?, 'huérfana', '')", (nid,))
    conn.commit()
    with caplog.at_level(logging.WARNING, logger="faros.service"):
        t = s.propose_ticket(conn, "owner", "la nueva", owner="alice")
    assert t["ticket"]["id"] == nid
    rows = conn.execute("SELECT title FROM tickets_fts WHERE rowid=?", (nid,)).fetchall()
    assert [r[0] for r in rows] == ["la nueva"]
    hits = conn.execute("SELECT rowid FROM tickets_fts WHERE tickets_fts MATCH 'nueva'").fetchall()
    assert [r[0] for r in hits] == [nid]
    assert any("#152" in r.message and "huérfana" in r.message for r in caplog.records)


def test_without_an_orphan_nothing_is_logged(conn, caplog):
    with caplog.at_level(logging.WARNING, logger="faros.service"):
        s.propose_ticket(conn, "owner", "normal", owner="alice")
    assert not [r for r in caplog.records if "#152" in r.message]
