"""Bloco 18: privacidade. Exportar e apagar contato, retenção, consentimento e exclusão da conta."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from fm_seller import privacy
from fm_seller.ai.model import SimulatedAiModel
from fm_seller.ai.seller import run_ai_replies
from fm_seller.channels.social import upsert_social_conversation
from fm_seller.channels.whatsapp import upsert_conversation
from fm_seller.db import Database
from fm_seller.recovery.engine import _is_suppressed
from tests.conftest import Env, connect_whatsapp, login, unique_email
from tests.test_whatsapp_inbound import payload, post, text
from tests.test_whatsapp_inbound import wa as wa

PHONE = "5511977776666"
EMAIL = "cliente.privado@example.test"
BODY = "meu segredo é o texto-unico-da-conversa-777"


def _owner(client: TestClient, env: Env, name: str = "Loja Privacidade") -> tuple[str, str]:
    email = unique_email("priv")
    tid = env.tenant(name, email)
    assert login(client, email).status_code == 200
    return tid, email


def _populate(
    env: Env, db: Database, tid: str, phone: str = PHONE, email: str | None = EMAIL
) -> uuid.UUID:
    """Um contato com conversa, mensagens, um caso aberto com passo e um caso encerrado."""
    tenant = uuid.UUID(tid)
    with db.tx(tenant_id=tenant) as conn:
        contact, conv = upsert_conversation(conn, tenant, phone, "Maria Privada")
    env.sql("UPDATE contacts SET email = %s WHERE id = %s", (email, str(contact)))
    for direction, author, body in (("in", "customer", BODY), ("out", "bot", "Posso ajudar!")):
        env.sql(
            "INSERT INTO messages (tenant_id, conversation_id, direction, author, body, status) "
            "VALUES (%s, %s, %s, %s, %s, 'sent')",
            (tid, str(conv), direction, author, body),
        )
    for ref, status in ((f"aberto-{phone}", "open"), (f"fechado-{phone}", "stopped")):
        case = env.sql(
            "INSERT INTO recovery_cases (tenant_id, contact_id, trigger_kind, external_ref, "
            "product_name, amount_cents, status) "
            "VALUES (%s, %s, 'pix_pending', %s, 'Sofá', 9900, %s) "
            "RETURNING id",
            (tid, str(contact), ref, status),
        )[0][0]
        env.sql(
            "INSERT INTO recovery_steps (tenant_id, case_id, step_no, template_key, scheduled_at, "
            "first_scheduled_at) VALUES (%s, %s, 1, 'pix_1', now(), now())",
            (tid, str(case)),
        )
    return contact


def _count(env: Env, table: str, tid: str) -> int:
    return int(env.sql(f"SELECT count(*) FROM {table} WHERE tenant_id = %s", (tid,))[0][0])


# ---------------------------------------------------------------- exportar


def test_export_finds_by_phone_email_and_channel_and_has_everything(
    client: TestClient, env: Env, db: Database
) -> None:
    tid, _ = _owner(client, env)
    contact = _populate(env, db, tid)
    tenant = uuid.UUID(tid)
    with db.tx(tenant_id=tenant) as conn:
        upsert_social_conversation(conn, tenant, "messenger", "psid-123")
    for ident in ("(11) 97777-6666", PHONE, EMAIL.upper()):
        res = client.post("/v1/privacy/contacts/export", json={"identifier": ident})
        assert res.status_code == 200, res.text
        data = res.json()["data"]
        assert data["contato"]["telefone"] == PHONE and data["contato"]["email"] == EMAIL
        assert data["contato"]["nome"] == "Maria Privada"
        texts = [m["texto"] for m in data["conversas"][0]["mensagens"]]
        assert BODY in texts and "Posso ajudar!" in texts
        assert len(data["recuperacao"]) == 2 and data["recuperacao"][0]["mensagens_de_recuperacao"]
    assert res.headers["cache-control"] == "no-store"
    social = client.post("/v1/privacy/contacts/export", json={"identifier": "messenger:psid-123"})
    assert social.json()["found"] is True and social.json()["data"]["contato"]["telefone"] is None
    none = client.post("/v1/privacy/contacts/export", json={"identifier": "11900000000"})
    assert none.json() == {"found": False}
    audit_rows = env.sql(
        "SELECT target, detail::text FROM audit_log WHERE tenant_id = %s "
        "AND action = 'privacy.contact_exported'",
        (tid,),
    )
    assert audit_rows and all(str(contact) == r[0] for r in audit_rows[:1])
    blob = json.dumps(audit_rows)
    for pii in (PHONE, EMAIL, "Maria", BODY):
        assert pii not in blob  # a auditoria guarda o id interno, nunca o dado da pessoa


def test_export_validation_roles_and_isolation(client: TestClient, env: Env, db: Database) -> None:
    tid, _ = _owner(client, env, "Loja Dona")
    _populate(env, db, tid)
    agent = unique_email("agent")
    env.invite(tid, agent, "agent")
    client.post("/v1/auth/logout")
    assert login(client, agent).status_code == 200
    assert client.post("/v1/privacy/contacts/export", json={"identifier": PHONE}).status_code == 403
    assert client.get("/v1/privacy").status_code == 403
    client.post("/v1/auth/logout")
    _owner(client, env, "Loja Outra")
    assert client.post("/v1/privacy/contacts/export", json={"identifier": PHONE}).json() == {
        "found": False
    }
    erase = client.post("/v1/privacy/contacts/erase", json={"identifier": PHONE, "confirm": True})
    assert erase.json() == {"found": False}  # não alcança o contato de outro cliente
    assert _count(env, "contacts", tid) == 1
    for bad in ("", "abc", "12"):
        res = client.post("/v1/privacy/contacts/export", json={"identifier": bad})
        assert res.status_code == 400 and res.json()["error"]["code"] == "invalid_identifier"
    huge = client.post("/v1/privacy/contacts/export", json={"identifier": "x" * 300})
    assert huge.status_code == 422


# ---------------------------------------------------------------- apagar


def test_erase_removes_everything_of_the_contact_and_is_repeatable(
    client: TestClient, env: Env, db: Database
) -> None:
    tid, _ = _owner(client, env)
    other = _populate(env, db, tid, phone="5511966665555", email=None)  # outro contato, intocado
    _populate(env, db, tid)
    body = {"identifier": PHONE, "confirm": True}
    no_confirm = client.post("/v1/privacy/contacts/erase", json={"identifier": PHONE})
    assert (
        no_confirm.status_code == 400 and no_confirm.json()["error"]["code"] == "confirm_required"
    )
    res = client.post("/v1/privacy/contacts/erase", json=body).json()
    assert res["found"] is True
    assert res["erased"] == {"mensagens": 2, "conversas": 1, "casos": 2}
    gone = env.sql(
        "SELECT count(*) FROM contacts WHERE tenant_id = %s AND phone = %s", (tid, PHONE)
    )
    assert gone[0][0] == 0
    assert _count(env, "contacts", tid) == 1 and _count(env, "conversations", tid) == 1
    assert env.sql("SELECT count(*) FROM contacts WHERE id = %s", (str(other),))[0][0] == 1
    assert _count(env, "recovery_cases", tid) == 2 and _count(env, "recovery_steps", tid) == 2
    assert client.post("/v1/privacy/contacts/erase", json=body).json() == {"found": False}
    audit_rows = env.sql(
        "SELECT target, detail::text FROM audit_log WHERE tenant_id = %s "
        "AND action = 'privacy.contact_erased'",
        (tid,),
    )
    assert len(audit_rows) == 1
    for pii in (PHONE, EMAIL, "Maria", BODY):
        assert pii not in json.dumps(audit_rows)


def test_opt_out_survives_erasure_with_only_the_identifier(
    client: TestClient, env: Env, db: Database
) -> None:
    tid, _ = _owner(client, env)
    contact = _populate(env, db, tid)
    env.sql(
        "INSERT INTO suppressions (tenant_id, identity, reason) VALUES (%s, %s, 'opt_out')",
        (tid, PHONE),
    )
    res = client.post("/v1/privacy/contacts/erase", json={"identifier": PHONE, "confirm": True})
    assert res.json()["block_kept"] is True
    rows = env.sql("SELECT identity, reason FROM suppressions WHERE tenant_id = %s", (tid,))
    assert rows == [(PHONE, "opt_out")]  # só o identificador e o motivo: sem nome, sem mensagem
    assert env.sql("SELECT count(*) FROM contacts WHERE id = %s", (str(contact),))[0][0] == 0
    with db.tx(tenant_id=uuid.UUID(tid)) as conn:
        assert _is_suppressed(conn, uuid.UUID(tid), PHONE, None) is True  # continua barrando


def test_also_block_adds_only_the_identifier_when_nothing_blocked_it(
    client: TestClient, env: Env, db: Database
) -> None:
    tid, _ = _owner(client, env)
    _populate(env, db, tid)
    res = client.post(
        "/v1/privacy/contacts/erase",
        json={"identifier": PHONE, "confirm": True, "also_block": True},
    ).json()
    assert res["block_kept"] is True
    assert env.sql("SELECT identity, reason FROM suppressions WHERE tenant_id = %s", (tid,)) == [
        (PHONE, "manual")
    ]
    tid2, _ = _owner(client, env, "Loja Sem Bloqueio")
    _populate(env, db, tid2)
    plain = client.post("/v1/privacy/contacts/erase", json={"identifier": PHONE, "confirm": True})
    assert plain.json()["block_kept"] is False
    assert _count(env, "suppressions", tid2) == 0


# ---------------------------------------------------------------- retenção


def test_retention_setting_validation_and_audit(client: TestClient, env: Env) -> None:
    tid, _ = _owner(client, env)
    assert client.get("/v1/privacy").json()["retention_days"] == 365
    for bad in (29, 3651, 0, -5):
        res = client.put("/v1/privacy/retention", json={"retention_days": bad})
        assert res.status_code == 400 and res.json()["error"]["code"] == "invalid_retention"
    assert client.put("/v1/privacy/retention", json={"retention_days": 90}).json() == {
        "retention_days": 90
    }
    assert client.get("/v1/privacy").json()["retention_days"] == 90
    assert (
        env.sql(
            "SELECT count(*) FROM audit_log WHERE tenant_id = %s AND action = 'privacy.retention'",
            (tid,),
        )[0][0]
        == 1
    )


def test_purge_applies_each_clients_deadline_and_keeps_open_cases(
    client: TestClient, env: Env, db: Database
) -> None:
    short, _ = _owner(client, env, "Loja Prazo Curto")
    client.put("/v1/privacy/retention", json={"retention_days": 30})
    client.post("/v1/auth/logout")
    default, _ = _owner(client, env, "Loja Prazo Padrao")
    old = datetime.now(UTC) - timedelta(days=40)
    for tid, phone in ((short, "5511911110001"), (default, "5511911110002")):
        contact = _populate(env, db, tid, phone=phone, email=None)
        env.sql(
            "UPDATE conversations SET last_message_at = %s WHERE contact_id = %s",
            (old, str(contact)),
        )
        env.sql(
            "UPDATE recovery_cases SET opened_at = %s WHERE contact_id = %s", (old, str(contact))
        )
        env.sql("UPDATE contacts SET created_at = %s WHERE id = %s", (old, str(contact)))
    totals = privacy.purge_retention(db)
    assert totals["conversas"] >= 1 and totals["casos"] >= 1
    # prazo de 30 dias: conversa, mensagens e caso encerrado saem; o caso ABERTO fica
    assert _count(env, "conversations", short) == 0 and _count(env, "messages", short) == 0
    assert env.sql("SELECT status FROM recovery_cases WHERE tenant_id = %s", (short,)) == [
        ("open",)
    ]
    assert _count(env, "contacts", short) == 1  # ainda tem caso aberto: não é contato vazio
    # prazo padrão de 365 dias: nada sai
    assert (
        _count(env, "conversations", default) == 1 and _count(env, "recovery_cases", default) == 2
    )
    again = privacy.purge_retention(db)
    assert again["conversas"] == 0  # repetir não apaga de novo nem dá erro
    audited = env.sql(
        "SELECT detail::text FROM audit_log WHERE tenant_id = %s "
        "AND action = 'privacy.retention_purge'",
        (short,),
    )
    assert len(audited) == 1 and "conversas" in audited[0][0] and PHONE not in audited[0][0]


def test_purge_removes_old_raw_events_and_orphan_contacts(
    client: TestClient, env: Env, db: Database
) -> None:
    tid, _ = _owner(client, env)
    connect_whatsapp(client, env, tid)
    conn_id = env.sql("SELECT id FROM connections WHERE tenant_id = %s", (tid,))[0][0]
    for key, days in (("velho", 400), ("novo", 1)):
        env.sql(
            "INSERT INTO webhook_events (tenant_id, connection_id, provider, event_type, "
            "dedupe_key, payload_encrypted, received_at) "
            "VALUES (%s, %s, 'whatsapp_cloud', 'x', %s, %s, %s)",
            (tid, str(conn_id), key, b"\x00", datetime.now(UTC) - timedelta(days=days)),
        )
    empty = env.sql(
        "INSERT INTO contacts (tenant_id, name, phone, created_at) VALUES (%s, 'Sem nada', "
        "'5511911112222', now() - interval '400 days') RETURNING id",
        (tid,),
    )[0][0]
    totals = privacy.purge_retention(db)
    assert totals["eventos"] >= 1 and totals["contatos"] >= 1
    assert env.sql("SELECT dedupe_key FROM webhook_events WHERE tenant_id = %s", (tid,)) == [
        ("novo",)
    ]
    assert env.sql("SELECT count(*) FROM contacts WHERE id = %s", (str(empty),))[0][0] == 0


# ---------------------------------------------------------------- consentimento


def test_consent_log_shows_who_when_and_origin(client: TestClient, env: Env) -> None:
    tid, _ = _owner(client, env)
    connect_whatsapp(client, env, tid)
    ok = client.put(
        "/v1/recovery/settings", json={"consent_declared": True, "recovery_enabled": True}
    )
    assert ok.status_code == 200
    manual = {"phone": "11999990001", "name": "Ana", "product": "Sofá", "amount_cents": 9900}
    manual["contact_authorized"] = True
    assert client.post("/v1/recovery/opportunities", json=manual).status_code == 201
    csv = "telefone;produto\n11999990002;A\n11999990003;B\n"
    imp = client.post(
        "/v1/recovery/opportunities/import", json={"csv": csv, "contact_authorized": True}
    )
    assert imp.status_code == 200, imp.text
    client.put("/v1/recovery/settings", json={"consent_declared": False})
    events = client.get("/v1/privacy").json()["consents"]
    got = [(e["action"], e["origin"], e["count"]) for e in events]
    assert got[0] == ("revoked", "painel", 1)  # mais recente primeiro
    assert ("registered", "importacao", 2) in got and ("registered", "registro_avulso", 1) in got
    assert ("declared", "painel", 1) in got
    assert all(e["by"] for e in events) and all(e["at"] for e in events)
    assert PHONE not in json.dumps(events) and "11999990001" not in json.dumps(events)
    # declarar de novo sem mudar nada não cria evento repetido
    before = len(client.get("/v1/privacy").json()["consents"])
    client.put("/v1/recovery/settings", json={"consent_declared": False})
    assert len(client.get("/v1/privacy").json()["consents"]) == before


# ---------------------------------------------------------------- exclusão da conta


def test_account_deletion_request_cancel_and_purge(
    client: TestClient, env: Env, db: Database
) -> None:
    tid, owner = _owner(client, env, "Loja Que Sai")
    _populate(env, db, tid)
    agent = unique_email("agent")
    env.invite(tid, agent, "admin")
    env.sql(
        "INSERT INTO platform_events (provider, dedupe_key, event_type, buyer_email, tenant_id, "
        "payload_encrypted) VALUES ('cakto', %s, 'purchase_approved', %s, %s, %s)",
        (uuid.uuid4().hex, owner, tid, b"\x00"),
    )
    bad = client.post("/v1/privacy/account/deletion", json={"confirm": "sim"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "confirm_required"
    first = client.post("/v1/privacy/account/deletion", json={"confirm": "EXCLUIR"}).json()
    assert first["grace_days"] == 30 and first["requested_at"] and first["due_at"]
    due = datetime.fromisoformat(first["due_at"]) - datetime.fromisoformat(first["requested_at"])
    assert due == timedelta(days=30)
    again = client.post("/v1/privacy/account/deletion", json={"confirm": "EXCLUIR"}).json()
    assert again["due_at"] == first["due_at"]  # repetir o pedido não estica o prazo
    status = env.sql("SELECT status FROM tenant_plans WHERE tenant_id = %s", (tid,))[0][0]
    assert status == "suspended"  # o serviço pausa, o dono ainda entra para cancelar
    assert client.get("/v1/privacy").json()["deletion"]["due_at"] == first["due_at"]
    # cancelar devolve tudo como estava
    cancel = client.delete("/v1/privacy/account/deletion").json()
    assert cancel["requested_at"] is None
    assert env.sql("SELECT status FROM tenant_plans WHERE tenant_id = %s", (tid,))[0][0] == "active"
    assert client.delete("/v1/privacy/account/deletion").json()["requested_at"] is None
    # pede de novo e deixa vencer
    client.post("/v1/privacy/account/deletion", json={"confirm": "EXCLUIR"})
    assert privacy.purge_due_tenants(db) == 0  # carência ainda não acabou
    assert _count(env, "contacts", tid) == 1
    future = datetime.now(UTC) + timedelta(days=31)
    assert privacy.purge_due_tenants(db, now=future) == 1
    for table in (
        "tenants",
        "contacts",
        "conversations",
        "messages",
        "recovery_cases",
        "audit_log",
        "tenant_settings",
        "tenant_plans",
        "memberships",
        "pending_invites",
    ):
        col = "id" if table == "tenants" else "tenant_id"
        assert env.sql(f"SELECT count(*) FROM {table} WHERE {col} = %s", (tid,))[0][0] == 0, table
    assert env.sql("SELECT count(*) FROM users WHERE lower(email) = lower(%s)", (owner,))[0][0] == 0
    assert env.sql("SELECT count(*) FROM platform_events WHERE tenant_id = %s", (tid,))[0][0] == 0
    log_row = env.sql("SELECT users_removed FROM deletion_log WHERE tenant_id = %s", (tid,))
    assert len(log_row) == 1 and log_row[0][0] >= 1
    assert privacy.purge_due_tenants(db, now=future) == 0  # repetir não faz nada


def test_deletion_keeps_users_that_belong_to_another_client_and_other_clients_untouched(
    client: TestClient, env: Env, db: Database
) -> None:
    survivor, owner = _owner(client, env, "Loja Que Fica")
    _populate(env, db, survivor)
    leaving = env.tenant("Loja Que Sai Junto", unique_email("l"))
    shared = unique_email("shared")
    env.invite(survivor, shared, "admin")
    env.invite(leaving, shared, "admin")
    client.post("/v1/auth/logout")
    assert login(client, shared).status_code == 200
    client.post("/v1/auth/logout")
    assert login(client, owner).status_code == 200
    env.sql(
        "UPDATE tenant_settings SET deletion_due_at = now() - interval '1 day', "
        "deletion_requested_at = now() - interval '31 days'"
        " WHERE tenant_id = %s",
        (leaving,),
    ) if _count(env, "tenant_settings", leaving) else env.sql(
        "INSERT INTO tenant_settings (tenant_id, deletion_due_at, deletion_requested_at) "
        "VALUES (%s, now() - interval '1 day', now() - interval '31 days')",
        (leaving,),
    )
    assert privacy.purge_due_tenants(db) >= 1
    assert env.sql("SELECT count(*) FROM tenants WHERE id = %s", (leaving,))[0][0] == 0
    assert env.sql("SELECT count(*) FROM tenants WHERE id = %s", (survivor,))[0][0] == 1
    assert _count(env, "contacts", survivor) == 1 and _count(env, "messages", survivor) == 2
    assert env.sql("SELECT count(*) FROM users WHERE lower(email) = lower(%s)", (owner,))[0][0] == 1


def test_only_the_owner_can_request_or_cancel_deletion(client: TestClient, env: Env) -> None:
    tid, _ = _owner(client, env)
    admin = unique_email("adm")
    env.invite(tid, admin, "admin")
    client.post("/v1/auth/logout")
    assert login(client, admin).status_code == 200
    res = client.post("/v1/privacy/account/deletion", json={"confirm": "EXCLUIR"})
    assert res.status_code == 403
    assert client.delete("/v1/privacy/account/deletion").status_code == 403
    assert client.get("/v1/privacy").status_code == 200  # administrador vê e usa o resto
    assert env.sql("SELECT status FROM tenant_plans WHERE tenant_id = %s", (tid,))[0][0] == "active"


# ---------------------------------------------------------------- nada pessoal em log


def test_no_log_line_carries_phone_email_name_or_conversation_text(
    wa: tuple[TestClient, str, str, str],
    env: Env,
    db: Database,
    caplog: pytest.LogCaptureFixture,
) -> None:
    c, _, pid, _ = wa
    caplog.set_level(logging.DEBUG)
    secret_text = "texto-confidencial-do-cliente-4242"
    env.sql("UPDATE messages SET status = 'failed' WHERE direction = 'out' AND status = 'queued'")
    res = post(c, pid, payload([text("wamid.log1", secret_text)]))
    assert res.status_code == 200
    run_ai_replies(db, SimulatedAiModel())
    from tests.test_whatsapp_inbound import PHONE as WA_PHONE

    c.post("/v1/privacy/contacts/export", json={"identifier": WA_PHONE})
    c.post("/v1/privacy/contacts/erase", json={"identifier": WA_PHONE, "confirm": True})
    privacy.purge_retention(db)
    c.post(
        "/v1/privacy/contacts/erase",
        json={"identifier": "email.inexistente@example.test", "confirm": True},
    )
    everything = "\n".join(
        f"{r.getMessage()} {getattr(r, 'ctx', '')} {r.exc_text or ''}" for r in caplog.records
    )
    assert "request" in everything  # o log foi mesmo capturado: a ausência abaixo vale
    for pii in (secret_text, WA_PHONE, "Carla", "email.inexistente"):
        assert pii not in everything, f"vazou em log: {pii}"
