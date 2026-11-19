"""#657 (20-oct, de la #645 de Lienzo): cada decisión guarda DESDE QUÉ SUPERFICIE se decidió. El escritorio y el
móvil llaman al mismo endpoint contra el mismo daemon, así que el daemon no podía saberlo y «Pepe decide algo desde
el escritorio» no era comprobable. Valor EXPLÍCITO por cliente (desktop | mobile | mcp); la ausencia queda vacía y
NUNCA se interpreta como escritorio (matiz de Lienzo: un campo que deduce miente cuando aparece un cliente nuevo).
"""

import asyncio
import sqlite3

import pytest

from faros import db, decisions as dec, mcp_server
from faros.service import TicketError

OPTS = [{"label": "sí", "text": "sí, hazlo"}, {"label": "no", "text": "no lo hagas"}]


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _decidida(conn, **kw):
    d = dec.ask(conn, "alice", "¿lo hago?", OPTS)
    return dec.decide(conn, "owner", d["id"], option="sí", rationale="porque sí", **kw)


@pytest.mark.parametrize("superficie", ["desktop", "mobile", "mcp"])
def test_la_superficie_queda_en_el_historial(conn, superficie):
    d = _decidida(conn, surface=superficie)
    assert d["history"][-1]["event"] == "decided" and d["history"][-1]["surface"] == superficie


def test_sin_superficie_queda_vacia_y_nunca_escritorio(conn):
    d = _decidida(conn)
    assert d["history"][-1]["surface"] is None


def test_una_superficie_inventada_se_rechaza(conn):
    d = dec.ask(conn, "alice", "¿lo hago?", OPTS)
    with pytest.raises(TicketError) as e:
        dec.decide(conn, "owner", d["id"], option="sí", rationale="x", surface="tablet")
    assert "surface" in str(e.value)
    assert dec.get_decision(conn, d["id"])["status"] != "decided"


def test_la_lista_de_decisiones_tambien_la_trae(conn):
    d = _decidida(conn, surface="mobile")
    lista = dec.list_decisions(conn)
    assert next(x for x in lista if x["id"] == d["id"])["history"][-1]["surface"] == "mobile"


def test_decidir_por_mcp_dice_mcp(monkeypatch):
    llamadas = []
    monkeypatch.setattr(mcp_server, "_call", lambda method, path, **kw: llamadas.append(kw) or {"ok": True})
    asyncio.run(mcp_server.mcp.call_tool("decide", {"agent": "Pepe", "decision_id": 1, "option": "sí",
                                                     "rationale": "x"}))
    assert llamadas[0]["json"]["surface"] == "mcp"


def test_una_base_v8_gana_la_columna_al_abrirla(tmp_path):
    ruta = tmp_path / "v8.db"
    c = db.connect(ruta)
    c.execute("ALTER TABLE decision_history DROP COLUMN surface")
    c.execute("DELETE FROM schema_version WHERE version > 8")
    c.commit()
    c.close()
    c = db.connect(ruta)
    assert "surface" in {r[1] for r in c.execute("PRAGMA table_info(decision_history)")}
    c.close()
